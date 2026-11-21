"""Local provider-contract checks; no requests reach public map services."""

import copy
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch

import road_network as roads
from geographic import geo_seed
from scenario import normalize_scenario, scenario_digest


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        state = self.server.state
        state.calls.append({"path": self.path, "agent": self.headers.get("User-Agent"), "time": time.monotonic()})
        if state.failure:
            status, raw, headers = state.failure
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
            return
        request = urlsplit(self.path)
        if "/table/v1/driving/" in request.path:
            coordinates = [[float(value) for value in part.split(",")]
                           for part in unquote(request.path.rsplit("/", 1)[-1]).split(";")]
            state.coordinates = coordinates
            count = len(coordinates)
            state.distances = [[0 if i == j else 100 * (i + 1) + 7 * (j + 1) for j in range(count)] for i in range(count)]
            state.durations = [[value / 10 for value in row] for row in state.distances]
            if state.unreachable:
                state.distances[0][1] = state.durations[0][1] = None
            result = {"code": "Ok", "data_version": "2026-11-20T00:00:00Z",
                      "sources": [{"location": point, "distance": 0} for point in coordinates],
                      "destinations": [{"location": point, "distance": 0} for point in coordinates],
                      "distances": state.distances, "durations": state.durations}
            if state.table_mutation:
                state.table_mutation(result)
        elif "/route/v1/driving/" in request.path:
            points = [[float(value) for value in part.split(",")]
                      for part in unquote(request.path.rsplit("/", 1)[-1]).split(";")]
            first, second = [state.coordinates.index(point) for point in points]
            result = {"code": "Ok", "waypoints": [{"location": point, "distance": 0} for point in points],
                      "routes": [{"distance": state.distances[first][second], "duration": state.durations[first][second],
                                  "geometry": {"type": "LineString", "coordinates": points}}]}
            if state.route_mutation:
                state.route_mutation(result)
        elif request.path.endswith("/api/"):
            query = parse_qs(request.query)["q"][0]
            result = {"features": [{"type": "Feature", "properties": {"name": query, "city": "Toronto", "country": "Canada"},
                                    "geometry": {"type": "Point", "coordinates": [-79.3832014, 43.6531997]}}]}
        else:
            self.send_error(404)
            return
        raw = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class RoadNetworkTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state = type("ProviderState", (), {})()
        self.state.calls = []
        self.state.failure = self.state.table_mutation = self.state.route_mutation = None
        self.state.unreachable = False
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        self.http.state = self.state
        thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        environment = patch.dict(os.environ, {"QUANTUM_MAP_CACHE_DIR": str(self.directory),
                                              "QUANTUM_OSRM_URL": self.base, "QUANTUM_GEOCODER_URL": self.base})
        environment.start()
        self.addCleanup(environment.stop)
        interval = patch.object(roads, "REQUEST_INTERVAL_SECONDS", 0)
        interval.start()
        self.addCleanup(interval.stop)
        with roads._CACHE_LOCK:
            roads._CACHE.clear()
            roads._REVOKED_TABLES.clear()
        roads._LAST_REQUEST.update(routing=float("-inf"), geocoding=float("-inf"))
        self.source = geo_seed()

    def test_facility_pin_order_driving_direction_and_cache(self):
        pins = [[-79.38, 43.65], [-79.39, 43.65], [-79.40, 43.65]]
        self.state.unreachable = True
        result = roads.travel_times(pins)
        self.assertEqual(pins, result["positions"])
        self.assertEqual(pins, result["snapped_positions"])
        self.assertIsNone(result["durations_s"][0][1])
        self.assertIsNone(result["distances_m"][0][1])
        self.assertEqual(result["durations_s"][1][0], 20.7)
        self.assertEqual(result["distances_m"][1][0], 207)
        self.assertRegex(result["routing_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(roads.travel_times(pins), result)
        self.assertEqual(len(self.state.calls), 1)
        for invalid in ([], pins * 9, [[0, 86], pins[1]], [False, pins[1]]):
            with self.assertRaises(roads.MapServiceError):
                roads.travel_times(invalid)
        self.assertEqual(len(self.state.calls), 1)

    def test_facility_path_is_bound_to_order_direction_and_snapshot(self):
        pins = [[-79.38, 43.65], [-79.39, 43.65], [-79.40, 43.65]]
        table = roads.travel_times(pins)
        bend = [-79.385, 43.653]
        self.state.route_mutation = lambda reply: reply["routes"][0]["geometry"]["coordinates"].insert(1, bend)
        for first, second in ((0, 1), (1, 0)):
            with self.subTest(first=first, second=second):
                path = roads.road_path(pins, first, second, table["routing_digest"])
                self.assertEqual(path, {"from_index": first, "to_index": second, "routing_digest": table["routing_digest"],
                                        "coordinates": [pins[first], bend, pins[second]], "distance_m": table["distances_m"][first][second],
                                        "duration_s": table["durations_s"][first][second]})
                self.assertEqual(roads.road_path(pins, first, second), path)
        self.assertNotEqual(table["distances_m"][0][1], table["distances_m"][1][0])
        self.assertNotEqual(table["durations_s"][0][1], table["durations_s"][1][0])
        self.assertEqual(len(self.state.calls), 3, "one road matrix plus one geometry request per direction")
        with self.assertRaises(roads.MapServiceError) as changed:
            roads.road_path(pins, 0, 1, "f" * 64)
        self.assertEqual(changed.exception.status, 409)
        self.assertEqual(len(self.state.calls), 3)

    def test_invalid_or_unreachable_facility_legs_never_request_a_road_line(self):
        pins = [[-79.38, 43.65], [-79.39, 43.65]]
        for first, second, digest in ((-1, 0, None), (0, 2, None), (True, 1, None),
                                      (0, 1.5, None), (0, "1", None), (0, 1, "invalid")):
            with self.subTest(first=first, second=second, digest=digest), self.assertRaises(roads.MapServiceError) as error:
                roads.road_path(pins, first, second, digest)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(len(self.state.calls), 0)
        self.state.unreachable = True
        with self.assertRaisesRegex(roads.MapServiceError, "No drivable route"):
            roads.road_path(pins, 0, 1)
        self.assertEqual(len(self.state.calls), 1)

    def test_one_directed_table_binds_canonical_nodes_and_source(self):
        self.source["depots"].reverse()
        artifact = roads.prepare_routing(self.source)
        self.assertEqual([item["id"] for item in artifact["nodes"]], ["P1", "P2", "C1", "C2", "D1", "D2", "D3"])
        self.assertEqual(artifact["source_digest"], scenario_digest(self.source))
        self.assertEqual(artifact["digest"], roads.routing_digest(artifact))
        self.assertNotEqual(artifact["distances_m"][0][1], artifact["distances_m"][1][0])
        self.assertEqual(len(self.state.calls), 1)
        request = self.state.calls[0]
        self.assertTrue(request["agent"].startswith("QuantumLoop/"))
        query = parse_qs(urlsplit(request["path"]).query)
        self.assertEqual(query["annotations"], ["distance,duration"])
        self.assertEqual(query["radiuses"], [";".join(["100"] * 7)])
        self.assertNotIn("fallback_speed", query)
        artifact["distances_m"][0][1] = 999
        restored = roads.prepare_routing(self.source)
        self.assertNotEqual(restored["distances_m"][0][1], 999)
        self.assertEqual(len(self.state.calls), 1, "caller mutations cannot corrupt the immutable cached snapshot")

    def test_maximum_72_locations_uses_one_request_and_retains_unreachable_pairs(self):
        source = self.source
        source["depots"] = [{"id": f"P{i}", "position": [-79.38 + i / 10000, 43.65]} for i in range(1, 7)]
        source["chargers"] = [{"id": f"C{i}", "position": [-79.39 + i / 10000, 43.65], "power_kw": 10, "slots": []} for i in range(1, 7)]
        source["vehicles"] = [{"id": f"V{i}", "depot_id": f"P{i}", "initial_charge_pct": 100} for i in range(1, 7)]
        source["deliveries"] = [{"id": f"D{i}", "position": [-79.4 + i / 10000, 43.65],
                                 "vehicle_id": f"V{(i - 1) // 10 + 1}", "deadline_min": 90, "service_min": 1} for i in range(1, 61)]
        self.state.unreachable = True
        artifact = roads.prepare_routing(source)
        self.assertEqual(len(artifact["nodes"]), 72)
        self.assertEqual(len(artifact["distances_m"]), 72)
        self.assertIsNone(artifact["distances_m"][0][1])
        self.assertIsNone(artifact["durations_s"][0][1])
        self.assertEqual(len(self.state.calls), 1)
        with self.assertRaisesRegex(roads.MapServiceError, "No drivable route"):
            roads.road_geometry(artifact, "P1", "P2")
        self.assertEqual(len(self.state.calls), 1)

    def test_offline_validation_checks_source_order_all_matrices_and_digest(self):
        artifact = roads.prepare_routing(self.source)
        with patch.object(roads, "_request_json", side_effect=AssertionError("validation must be offline")):
            self.assertEqual(roads.validate_routing(self.source, artifact), artifact)
            for mutate in (
                lambda a: a.update(source_digest="0" * 64),
                lambda a: a["nodes"].reverse(),
                lambda a: a["distances_m"][0].pop(),
                lambda a: a["durations_s"][0].__setitem__(1, None),
                lambda a: a["distances_m"][0].__setitem__(1, -1),
                lambda a: a["distances_m"][0].__setitem__(1, True),
                lambda a: a["nodes"][0].update(snap_distance_m=101),
                lambda a: a.update(digest="f" * 64),
                lambda a: a.update(unexpected=True),
            ):
                invalid = copy.deepcopy(artifact)
                mutate(invalid)
                if set(invalid) == set(artifact) and invalid["digest"] == artifact["digest"]:
                    invalid["digest"] = roads.routing_digest(invalid)
                with self.subTest(mutate=mutate):
                    with self.assertRaises(roads.MapServiceError):
                        roads.validate_routing(self.source, invalid)
        changed = copy.deepcopy(self.source)
        changed["costs"]["driving_per_km"] = 0.5
        with self.assertRaisesRegex(roads.MapServiceError, "different scenario"):
            roads.validate_routing(changed, artifact)
        self.assertEqual(roads.validate_routing(changed, artifact, expected_source_digest=artifact["source_digest"]), artifact)
        changed["depots"][0]["position"][0] += 0.001
        with self.assertRaisesRegex(roads.MapServiceError, "locations changed"):
            roads.validate_routing(changed, artifact, expected_source_digest=artifact["source_digest"])

    def test_persistent_cache_is_offline_and_corrupt_entries_are_refetched(self):
        artifact = roads.prepare_routing(self.source)
        with roads._CACHE_LOCK:
            roads._CACHE.clear()
        with patch.object(roads, "_request_json", side_effect=AssertionError("no second provider request")):
            self.assertEqual(roads.prepare_routing(self.source), artifact)
        path = next(self.directory.glob("table-*.json"))
        saved = json.loads(path.read_text())
        saved["value"]["nodes"][0]["position"][0] += 1
        path.write_text(json.dumps(saved))
        with roads._CACHE_LOCK:
            roads._CACHE.clear()
        self.assertEqual(roads.prepare_routing(self.source)["nodes"], artifact["nodes"])
        self.assertEqual(len(self.state.calls), 2)

    def test_selected_geometry_matches_frozen_metrics_and_is_cached_per_snapshot(self):
        artifact = roads.prepare_routing(self.source)
        geometry = roads.road_geometry(artifact, "P1", "D1")
        by_id = {node["id"]: node for node in artifact["nodes"]}
        self.assertEqual(geometry, [by_id["P1"]["snapped_position"], by_id["D1"]["snapped_position"]])
        self.assertEqual(len(self.state.calls), 2)
        query = parse_qs(urlsplit(self.state.calls[-1]["path"]).query)
        self.assertEqual(query["geometries"], ["geojson"])
        self.assertEqual(query["continue_straight"], ["false"])
        geometry[0][0] += 1
        with roads._CACHE_LOCK:
            roads._CACHE.clear()
        with patch.object(roads, "_request_json", side_effect=AssertionError("cached geometry must be offline")):
            self.assertEqual(roads.road_geometry(artifact, "P1", "D1")[0], by_id["P1"]["snapped_position"])
        roads.road_geometry(artifact, "D1", "P1")
        self.assertEqual(len(self.state.calls), 3, "directed legs have different cached geometry")

    def test_geometry_rejects_changed_metrics_endpoints_and_provider(self):
        artifact = roads.prepare_routing(self.source)
        mutations = (
            lambda result: result["routes"][0].update(distance=999999),
            lambda result: result["routes"][0].update(duration=999999),
            lambda result: result["waypoints"][0].update(location=[0, 0]),
            lambda result: result["routes"][0]["geometry"].update(coordinates=[[0, 0], [1, 1]]),
        )
        for mutate in mutations:
            self.state.route_mutation = mutate
            with self.subTest(mutate=mutate):
                with self.assertRaises(roads.MapServiceError):
                    roads.road_geometry(artifact, "P1", "D1")
        before = len(self.state.calls)
        invalid = copy.deepcopy(artifact)
        invalid["base_url"] = "http://127.0.0.1:1/private"
        invalid["digest"] = roads.routing_digest(invalid)
        with self.assertRaisesRegex(roads.MapServiceError, "provider changed"):
            roads.road_geometry(invalid, "P1", "D1")
        self.assertEqual(len(self.state.calls), before, "a persisted artifact cannot choose an arbitrary network target")

    def test_changed_provider_metrics_refresh_next_table_without_touching_approved_geometry(self):
        original = roads.prepare_routing(self.source)
        original_bytes = json.dumps(original, sort_keys=True)
        saved_path = roads.road_geometry(original, "P1", "D1")
        self.state.route_mutation = lambda result: result["routes"][0].update(distance=999999)
        # Also cover cache directories where removing an old file is disallowed.
        with patch.object(Path, "unlink", side_effect=PermissionError("read-only cache")):
            with self.assertRaisesRegex(roads.MapServiceError, "no longer matches"):
                roads.road_geometry(original, "P1", "D2")
        self.assertEqual(len(self.state.calls), 3)
        self.assertTrue(list(self.directory.glob("table-*.json")))
        self.state.route_mutation = None

        def changed_table(result):
            result["distances"][0][4] += 30
            result["durations"][0][4] += 5

        self.state.table_mutation = changed_table
        with roads._CACHE_LOCK:
            roads._CACHE.clear()
        refreshed = roads.prepare_routing(self.source)
        self.assertEqual(len(self.state.calls), 4, "the next preparation bypasses both stale memory and disk entries")
        self.assertEqual(refreshed["source_digest"], original["source_digest"])
        self.assertNotEqual(refreshed["digest"], original["digest"])
        self.assertEqual(refreshed["distances_m"][0][4], original["distances_m"][0][4] + 30)
        self.assertEqual(json.dumps(original, sort_keys=True), original_bytes, "approved matrix values remain unchanged")
        with patch.object(roads, "_request_json", side_effect=AssertionError("saved geometry must remain offline")):
            self.assertEqual(roads.road_geometry(original, "P1", "D1"), saved_path)
        roads.road_geometry(refreshed, "P1", "D1")
        self.assertEqual(len(self.state.calls), 5, "the refreshed snapshot captures geometry using its own metrics")

    def test_missing_road_response_invalidates_table_but_transient_outage_does_not(self):
        original = roads.prepare_routing(self.source)
        self.state.failure = (503, b"temporarily unavailable", {})
        with self.assertRaises(roads.MapServiceError):
            roads.road_geometry(original, "P1", "D1")
        self.state.failure = None
        self.assertEqual(roads.prepare_routing(self.source), original)
        self.assertEqual(len(self.state.calls), 2, "a transient provider outage retains usable table data")
        self.state.failure = (400, b'{"code":"NoSegment"}', {})
        with self.assertRaisesRegex(roads.MapServiceError, "no drivable road"):
            roads.road_geometry(original, "P1", "D1")
        self.state.failure = None
        roads.prepare_routing(self.source)
        self.assertEqual(len(self.state.calls), 4, "missing roads cause the next preparation to fetch a current table")

    def test_changed_road_snapping_also_refreshes_next_preparation(self):
        artifact = roads.prepare_routing(self.source)

        def changed_endpoint(result):
            point = result["waypoints"][0]["location"]
            result["waypoints"][0]["location"] = [point[0] + 0.00005, point[1]]

        self.state.route_mutation = changed_endpoint
        with self.assertRaisesRegex(roads.MapServiceError, "changed a road endpoint"):
            roads.road_geometry(artifact, "P1", "D1")
        self.state.route_mutation = None
        roads.prepare_routing(self.source)
        self.assertEqual(len(self.state.calls), 3)

    def test_provider_bad_status_redirect_invalid_json_and_oversize_fail_closed(self):
        for failure in (
            (429, b"", {}),
            (503, b"unavailable", {}),
            (302, b"", {"Location": self.base + "/private"}),
            (200, b'{"code":"Ok","distance":NaN}', {}),
            (200, b'{"code":"Ok","code":"NoSegment"}', {}),
            (200, b"{}", {"Content-Length": "10000001"}),
        ):
            self.state.failure = failure
            before = len(self.state.calls)
            with self.subTest(failure=failure):
                with self.assertRaises(roads.MapServiceError):
                    roads.prepare_routing(self.source)
            self.assertEqual(len(self.state.calls), before + 1, "errors do not trigger redirect or fallback requests")

    def test_no_road_far_snaps_incomplete_tables_and_estimates_are_not_accepted(self):
        for mutate in (
            lambda result: result.update(code="NoSegment"),
            lambda result: result["sources"][0].update(location=[0, 0], distance=0),
            lambda result: result["sources"].pop(),
            lambda result: result.update(fallback_speed_cells=[[0, 1]]),
        ):
            self.state.table_mutation = mutate
            with self.subTest(mutate=mutate):
                with self.assertRaises(roads.MapServiceError):
                    roads.prepare_routing(self.source)

    def test_explicit_search_encodes_input_biases_results_and_caches(self):
        query = "City hall & stop?"
        places = roads.search_places(query, lon=-79.38, lat=43.65)
        self.assertEqual(places, [{"label": query + ", Toronto, Canada", "position": [-79.383201, 43.6532]}])
        parameters = parse_qs(urlsplit(self.state.calls[0]["path"]).query)
        self.assertEqual(parameters["q"], [query])
        self.assertEqual(parameters["limit"], ["5"])
        self.assertEqual(parameters["lon"], ["-79.38"])
        self.assertEqual(roads.search_places(query, lon=-79.38, lat=43.65), places)
        self.assertEqual(len(self.state.calls), 1)
        for invalid in ("", "x" * 201, "bad\nquery"):
            with self.assertRaises(roads.MapServiceError):
                roads.search_places(invalid)
        with self.assertRaises(roads.MapServiceError):
            roads.search_places("Toronto", lon=1)
        self.assertEqual(len(self.state.calls), 1)

    def test_osrm_preparation_and_geometry_share_a_single_application_limiter(self):
        with patch.object(roads, "REQUEST_INTERVAL_SECONDS", 0.06):
            artifact = roads.prepare_routing(self.source)
            roads.road_geometry(artifact, "P1", "D1")
        self.assertGreaterEqual(self.state.calls[1]["time"] - self.state.calls[0]["time"], 0.05)

    def test_configurable_providers_and_bounded_memory_cache(self):
        config = roads.maps_config()
        self.assertIn("openstreetmap.org/copyright", config["tile_attribution"])
        self.assertIn("project-osrm.org", config["routing_attribution"])
        with patch.dict(os.environ, {"QUANTUM_TILE_URL": "https://tiles.example/{z}/{x}/{y}.png?key=public-token"}):
            self.assertIn("key=public-token", roads.maps_config()["tile_url"])
        for invalid in ("file:///secret", "http://user:password@localhost", "https://example.test/?url=private"):
            with patch.dict(os.environ, {"QUANTUM_OSRM_URL": invalid}):
                with self.assertRaises(roads.MapServiceError):
                    roads.prepare_routing(self.source)
        with patch.object(roads, "MAX_MEMORY_ENTRIES", 2):
            roads._store("one", {"value": 1})
            roads._store("two", {"value": 2})
            roads._store("three", {"value": 3})
            self.assertEqual(list(roads._CACHE), ["two", "three"])


if __name__ == "__main__":
    unittest.main()
