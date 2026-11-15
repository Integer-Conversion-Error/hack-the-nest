import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from ev_reference import PROMPT, reference_handoff
from server import Handler


CONTROL_ID = "van_2_initial_charge_pct"


class QuietDashboard(Handler):
    def log_message(self, *_args):
        pass


def verified_handoff(values, previous=None, provenance="live"):
    handoff = copy.deepcopy(reference_handoff(values, previous))
    result = handoff["result"]
    result["status"] = "verified"
    result["provenance"] = provenance
    result["quantum"]["actual_qubits"] = result["quantum"]["binary_variables"]
    bits = "0" * result["quantum"]["actual_qubits"]
    result["simulation"]["counts"] = {bits: 16}
    result["circuits"] = [{"title": "Test circuit", "drawing": "test circuit diagram"}]
    return handoff



def with_attempt(handoff, attempt_id):
    response = copy.deepcopy(handoff)
    response["attempt_id"] = attempt_id
    response["result"]["attempt_id"] = attempt_id
    return response


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.dashboard_url = self.serve(QuietDashboard)

    def serve(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def configure_backend(self, *responses):
        calls = []
        pending = iter(responses)

        class BackendStub(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, request))
                template = next(pending, None)
                if callable(template):
                    response = template(request)
                elif template is not None:
                    response = with_attempt(template, request["attempt_id"])
                else:
                    response = None
                body = json.dumps(response if response is not None else {"error": "Unexpected backend call"}).encode()
                self.send_response(200 if response is not None else 500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        env = patch.dict(os.environ, {"QUANTUM_BACKEND_URL": self.serve(BackendStub)})
        env.start()
        self.addCleanup(env.stop)
        return calls

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.dashboard_url + path, data, {"Content-Type": "application/json"})
        return urlopen(request, timeout=5)

    def run_plan(self, mode, **request):
        with self.request("/api/run", {"mode": mode, **request}) as response:
            return json.load(response)

    def assert_run_error(self, mode, expected_status=None, **request):
        with self.assertRaises(HTTPError) as caught:
            self.run_plan(mode, **request)
        with caught.exception as response:
            if expected_status is not None:
                self.assertEqual(response.code, expected_status)
            body = json.load(response)
            self.assertTrue(body["error"])
            return body["error"]

    def read_frame(self, view_id):
        params = urlencode({"view_id": view_id, "minute": 60})
        with self.request(f"/api/pygame-frame?{params}") as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(json.loads(response.headers["X-Pygame-Scene"])["minute"], 60)
            return response.read()

    def test_recorded_run_and_control_use_backend_measurements_without_a_service(self):
        with patch.dict(os.environ, {"QUANTUM_BACKEND_URL": ""}):
            with self.request("/api/example") as response:
                example = json.load(response)
            with self.request("/api/run", {"prompt": example["prompt"]}) as response:
                first = json.load(response)
            updated = self.run_plan("recorded", view_id=first["view_id"], variables={CONTROL_ID: 20})

        self.assertEqual(first["mode"], "recorded")
        self.assertEqual(first["run_id"], updated["run_id"])
        self.assertEqual(first["plan_id"], updated["plan_id"])
        self.assertNotEqual(first["attempt_id"], updated["attempt_id"])
        self.assertNotEqual(first["view_id"], updated["view_id"])
        for run, charge, cost, qubits, selected, count, file in (
            (first, 35, 15.783333, 5, "00101", 310, "handoff.json"),
            (updated, 20, 58.366667, 4, "0110", 491, "test.json"),
        ):
            with self.subTest(charge=charge):
                self.assertEqual(run["provenance"], "recorded")
                self.assertEqual(run["source"]["file"], file)
                self.assertEqual(run["effective_values"], {CONTROL_ID: charge})
                self.assertEqual(run["evaluation"]["objective_value"], cost)
                self.assertEqual(run["evaluation"]["reference_value"], cost)
                self.assertEqual(run["evaluation"]["gap"], 0)
                self.assertEqual(run["quantum"]["actual_qubits"], qubits)
                self.assertEqual(run["quantum"]["best_bitstring"], selected)
                self.assertEqual(sum(run["simulation"]["counts"].values()), 2048)
                self.assertEqual(run["simulation"]["counts"][selected], count)
        with patch("pygame_renderer.render_png", return_value=b"recorded png") as render:
            self.assertEqual(self.read_frame(updated["view_id"]), b"recorded png")
            schedule = render.call_args.args[0]
            charging = {
                van["id"]: [(event["start_min"], event["end_min"]) for event in van["events"]
                            if event["type"] == "charge"]
                for van in schedule["vans"]
            }
            self.assertEqual(charging, {"V1": [(60, 90)], "V2": [(30, 60)]})

    def test_reference_dispatch_and_recalculation_work_without_backend(self):
        with patch.dict(os.environ, {"QUANTUM_BACKEND_URL": ""}):
            with self.request("/api/config") as response:
                self.assertFalse(json.load(response)["backend_configured"])
            with self.request("/api/example") as response:
                example = json.load(response)
            self.assertEqual(example["prompt"], PROMPT)
            self.assertEqual([control["id"] for control in example["variables"]], [CONTROL_ID])
            self.assertEqual(example["variables"][0]["allowed_values"], [35, 20])
            first = self.run_plan("reference", prompt=example["prompt"])
            second = self.run_plan("reference", view_id=first["view_id"], variables={CONTROL_ID: 20})
        self.assertEqual(first["provenance"], "classical_reference")
        self.assertEqual(first["effective_values"], {CONTROL_ID: 35})
        self.assertAlmostEqual(first["evaluation"]["objective_value"], 947 / 60, places=2)
        self.assertEqual(second["effective_values"], {CONTROL_ID: 20})
        self.assertAlmostEqual(second["evaluation"]["objective_value"], 3502 / 60, places=2)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertNotEqual(first["attempt_id"], second["attempt_id"])
        self.assertNotEqual(first["view_id"], second["view_id"])
        self.assertIsNone(second["quantum"]["actual_qubits"])
        self.assertFalse(second["simulation"].get("counts"))

    def test_live_posts_once_per_attempt_and_renders_each_accepted_view(self):
        first_handoff = verified_handoff({})
        updated_handoff = verified_handoff({CONTROL_ID: 20}, first_handoff["result"], provenance="recorded")
        backend_calls = self.configure_backend(first_handoff, updated_handoff)
        with self.request("/api/config") as response:
            self.assertTrue(json.load(response)["backend_configured"])

        first = self.run_plan("live", prompt=PROMPT)
        updated = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 20})
        self.assertEqual(backend_calls, [
            ("/api/run", {
                "schema_version": 1, "attempt_id": first["attempt_id"], "prompt": PROMPT, "values": {},
            }),
            ("/api/run", {
                "schema_version": 1,
                "attempt_id": updated["attempt_id"],
                "run_id": first["run_id"],
                "plan_id": first["plan_id"],
                "previous_attempt_id": first["attempt_id"],
                "values": {CONTROL_ID: 20},
            }),
        ])
        self.assertEqual(first["provenance"], "live")
        self.assertEqual(updated["provenance"], "recorded")
        self.assertNotEqual(updated["attempt_id"], first["attempt_id"])
        self.assertEqual(updated["effective_values"], {CONTROL_ID: 20})
        self.assertEqual(updated["circuits"], updated_handoff["result"]["circuits"])
        self.assertNotEqual(first["view_id"], updated["view_id"])

        def render_frame(simulation, *_args, **_kwargs):
            return str(simulation["vans"][1]["initial_battery_kwh"]).encode()

        with patch("pygame_renderer.render_png", side_effect=render_frame):
            self.assertEqual(float(self.read_frame(first["view_id"])), 7)
            self.assertEqual(float(self.read_frame(updated["view_id"])), 4)

    def test_bad_controls_do_not_call_backend(self):
        backend_calls = self.configure_backend(verified_handoff({}))
        first = self.run_plan("live", prompt=PROMPT)

        for variables in ({CONTROL_ID: 25}, {CONTROL_ID: True}, {"unapproved_control": 20}):
            with self.subTest(variables=variables):
                self.assert_run_error("live", 400, view_id=first["view_id"], variables=variables)
        self.assertEqual(len(backend_calls), 1)

    def test_bad_handoffs_leave_the_last_verified_view_available(self):
        first_handoff = verified_handoff({})
        valid = verified_handoff({CONTROL_ID: 20}, first_handoff["result"])

        no_go = copy.deepcopy(valid)
        no_go["result"].update(status="no_go", message="No feasible measurement.")
        def mismatched_identity(request):
            response = with_attempt(valid, request["attempt_id"])
            response["result"]["attempt_id"] = request["previous_attempt_id"]
            return response

        def reuse_previous_attempt(request):
            return with_attempt(valid, request["previous_attempt_id"])

        different_values = verified_handoff({CONTROL_ID: 35}, first_handoff["result"])
        different_controls = copy.deepcopy(valid)
        different_controls["controls"][0]["allowed_values"].append(25)
        bad_scene = copy.deepcopy(valid)
        bad_scene["scene_type"] = "unsupported_scene"
        unknown_delivery = copy.deepcopy(valid)
        events = unknown_delivery["result"]["simulation"]["vans"][0]["events"]
        next(event for event in events if event["type"] == "deliver")["delivery_id"] = "unknown"

        failures = {
            "no go": no_go,
            "mismatched attempt": mismatched_identity,
            "reused attempt": reuse_previous_attempt,
            "mismatched values": different_values,
            "changed approved controls": different_controls,
            "unsupported scene": bad_scene,
            "unknown delivery entity": unknown_delivery,
        }
        def reuse_initial_attempt(_request):
            return with_attempt(first_handoff, calls[0][1]["attempt_id"])

        calls = self.configure_backend(first_handoff, *failures.values(), valid, reuse_initial_attempt)
        first = self.run_plan("live", prompt=PROMPT)
        for problem in failures:
            with self.subTest(problem=problem):
                self.assert_run_error("live", view_id=first["view_id"], variables={CONTROL_ID: 20})

        updated = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 20})
        self.assert_run_error("live", view_id=updated["view_id"], variables={CONTROL_ID: 35})
        self.assertEqual(len(calls), len(failures) + 3)
        with patch("pygame_renderer.render_png", return_value=b"fixture png") as render:
            self.assertEqual(self.read_frame(first["view_id"]), b"fixture png")
            self.assertEqual(render.call_args.args[0]["vans"][1]["initial_battery_kwh"], 7)


    def test_retry_rejects_a_result_for_the_timed_out_attempt(self):
        initial = verified_handoff({})
        updated = verified_handoff({CONTROL_ID: 20}, initial["result"])
        timed_out_attempts = []

        def time_out(request, *, timeout):
            timed_out_attempts.append(json.loads(request.data)["attempt_id"])
            raise TimeoutError()

        def late_result(_request):
            return with_attempt(updated, timed_out_attempts[0])

        calls = self.configure_backend(initial, late_result, updated)
        first = self.run_plan("live", prompt=PROMPT)
        request = {"view_id": first["view_id"], "variables": {CONTROL_ID: 20}}
        with patch("server.urlopen", side_effect=time_out):
            self.assert_run_error("live", 504, **request)

        self.assertIn("pending attempt", self.assert_run_error("live", 502, **request))
        accepted = self.run_plan("live", **request)
        retry_attempts = [payload["attempt_id"] for _, payload in calls[1:]]
        self.assertEqual(len(set(timed_out_attempts + retry_attempts)), 3)
        self.assertEqual(accepted["attempt_id"], retry_attempts[-1])


    def test_recalculation_rejects_a_stale_schedule_with_matching_values(self):
        initial = verified_handoff({})
        stale = verified_handoff({}, initial["result"])
        stale["result"]["effective_values"][CONTROL_ID] = 20
        updated = verified_handoff({CONTROL_ID: 20}, initial["result"])
        self.configure_backend(initial, stale, updated)
        first = self.run_plan("live", prompt=PROMPT)
        request = {"view_id": first["view_id"], "variables": {CONTROL_ID: 20}}

        message = self.assert_run_error("live", 502, **request)
        self.assertIn("starting battery does not match", message)
        with patch("pygame_renderer.render_png", return_value=b"fixture png"):
            self.assertEqual(self.read_frame(first["view_id"]), b"fixture png")

        accepted = self.run_plan("live", **request)
        self.assertEqual(accepted["effective_values"], {CONTROL_ID: 20})
        self.assertEqual(accepted["simulation"]["vans"][1]["initial_battery_kwh"], 4)


if __name__ == "__main__":
    unittest.main()
