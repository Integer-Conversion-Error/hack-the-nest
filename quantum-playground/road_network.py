"""Bounded, cached map providers and immutable road-network snapshots.

Only operator configuration selects upstream URLs. Solver inputs and saved
results use frozen matrices; network calls are confined to preparation/search
and geometry enrichment by the dashboard host.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_NODES = 72
MAX_ARTIFACT_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 10_000_000
SNAP_RADIUS_M = 100
REQUEST_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 25
MAX_MEMORY_ENTRIES = 128
MAX_MEMORY_BYTES = 16_000_000
MAX_DISK_ENTRIES = 256
MAX_DISK_BYTES = 64_000_000
USER_AGENT = "QuantumLoop/1.0 (+https://github.com/Integer-Conversion-Error/hack-the-nest)"

_CACHE = OrderedDict()
_REVOKED_TABLES = OrderedDict()
_CACHE_LOCK = threading.RLock()
_FETCH_LOCKS = {"routing": threading.Lock(), "geocoding": threading.Lock()}
_LAST_REQUEST = {"routing": float("-inf"), "geocoding": float("-inf")}
_FIELDS = {"schema_version", "source_digest", "provider", "base_url", "profile", "created_at",
           "data_version", "nodes", "distances_m", "durations_s", "digest"}


class MapServiceError(ValueError):
    def __init__(self, message, status=503, *, provider_code=None):
        super().__init__(message)
        self.status = status
        self.provider_code = provider_code


def _require(condition, message, status=422):
    if not condition:
        raise MapServiceError(message, status)


def _number(value, label, minimum=0):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        valid = False
    _require(valid, label + " must be a finite number.")
    return value


def _position(value, label):
    _require(isinstance(value, list) and len(value) == 2, label + " must be [longitude, latitude].")
    longitude = _number(value[0], label + " longitude", -180)
    latitude = _number(value[1], label + " latitude", -90)
    _require(longitude <= 180 and latitude <= 90, label + " is outside geographic coordinates.")
    return [longitude, latitude]


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise MapServiceError("Map data contains invalid JSON.", 422) from None


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite JSON value")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise MapServiceError("The map provider returned invalid JSON. Try again or change the configured provider.") from None


def _base_url(value, label):
    _require(isinstance(value, str) and value.strip(), label + " URL is not configured.", 503)
    try:
        parts = urlsplit(value.strip())
        valid = (parts.scheme in ("http", "https") and parts.hostname and parts.port != 0
                 and not parts.username and not parts.password and not parts.query and not parts.fragment)
    except ValueError:
        valid = False
    _require(valid, label + " must be an HTTP(S) base URL without credentials, query, or fragment.", 503)
    _require(not any(ord(char) < 33 for char in value.strip()), label + " URL contains invalid characters.", 503)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _osrm_url():
    return _base_url(os.getenv("QUANTUM_OSRM_URL", "https://router.project-osrm.org"), "Routing provider")


def maps_config():
    tile_url = os.getenv("QUANTUM_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png").strip()
    try:
        tile_parts = urlsplit(tile_url)
        _base_url(urlunsplit((tile_parts.scheme, tile_parts.netloc, tile_parts.path, "", "")), "Tile provider")
        _require(not tile_parts.fragment and not any(ord(char) < 33 for char in tile_url), "Invalid tile provider URL.", 503)
    except ValueError:
        raise MapServiceError("Tile provider must be an HTTP(S) URL template.") from None
    _require(all(token in tile_url for token in ("{z}", "{x}", "{y}")),
             "Tile URL must include {z}, {x}, and {y} placeholders.", 503)
    return {
        "tile_url": tile_url,
        "tile_attribution": os.getenv("QUANTUM_TILE_ATTRIBUTION",
                                      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'),
        "geocoder_attribution": "Search: Photon / OpenStreetMap contributors",
        "routing_attribution": 'Routes: <a href="https://project-osrm.org">OSRM</a> · '
                               '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _request, response, _code, _message, _headers, _new_url):
        response.close()
        raise MapServiceError("The map provider redirected the request. Configure its direct HTTP(S) endpoint.")


def _request_json(base, path, parameters, service):
    request = Request(base + path + "?" + urlencode(parameters),
                      headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    # This lock limits the whole application, rather than each tab or solver.
    with _FETCH_LOCKS[service]:
        delay = _LAST_REQUEST[service] + REQUEST_INTERVAL_SECONDS - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST[service] = time.monotonic()
        try:
            with build_opener(_NoRedirect()).open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit():
                    _require(int(content_length) <= MAX_RESPONSE_BYTES, "The map provider response exceeds 10 MB.", 503)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                _require(len(raw) <= MAX_RESPONSE_BYTES, "The map provider response exceeds 10 MB.", 503)
        except HTTPError as exc:
            code = None
            try:
                detail = _json(exc.read(65_536))
                code = detail.get("code") if isinstance(detail, dict) else None
            except (MapServiceError, OSError):
                pass
            finally:
                exc.close()
            if code == "NoSegment":
                message = "A location has no drivable road within 100 m. Move its marker closer to a road and try again."
            elif code in ("NoRoute", "NoTable"):
                message = "The selected locations have no connected driving route. Move a location or revise the assignments."
            elif code == "TooBig":
                message = "The routing provider cannot handle this many locations. Reduce the scenario or configure a larger routing service."
            elif exc.code == 429:
                message = "The map provider is rate limiting requests. Wait and try again, or configure your own provider."
            else:
                message = f"The map provider returned HTTP {exc.code}. Check the locations or try again later."
            raise MapServiceError(message, provider_code=code) from None
        except (URLError, TimeoutError, OSError):
            raise MapServiceError("Could not reach the map provider. Your draft is preserved; try again or configure another provider.") from None
    result = _json(raw)
    _require(isinstance(result, dict), "The map provider returned an invalid response.", 503)
    return result


def _cache_root():
    configured = os.getenv("QUANTUM_MAP_CACHE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".cache" / "quantumloop" / "maps"


def _cache_key(kind, *parts):
    return kind + "-" + hashlib.sha256(_canonical(parts)).hexdigest()


def _invalidate_table(routing):
    """Refresh future planning after provider changes; preserve approved inputs."""
    key = _cache_key("table", routing["base_url"], routing["source_digest"])
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _REVOKED_TABLES[key] = routing["digest"]
        _REVOKED_TABLES.move_to_end(key)
        while len(_REVOKED_TABLES) > MAX_MEMORY_ENTRIES:
            _REVOKED_TABLES.popitem(last=False)
    try:
        (_cache_root() / (key + ".json")).unlink(missing_ok=True)
    except OSError:
        # The revocation above also protects a read-only disk cache.
        pass


def _remember(key, raw):
    with _CACHE_LOCK:
        _CACHE[key] = raw
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_MEMORY_ENTRIES or sum(map(len, _CACHE.values())) > MAX_MEMORY_BYTES:
            _CACHE.popitem(last=False)


def _cached(key):
    with _CACHE_LOCK:
        raw = _CACHE.get(key)
        if raw is not None:
            _CACHE.move_to_end(key)
    if raw is None:
        try:
            path = _cache_root() / (key + ".json")
            if path.is_symlink() or path.stat().st_size > MAX_RESPONSE_BYTES:
                return None
            raw = path.read_bytes()
        except OSError:
            return None
    try:
        envelope = _json(raw)
        _require(isinstance(envelope, dict) and set(envelope) == {"version", "key", "checksum", "value"}
                 and envelope["version"] == 1 and envelope["key"] == key
                 and envelope["checksum"] == hashlib.sha256(_canonical(envelope["value"])).hexdigest(),
                 "Invalid map cache entry.")
        value = envelope["value"]
    except MapServiceError:
        return None
    _remember(key, raw)
    return value


def _store(key, value):
    raw = _canonical({"version": 1, "key": key,
                      "checksum": hashlib.sha256(_canonical(value)).hexdigest(), "value": value})
    if len(raw) > MAX_RESPONSE_BYTES:
        return
    _remember(key, raw)
    staging = None
    try:
        directory = _cache_root()
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, prefix=".maps-", delete=False) as stream:
            staging = Path(stream.name)
            stream.write(raw)
        staging.replace(directory / (key + ".json"))
        entries = sorted((path for path in directory.glob("*.json") if not path.is_symlink()),
                         key=lambda path: path.stat().st_mtime, reverse=True)
        total = 0
        for index, path in enumerate(entries):
            total += path.stat().st_size
            if index >= MAX_DISK_ENTRIES or total > MAX_DISK_BYTES:
                path.unlink(missing_ok=True)
    except OSError:
        # A read-only/full cache must not disable otherwise valid routing.
        pass
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass


def _source(source):
    from scenario import normalize_scenario, scenario_digest

    try:
        normalized = normalize_scenario(source)
    except ValueError as exc:
        raise MapServiceError("Invalid geographic scenario: " + str(exc), 400) from None
    _require(normalized.get("map") == {"kind": "geographic", "profile": "driving"},
             "Road routing requires a geographic driving scenario.", 400)
    nodes = []
    for name in ("depots", "chargers", "deliveries"):
        for item in sorted(normalized[name], key=lambda item: int(item["id"][1:])):
            nodes.append({"id": item["id"], "position": list(item["position"])})
    _require(1 <= len(nodes) <= MAX_NODES, "Road routing supports at most 72 locations.", 400)
    return normalized, nodes, scenario_digest(normalized)


def routing_digest(artifact):
    _require(isinstance(artifact, dict), "The routing snapshot must be an object.")
    return hashlib.sha256(_canonical({key: value for key, value in artifact.items() if key != "digest"})).hexdigest()


def _meters(first, second):
    lon_a, lat_a, lon_b, lat_b = map(math.radians, (*first, *second))
    value = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return 12_742_017.6 * math.asin(min(1, math.sqrt(value)))


def _validate_artifact(artifact):
    _require(isinstance(artifact, dict) and set(artifact) == _FIELDS, "The routing snapshot has invalid fields.")
    _require(type(artifact["schema_version"]) is int and artifact["schema_version"] == 1
             and artifact["provider"] == "osrm" and artifact["profile"] == "driving", "Unsupported routing snapshot.")
    _base_url(artifact["base_url"], "Routing snapshot provider")
    _require(isinstance(artifact["source_digest"], str) and re.fullmatch(r"[0-9a-f]{64}", artifact["source_digest"]),
             "The routing snapshot has no valid source identity.")
    try:
        stamp = artifact["created_at"]
        _require(isinstance(stamp, str) and len(stamp) <= 64, "Invalid routing snapshot timestamp.")
        _require(datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is not None,
                 "The routing snapshot timestamp needs a time zone.")
    except ValueError:
        raise MapServiceError("Invalid routing snapshot timestamp.", 422) from None
    version = artifact["data_version"]
    _require(version is None or isinstance(version, str) and len(version) <= 160
             and not any(ord(char) < 32 for char in version), "Invalid routing data version.")
    nodes = artifact["nodes"]
    _require(isinstance(nodes, list) and 1 <= len(nodes) <= MAX_NODES, "The routing snapshot has invalid locations.")
    seen = set()
    for node in nodes:
        _require(isinstance(node, dict) and set(node) == {"id", "position", "snapped_position", "snap_distance_m"},
                 "Invalid routing location fields.")
        key = node["id"]
        _require(isinstance(key, str) and re.fullmatch(r"[PCD][1-9][0-9]{0,5}", key) and key not in seen,
                 "Invalid or duplicate routing location ID.")
        seen.add(key)
        original = _position(node["position"], key)
        snapped = _position(node["snapped_position"], key + " road position")
        distance = _number(node["snap_distance_m"], key + " snap distance")
        _require(distance <= SNAP_RADIUS_M + 1e-6 and _meters(original, snapped) <= SNAP_RADIUS_M + 1,
                 key + " has no drivable road within 100 m. Move the marker closer to a road.")
        _require(abs(_meters(original, snapped) - distance) <= 2,
                 key + " has an inconsistent road snapping distance.")
    for field in ("distances_m", "durations_s"):
        matrix = artifact[field]
        _require(isinstance(matrix, list) and len(matrix) == len(nodes)
                 and all(isinstance(row, list) and len(row) == len(nodes) for row in matrix),
                 "The routing snapshot matrix has invalid dimensions.")
    for i, row in enumerate(artifact["distances_m"]):
        for j, distance in enumerate(row):
            duration = artifact["durations_s"][i][j]
            _require((distance is None) == (duration is None), "An unreachable route must have paired null distance and duration.")
            if distance is not None:
                _number(distance, "Road distance")
                _number(duration, "Road travel time")
            if i == j:
                _require(distance == 0 and duration == 0, "A location's route to itself must be zero.")
    _require(isinstance(artifact["digest"], str) and re.fullmatch(r"[0-9a-f]{64}", artifact["digest"])
             and artifact["digest"] == routing_digest(artifact), "The routing snapshot changed. Prepare routes again.")
    _require(len(_canonical(artifact)) <= MAX_ARTIFACT_BYTES, "The routing snapshot exceeds 1 MB.")
    return copy.deepcopy(artifact)


def validate_routing(source, artifact, *, expected_source_digest=None):
    """Validate frozen road data without network access.

    The optional digest is supplied only by the host for approved numeric
    overlays. Geometry and every node still have to match the effective source.
    """
    _normalized, expected_nodes, digest = _source(source)
    validated = _validate_artifact(artifact)
    expected = digest if expected_source_digest is None else expected_source_digest
    _require(validated["source_digest"] == expected, "The routing snapshot belongs to a different scenario.")
    _require([{key: node[key] for key in ("id", "position")} for node in validated["nodes"]] == expected_nodes,
             "The routing snapshot's locations changed. Prepare routes again.")
    return validated


def _waypoint(waypoint, original, label):
    _require(isinstance(waypoint, dict), "The routing provider omitted a road location.", 503)
    snapped = _position(waypoint.get("location"), label + " road position")
    distance = _number(waypoint.get("distance", _meters(original, snapped)), label + " snap distance")
    _require(distance <= SNAP_RADIUS_M + 1e-6 and _meters(original, snapped) <= SNAP_RADIUS_M + 1,
             label + " has no drivable road within 100 m. Move its marker closer to a road.", 503)
    return snapped, distance


def prepare_routing(source):
    _normalized, nodes, digest = _source(source)
    base = _osrm_url()
    key = _cache_key("table", base, digest)
    cached = _cached(key)
    with _CACHE_LOCK:
        if isinstance(cached, dict) and cached.get("digest") == _REVOKED_TABLES.get(key):
            cached = None
    if cached is not None:
        try:
            return validate_routing(source, cached)
        except MapServiceError:
            pass
    coordinates = ";".join(f"{node['position'][0]:.6f},{node['position'][1]:.6f}" for node in nodes)
    result = _request_json(base, "/table/v1/driving/" + coordinates,
                           {"annotations": "distance,duration", "radiuses": ";".join(["100"] * len(nodes)),
                            "generate_hints": "false"}, "routing")
    _require(result.get("code") == "Ok", "Could not prepare road routes. Move locations onto drivable roads and try again.", 503)
    _require(not result.get("fallback_speed_cells"), "Estimated straight-line routing is not supported.", 503)
    sources, destinations = result.get("sources"), result.get("destinations")
    _require(isinstance(sources, list) and isinstance(destinations, list)
             and len(sources) == len(nodes) and len(destinations) == len(nodes),
             "The routing provider returned incomplete road locations.", 503)
    snapped_nodes = []
    for node, start, end in zip(nodes, sources, destinations):
        snapped, distance = _waypoint(start, node["position"], node["id"])
        destination, _ = _waypoint(end, node["position"], node["id"])
        _require(_meters(snapped, destination) <= 1, "Routing endpoints disagree. Move the marker and prepare routes again.", 503)
        snapped_nodes.append({**node, "snapped_position": snapped, "snap_distance_m": distance})
    artifact = {"schema_version": 1, "source_digest": digest, "provider": "osrm", "base_url": base,
                "profile": "driving", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data_version": result.get("data_version"), "nodes": snapped_nodes,
                "distances_m": result.get("distances"), "durations_s": result.get("durations")}
    artifact["digest"] = routing_digest(artifact)
    artifact = validate_routing(source, artifact)
    _store(key, artifact)
    return artifact


def _geometry(value, routing, first, second):
    _require(isinstance(value, dict) and set(value) == {"routing_digest", "from_id", "to_id", "distance_m", "duration_s", "coordinates"},
             "Invalid saved road geometry.")
    _require(value["routing_digest"] == routing["digest"] and value["from_id"] == first["id"]
             and value["to_id"] == second["id"], "Road geometry belongs to another routing snapshot.")
    index = {node["id"]: i for i, node in enumerate(routing["nodes"])}
    i, j = index[first["id"]], index[second["id"]]
    for field, matrix, tolerance in (("distance_m", "distances_m", 1), ("duration_s", "durations_s", 1)):
        actual = _number(value[field], "Road geometry " + field)
        expected = routing[matrix][i][j]
        _require(expected is not None and abs(actual - expected) <= tolerance,
                 "Road geometry no longer matches the saved matrix. Prepare routes again.")
    coordinates = value["coordinates"]
    _require(isinstance(coordinates, list) and 2 <= len(coordinates) <= 20_000, "Invalid road route geometry.")
    for coordinate in coordinates:
        _position(coordinate, "Road geometry")
    _require(_meters(coordinates[0], first["snapped_position"]) <= 1
             and _meters(coordinates[-1], second["snapped_position"]) <= 1,
             "Road geometry endpoints do not match the saved road locations.")
    return copy.deepcopy(coordinates)


def road_geometry(routing, from_id, to_id):
    routing = _validate_artifact(routing)
    nodes = {node["id"]: node for node in routing["nodes"]}
    _require(isinstance(from_id, str) and from_id in nodes and isinstance(to_id, str) and to_id in nodes,
             "Unknown road geometry location.")
    first, second = nodes[from_id], nodes[to_id]
    index = {node["id"]: i for i, node in enumerate(routing["nodes"])}
    i, j = index[from_id], index[to_id]
    distance, duration = routing["distances_m"][i][j], routing["durations_s"][i][j]
    _require(distance is not None and duration is not None, f"No drivable route from {from_id} to {to_id}. Move a location or revise the assignments.")
    if from_id == to_id or distance == duration == 0 and first["snapped_position"] == second["snapped_position"]:
        return [list(first["snapped_position"]), list(second["snapped_position"])]
    key = _cache_key("geometry", routing["digest"], from_id, to_id)
    cached = _cached(key)
    if cached is not None:
        try:
            return _geometry(cached, routing, first, second)
        except MapServiceError:
            pass
    base = _osrm_url()
    _require(base == routing["base_url"], "The routing provider changed. Prepare routes again before fetching new geometry.", 503)
    coordinates = ";".join(f"{node['position'][0]:.6f},{node['position'][1]:.6f}" for node in (first, second))
    try:
        response = _request_json(base, "/route/v1/driving/" + coordinates,
                                 {"geometries": "geojson", "overview": "full", "steps": "false",
                                  "continue_straight": "false", "radiuses": "100;100", "generate_hints": "false"}, "routing")
    except MapServiceError as exc:
        if exc.provider_code in ("NoSegment", "NoRoute", "NoTable"):
            _invalidate_table(routing)
        raise
    try:
        _require(response.get("code") == "Ok" and isinstance(response.get("routes"), list) and response["routes"],
                 f"Could not fetch the road route from {from_id} to {to_id}. Try again or prepare routes again.", 503)
        waypoints = response.get("waypoints")
        _require(isinstance(waypoints, list) and len(waypoints) == 2, "The routing provider omitted route endpoints.", 503)
        for node, waypoint in zip((first, second), waypoints):
            snapped, _ = _waypoint(waypoint, node["position"], node["id"])
            _require(_meters(snapped, node["snapped_position"]) <= 1,
                     "The routing provider changed a road endpoint. Prepare routes again.", 503)
        route = response["routes"][0]
        _require(isinstance(route, dict) and isinstance(route.get("geometry"), dict)
                 and route["geometry"].get("type") == "LineString", "The routing provider returned no road line.", 503)
        value = {"routing_digest": routing["digest"], "from_id": from_id, "to_id": to_id,
                 "distance_m": route.get("distance"), "duration_s": route.get("duration"),
                 "coordinates": route["geometry"].get("coordinates")}
        geometry = _geometry(value, routing, first, second)
    except MapServiceError:
        _invalidate_table(routing)
        raise
    _store(key, value)
    return geometry


def search_places(query, lon=None, lat=None):
    _require(isinstance(query, str) and 1 <= len(query.strip()) <= 200
             and not any(ord(char) < 32 for char in query), "Search for a place using 1–200 characters.", 400)
    parameters = {"q": query.strip(), "limit": 5, "lang": "en"}
    _require((lon is None) == (lat is None), "Search bias requires both longitude and latitude.", 400)
    if lon is not None:
        position = _position([lon, lat], "Search focus")
        parameters.update(lon=position[0], lat=position[1])
    base = _base_url(os.getenv("QUANTUM_GEOCODER_URL", "https://photon.komoot.io"), "Geocoder")
    key = _cache_key("search", base, parameters)
    cached = _cached(key)
    if isinstance(cached, list) and len(cached) <= 5:
        try:
            for item in cached:
                _require(isinstance(item, dict) and set(item) == {"label", "position"}
                         and isinstance(item["label"], str) and len(item["label"]) <= 500, "Invalid cached place.")
                _position(item["position"], "Place")
            return copy.deepcopy(cached)
        except MapServiceError:
            pass
    result = _request_json(base, "/api/", parameters, "geocoding")
    features = result.get("features")
    _require(isinstance(features, list) and len(features) <= 100, "The geocoder returned invalid places.", 503)
    places = []
    for feature in features[:5]:
        _require(isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
                 and feature["geometry"].get("type") == "Point" and isinstance(feature.get("properties"), dict),
                 "The geocoder returned an invalid place.", 503)
        position = _position(feature["geometry"].get("coordinates"), "Place")
        properties = feature["properties"]
        address = " ".join(str(properties[key]) for key in ("housenumber", "street") if properties.get(key))
        parts = [properties.get("name"), address, properties.get("city"), properties.get("state"), properties.get("country")]
        labels = []
        for part in parts:
            if isinstance(part, str) and part.strip() and part.strip() not in labels:
                labels.append(" ".join(part.split())[:160])
        label = ", ".join(labels)[:500] or f"{position[1]:.6f}, {position[0]:.6f}"
        places.append({"label": label, "position": [round(number, 6) for number in position]})
    _store(key, places)
    return places
