"""HTTP map configuration/search boundaries and saved replay rejection paths.

All providers are patched or synthetic fixtures; these tests never contact a
public geocoder, router, or tile server.
"""

import copy
import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from server import Handler
from ev_reference import PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, normalize_handoff
from geographic import geo_seed
from pipeline_live import project_result
from road_network import MapServiceError, search_places
from road_replay import capture_replay
from scenario import scenario_prompt
from test_geographic import raw_geo_result, routing_fixture


MAP_SETTINGS = {
    "tile_url": "https://tiles.example.test/{z}/{x}/{y}.png",
    "tile_attribution": '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    "geocoder_attribution": "Photon / OpenStreetMap",
    "routing_attribution": "OSRM / OpenStreetMap",
}


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class MapsHttpTests(unittest.TestCase):
    def setUp(self):
        for target, value in (("server.backend_url", ""), ("server.native_pipeline_configured", False),
                              ("server.local_solver_available", False), ("server.planner_configured", False)):
            replacement = patch(target, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        deny_network = patch("road_network._request_json", side_effect=AssertionError("public provider request in HTTP test"))
        self.network = deny_network.start()
        self.addCleanup(deny_network.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def request_json(self, path, payload=None, content_type="application/json"):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.url + path, data=body, headers={"Content-Type": content_type})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            return response.status, json.load(response)

    def test_map_configuration_is_public_safe_metadata_and_matches_boot_configuration(self):
        with patch("server.maps_config", return_value=MAP_SETTINGS):
            status, settings = self.request_json("/api/maps/config")
            self.assertEqual(status, 200)
            self.assertEqual(settings, MAP_SETTINGS)
            status, boot = self.request_json("/api/config")
            self.assertEqual(status, 200)
            self.assertEqual(boot["maps"], settings)
            self.assertFalse(boot["backend_configured"])
        self.assertNotIn("base_url", settings)
        self.assertNotIn("geocoder_url", settings)
        self.network.assert_not_called()

    def test_broken_map_provider_configuration_does_not_disable_grid_boot(self):
        with (
            patch("server.maps_config", side_effect=MapServiceError("Tile provider URL is invalid.")),
            patch("server.native_pipeline_configured", return_value=True),
        ):
            status, failure = self.request_json("/api/maps/config")
            self.assertEqual(status, 503)
            self.assertEqual(failure, {"error": "Tile provider URL is invalid."})
            status, boot = self.request_json("/api/config")
            self.assertEqual(status, 200)
            self.assertTrue(boot["pipeline_backend_configured"])
            self.assertIsNone(boot["maps"])
            self.assertEqual(boot["maps_error"], failure["error"])

    def test_search_forwards_decoded_query_and_coordinates_without_accepting_provider_urls(self):
        places = [{"label": "Toronto City Hall", "position": [-79.3832, 43.6532]}]
        with patch("server.search_places", return_value=places) as search:
            parameters = {"q": "City hall & deliveries?", "lon": "-79.38", "lat": "43.65",
                          "url": "http://127.0.0.1/private", "provider": "file:///secret"}
            status, body = self.request_json("/api/maps/search?" + urlencode(parameters))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"results": places})
        search.assert_called_once_with("City hall & deliveries?", lon=-79.38, lat=43.65)
        self.network.assert_not_called()

    def test_bad_numeric_text_is_rejected_before_geocoder_and_failures_remain_actionable_json(self):
        with patch("server.search_places") as search:
            status, body = self.request_json("/api/maps/search?q=Toronto&lon=not-a-number&lat=43")
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        search.assert_not_called()
        for code in (400, 422, 429, 503):
            with self.subTest(code=code), patch("server.search_places", side_effect=MapServiceError("Keep the draft and try again.", code)):
                status, body = self.request_json("/api/maps/search?q=Toronto")
                self.assertEqual(status, code)
                self.assertEqual(body, {"error": "Keep the draft and try again."})

    def test_missing_query_and_nonfinite_or_incomplete_focus_never_reach_provider(self):
        with patch("server.search_places", wraps=search_places):
            for query in ("", "?q=", "?q=" + "x" * 201, "?q=Toronto&lon=nan&lat=43",
                          "?q=Toronto&lon=1&lat=inf", "?q=Toronto&lon=1", "?q=Toronto&lat=43"):
                with self.subTest(query=query):
                    status, body = self.request_json("/api/maps/search" + query)
                    self.assertIn(status, (400, 422))
                    self.assertIsInstance(body.get("error"), str)
        self.network.assert_not_called()

    def test_ordered_facility_road_paths_expose_only_validated_metrics_and_geometry(self):
        pins = [[-79.38, 43.65], [-79.39, 43.65]]
        payload = {"positions": pins, "from_index": 1, "to_index": 0, "routing_digest": "a" * 64}
        verified = {"from_index": 1, "to_index": 0, "coordinates": [pins[1], [-79.385, 43.66], pins[0]],
                    "distance_m": 207, "duration_s": 20.7, "routing_digest": payload["routing_digest"]}
        with patch("server.road_path", return_value=verified) as route:
            self.assertEqual(self.request_json("/api/maps/road-path", payload), (200, verified))
            route.assert_called_once_with(pins, 1, 0, payload["routing_digest"])
            for extra in ({"url": "https://example.test"}, {"routing": {}}, {"distances_m": [[0, 3], [4, 0]]}):
                self.assertEqual(self.request_json("/api/maps/road-path", {**payload, **extra})[0], 400)
            self.assertEqual(self.request_json("/api/maps/road-path", payload, "text/plain")[0], 415)
            self.assertEqual(self.request_json("/api/maps/road-path", {**payload, "routing_digest": "x" * 17000})[0], 400)
            self.assertEqual(route.call_count, 1)
        with patch("server.road_path", side_effect=MapServiceError("The routing snapshot changed.", 409)):
            self.assertEqual(self.request_json("/api/maps/road-path", payload), (409, {"error": "The routing snapshot changed."}))
        self.network.assert_not_called()

    def test_maps_endpoint_post_cannot_start_a_provider_request_or_change_configuration(self):
        request = Request(self.url + "/api/maps/search", b'{"q":"Toronto"}',
                          {"Content-Type": "application/json"})
        with patch("server.search_places") as search, patch("server.maps_config") as configuration:
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=3)
            with error.exception:
                self.assertEqual(error.exception.code, 404)
        search.assert_not_called()
        configuration.assert_not_called()
        self.network.assert_not_called()


class MapReplayBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.source = geo_seed()
        self.routing = routing_fixture(self.source)
        self.output = raw_geo_result(self.source, self.routing)
        self.artifact = {"problem": {"title": "Geographic contract fixture", "summary": "Synthetic routes"}, "controls": []}
        self.identity = {"run_id": "run", "plan_id": "plan", "attempt_id": "attempt"}

    def capture(self):
        def geometry(routing, first, second):
            nodes = {node["id"]: node["snapped_position"] for node in routing["nodes"]}
            a, b = nodes[first], nodes[second]
            return [list(a), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], list(b)]

        with patch("road_replay.road_geometry", side_effect=geometry):
            return capture_replay(self.output, self.routing, fetch=True)

    def test_v3_is_geographic_only_and_legacy_v1_v2_stay_compatible(self):
        legacy = reference_handoff({})
        normalize_handoff(legacy, "reference", PROMPT)
        multi = copy.deepcopy(legacy)
        multi["schema_version"] = 2
        scene = multi["result"]["simulation"]
        scene["depots"] = [{"id": "P1", "position": scene.pop("depot")}]
        for van in scene["vans"]:
            van["depot_id"] = "P1"
        normalize_handoff(multi, "reference", PROMPT)
        invalid = copy.deepcopy(multi)
        invalid["schema_version"] = 3
        with self.assertRaisesRegex(InvalidHandoff, "requires a geographic"):
            normalize_handoff(invalid, "reference", PROMPT)
        artifact = {**self.artifact, "geographic_replay": self.capture()}
        handoff = project_result(artifact, self.output, scenario_prompt(self.source), self.identity,
                                 source_scenario=self.source, routing_artifact=self.routing)
        self.assertEqual(handoff["schema_version"], 3)
        normalize_handoff(handoff, "live", scenario_prompt(self.source))
        for version in (1, 2):
            handoff["schema_version"] = version
            with self.assertRaisesRegex(InvalidHandoff, "version 3"):
                normalize_handoff(handoff, "live", scenario_prompt(self.source))

    def test_malformed_map_metadata_is_rejected_as_a_handoff_error(self):
        for metadata in (None, [], "geographic", 1, True, {"kind": "unknown"}):
            with self.subTest(metadata=metadata):
                handoff = reference_handoff({})
                handoff["result"]["simulation"]["map"] = metadata
                with self.assertRaises(InvalidHandoff):
                    normalize_handoff(handoff, "reference", PROMPT)

    def test_malformed_saved_captures_fail_closed_without_network(self):
        for stored in ([], "broken", 1, True, None):
            with self.subTest(stored=stored), patch("road_replay.road_geometry") as geometry:
                with self.assertRaises(ValueError):
                    capture_replay(self.output, self.routing, stored=stored, fetch=False)
                geometry.assert_not_called()
        for mutation in ("boolean-version", "oversize-coordinate"):
            capture = self.capture()
            if mutation == "boolean-version":
                capture["schema_version"] = True
            else:
                event = next(event for van in capture["simulation"]["vans"] for event in van["events"] if event["type"] == "drive")
                event["path"][1][0] = 10 ** 400
            metadata = {key: value for key, value in capture.items() if key != "digest"}
            capture["digest"] = hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), patch("road_replay.road_geometry") as geometry:
                with self.assertRaises(ValueError):
                    capture_replay(self.output, self.routing, stored=capture, fetch=False)
                geometry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
