import copy
import json
import os
import threading
import unittest
from collections import OrderedDict
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from ev_recorded import LIVE_PROMPT, RECORDING_DIR, REPO_ROOT
from ev_reference import PROMPT, reference_handoff
from fleet_handoff import UnverifiedResult
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
        planner_env = patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""})
        planner_env.start()
        self.addCleanup(planner_env.stop)
        for name, value in (("VIEWS", {}), ("VIEW_BACKENDS", {}), ("SOLVER_RESULTS", OrderedDict())):
            state = patch("server." + name, value)
            state.start()
            self.addCleanup(state.stop)
        self.dashboard_url = self.serve(QuietDashboard)

    def test_openrouter_enables_the_native_pipeline_without_exposing_credentials(self):
        with patch.dict(os.environ, {
            "QUANTUM_BACKEND_URL": "", "MODEL_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "private-openrouter-key", "OPENROUTER_MODEL": "fixture/model",
        }, clear=True):
            with self.request("/api/config") as response:
                body = response.read().decode("utf-8")
            config = json.loads(body)
            self.assertTrue(config["pipeline_backend_configured"])
            self.assertTrue(config["saved_run_recalculation_configured"])
            self.assertNotIn("private-openrouter-key", body)

            with patch.dict(os.environ, {"OPENROUTER_MODEL": ""}):
                with self.request("/api/config") as response:
                    self.assertFalse(json.load(response)["pipeline_backend_configured"])

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

    def start_pipeline(self, prompt):
        with self.request("/api/pipeline-runs", {"prompt": prompt}) as response:
            self.assertEqual(response.status, 202)
            return json.load(response)

    def read_pipeline(self, job_id):
        with self.request(f"/api/pipeline-runs/{job_id}") as response:
            return json.load(response)

    def wait_for_pipeline(self, job_id):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            snapshot = self.read_pipeline(job_id)
            if snapshot["status"] in {"completed", "failed"}:
                return snapshot
            sleep(0.02)
        self.fail("The pipeline job did not finish.")

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
        with patch.dict(os.environ, {"QUANTUM_BACKEND_URL": ""}), patch(
            "server.local_solver_available", return_value=False
        ):
            with self.request("/api/config") as response:
                self.assertFalse(json.load(response)["backend_configured"])
            with self.request("/api/example") as response:
                example = json.load(response)
            self.assertEqual(example["prompt"], PROMPT)
            self.assertEqual(example["live_prompt"], LIVE_PROMPT)
            self.assertEqual([control["id"] for control in example["variables"]], [CONTROL_ID])
            self.assertEqual(example["variables"][0]["allowed_values"], [35, 20])
            multi_control_examples = ["ev-charge-and-energy-prices", "ev-waiting-and-late-delivery",
                                      "ev-battery-and-charger-limits", "ev-fleet-dispatch-full-scenario",
                                      "ev-six-van-six-controls"]
            self.assertEqual([(sample["id"], sample["prompt"]) for sample in example["samples"]], [
                ("ev-charge-range", LIVE_PROMPT),
                ("ev-charge-options", PROMPT),
                ("ev-electricity-price", PROMPT + "\nLet me vary the electricity price from $0.10 to $0.50 per kWh in $0.10 steps."),
                *((name, (REPO_ROOT / "example-user-prompts" / f"{name}.md").read_text(encoding="utf-8"))
                  for name in multi_control_examples),
            ])
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

    def test_builtin_solver_runs_again_when_the_charge_changes(self):
        final = json.loads((RECORDING_DIR / "handoff.json").read_text(encoding="utf-8"))["results"]
        with (
            patch.dict(os.environ, {"QUANTUM_BACKEND_URL": "", "QR_KEY": "", "QR_ACCOUNT": "",
                                    "PYTHON_DOTENV_DISABLED": "1"}),
            patch("ev_live.local_solver_available", return_value=True),
            patch("server.local_solver_available", return_value=True),
        ):
            from ev_live import run_solver

            alternate = run_solver(22)
            worker_patch = patch("ev_live.run_solver", side_effect=[final, alternate])
            with self.request("/api/config") as response:
                self.assertTrue(json.load(response)["local_backend_configured"])
            self.assert_run_error("live", 400, prompt="A different dispatch problem")
            with worker_patch as worker:
                first = self.run_plan("live", prompt=LIVE_PROMPT)
                for invalid in (19, 22.5, 36):
                    self.assert_run_error("live", 400, view_id=first["view_id"], variables={CONTROL_ID: invalid})
                updated = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 22})

        self.assertEqual([call.args for call in worker.call_args_list], [(35,), (22,)])
        self.assertEqual(first["provenance"], "live")
        self.assertEqual(updated["provenance"], "live")
        self.assertEqual(updated["effective_values"], {CONTROL_ID: 22})
        control, = first["variables"]
        self.assertEqual((control["minimum"], control["maximum"], control["step"]), (20, 35, 1))
        self.assertEqual(updated["simulation"]["vans"][1]["initial_battery_kwh"], 4.4)
        self.assertFalse(any(event["type"] == "charge" for event in first["simulation"]["vans"][1]["events"]))
        self.assertTrue(any(event["type"] == "charge" for event in updated["simulation"]["vans"][1]["events"]))
        self.assertEqual(first["run_id"], updated["run_id"])
        self.assertEqual(first["plan_id"], updated["plan_id"])
        self.assertNotEqual(first["attempt_id"], updated["attempt_id"])
        self.assertEqual(updated["simulation"]["counts"], alternate["quantum"]["counts"])
        self.assertEqual(updated["evaluation"]["objective_value"], alternate["objective"]["value"])

    def test_live_posts_once_per_attempt_and_renders_each_accepted_view(self):
        first_handoff = verified_handoff({})
        updated_handoff = verified_handoff({CONTROL_ID: 20}, first_handoff["result"], provenance="recorded")
        backend_calls = self.configure_backend(first_handoff, updated_handoff)
        with self.request("/api/config") as response:
            config = json.load(response)
            self.assertTrue(config["backend_configured"])
            self.assertFalse(config["local_backend_configured"])

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

    def test_external_classical_comparison_computes_gap_and_rejects_contradictions(self):
        initial = verified_handoff({})
        comparison = {
            "method": "Enumerate feasible schedules",
            "objective_value": 14,
            "feasible": True,
            "optimality_proven": True,
            "selected_options": ["Van 1: Slot 1", "Van 2: Direct"],
        }
        initial["result"]["evaluation"]["classical"] = comparison
        updated = verified_handoff({CONTROL_ID: 20}, initial["result"])
        updated["result"]["evaluation"]["classical"] = {**comparison, "objective_value": 60}
        self.configure_backend(initial, updated)

        first = self.run_plan("live", prompt=PROMPT)
        self.assertEqual(first["evaluation"]["classical"], comparison)
        self.assertEqual(first["evaluation"]["reference_value"], 14)
        self.assertAlmostEqual(first["evaluation"]["gap"], 947 / 60 - 14)
        self.assertIn("contradicts", self.assert_run_error(
            "live", 502, view_id=first["view_id"], variables={CONTROL_ID: 20},
        ))


    def test_apply_rejects_a_classical_map_from_an_older_charge(self):
        initial = verified_handoff({})
        stale = copy.deepcopy(initial["result"]["simulation"])
        stale["counts"] = {}
        updated = verified_handoff({CONTROL_ID: 20}, initial["result"])
        updated["result"]["evaluation"]["classical"] = {
            "method": "Enumerate feasible schedules",
            "objective_value": updated["result"]["evaluation"]["objective_value"],
            "feasible": True,
            "optimality_proven": True,
        }
        updated["result"]["classical_simulation"] = stale
        self.configure_backend(initial, updated)
        first = self.run_plan("live", prompt=PROMPT)

        self.assertIn("same vans and starting batteries", self.assert_run_error(
            "live", 502, view_id=first["view_id"], variables={CONTROL_ID: 20},
        ))


    def test_live_reuses_complete_verified_settings_and_keeps_backend_attempt_lineage(self):
        tariff = "electricity_price"

        def handoff(charge, price, previous=None):
            response = verified_handoff({CONTROL_ID: charge}, previous)
            response["controls"].append({
                "id": tariff, "label": "Electricity price", "type": "number", "default": 0.20,
                "allowed_values": [0.20, 0.30], "rerun_mode": "rebuild", "unit": "USD/kWh",
            })
            result = response["result"]
            result["effective_values"][tariff] = price
            evaluation = result["evaluation"]
            costs = evaluation["costs"]
            change = costs["electricity"] * (price - 0.20) / 0.20
            costs["electricity"] += change
            evaluation["objective_value"] += change
            evaluation["objective_units_60"] = round(evaluation["objective_value"] * 60)
            return response

        initial = handoff(35, 0.20)
        low_charge = handoff(20, 0.20, initial["result"])
        higher_tariff = handoff(20, 0.30, initial["result"])
        higher_tariff_at_default = handoff(35, 0.30, initial["result"])
        other_initial = handoff(35, 0.20)
        other_low_charge = handoff(20, 0.20, other_initial["result"])
        backend_calls = self.configure_backend(
            initial, low_charge, higher_tariff, higher_tariff_at_default, other_initial, other_low_charge,
        )

        first = self.run_plan("live", prompt=PROMPT)
        second = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 20})
        first_again = self.run_plan("live", view_id=second["view_id"], variables={CONTROL_ID: 35})
        second_again = self.run_plan("live", view_id=first_again["view_id"], variables={CONTROL_ID: 20})
        changed = self.run_plan("live", view_id=second_again["view_id"], variables={tariff: 0.30})
        changed_from_default = self.run_plan("live", view_id=first_again["view_id"], variables={tariff: 0.30})
        other_first = self.run_plan("live", prompt=PROMPT)
        other_second = self.run_plan("live", view_id=other_first["view_id"], variables={CONTROL_ID: 20})

        for saved, reused in ((first, first_again), (second, second_again)):
            self.assertNotIn("cached", saved)
            self.assertIs(reused["cached"], True)
            for key in ("run_id", "plan_id", "effective_values", "simulation", "evaluation", "quantum", "circuits"):
                self.assertEqual(reused[key], saved[key])
        views = (first, second, first_again, second_again, changed, changed_from_default, other_first, other_second)
        self.assertEqual(len({view["view_id"] for view in views}), len(views))
        self.assertEqual(len({view["attempt_id"] for view in views}), len(views))
        self.assertEqual(other_second["plan_id"], second["plan_id"])
        self.assertNotEqual(other_second["run_id"], second["run_id"])
        self.assertGreater(changed["evaluation"]["objective_value"], second["evaluation"]["objective_value"])

        payloads = [payload for _, payload in backend_calls]
        self.assertEqual([payload["attempt_id"] for payload in payloads], [
            first["attempt_id"], second["attempt_id"], changed["attempt_id"], changed_from_default["attempt_id"],
            other_first["attempt_id"], other_second["attempt_id"],
        ])
        self.assertEqual(payloads[2]["values"], {CONTROL_ID: 20, tariff: 0.30})
        self.assertEqual(payloads[2]["previous_attempt_id"], second["attempt_id"])
        self.assertEqual(payloads[3]["values"], {CONTROL_ID: 35, tariff: 0.30})
        self.assertEqual(payloads[3]["previous_attempt_id"], second["attempt_id"])


    def test_cached_settings_do_not_cross_external_backend_changes(self):
        original_calls = self.configure_backend(verified_handoff({}))
        first = self.run_plan("live", prompt=PROMPT)
        replacement_calls = self.configure_backend()

        message = self.assert_run_error("live", 409, view_id=first["view_id"], variables={CONTROL_ID: 35})

        self.assertIn("Start a new dispatch", message)
        self.assertEqual(len(original_calls), 1)
        self.assertEqual(replacement_calls, [])

    def test_verified_result_cache_evicts_old_settings_at_its_limit(self):
        first_handoff = verified_handoff({})
        alternate = verified_handoff({CONTROL_ID: 20}, first_handoff["result"])
        calls = self.configure_backend(first_handoff, alternate, first_handoff)
        with patch("server.MAX_CACHED_SOLVER_RESULTS", 1):
            first = self.run_plan("live", prompt=PROMPT)
            changed = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 20})
            restored = self.run_plan("live", view_id=changed["view_id"], variables={CONTROL_ID: 35})
        self.assertNotIn("cached", restored)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1][1]["previous_attempt_id"], changed["attempt_id"])

    def test_native_cache_requires_unchanged_solver_and_review_revision(self):
        from server import normalize_handoff, register_view, ViewBackend

        handoff = verified_handoff({})
        first = normalize_handoff(handoff, "live", PROMPT)
        register_view(first, backend=ViewBackend(("native", ""), first["attempt_id"], "original"))

        def recalculate(prompt, values, previous, attempt_id):
            return with_attempt(verified_handoff(values, previous), attempt_id)

        with (
            patch.dict(os.environ, {"QUANTUM_BACKEND_URL": ""}),
            patch("server.planner_configured", return_value=True),
            patch("server.pipeline_handoff", side_effect=recalculate) as solver,
            patch("server.native_cache_revision", return_value="original"),
        ):
            reused = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 35})
            self.assertIs(reused["cached"], True)
            solver.assert_not_called()
            for revision in ("solver-changed", "review-policy-changed", None):
                with patch("server.native_cache_revision", return_value=revision):
                    refreshed = self.run_plan("live", view_id=reused["view_id"], variables={CONTROL_ID: 35})
                self.assertNotIn("cached", refreshed)
            self.assertEqual(solver.call_count, 3)

    @unittest.skipUnless(find_spec("pydantic"), "The planner dependencies are required.")
    def test_native_cache_revision_changes_with_workspace_inputs_and_review_model(self):
        from server import native_cache_revision
        from backend.workspace import init_workspace

        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "QUANTUM_WORKSPACES_ROOT": directory, "MODEL_PROVIDER": "gemini",
        }):
            workspace = init_workspace(PROMPT, scene_type="ev_fleet")
            workspace.resolve("problem.json").write_bytes((RECORDING_DIR / "problem.json").read_bytes())
            plan = REPO_ROOT / "example-solver-plans" / "ev-fleet-dispatch-optimization" / "plan.json"
            workspace.resolve("plan.json").write_bytes(plan.read_bytes())
            workspace.resolve("main.py").write_text("print('first solver')\n", encoding="utf-8")
            workspace.resolve("handoff.json").write_text(json.dumps({
                "decision": "GO", "workspace_id": workspace.id,
            }), encoding="utf-8")
            view = {"plan_id": workspace.id}
            original = native_cache_revision(view)
            self.assertIsInstance(original, str)
            workspace.resolve("main.py").write_text("print('changed solver')\n", encoding="utf-8")
            changed = native_cache_revision(view)
            self.assertNotEqual(changed, original)
            with patch.dict(os.environ, {"GEMINI_SEMANTIC_REVIEW_MODEL": "other-review-model"}):
                self.assertNotEqual(native_cache_revision(view), changed)
            for key in ("QR_BACKEND", "QR_ACCOUNT", "QR_KEY"):
                with self.subTest(runtime_setting=key), patch.dict(os.environ, {key: "changed-fixture-" + key}):
                    self.assertNotEqual(native_cache_revision(view), changed)
            workspace.resolve("plan.json").unlink()
            self.assertIsNone(native_cache_revision(view))


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
        calls = self.configure_backend(first_handoff, *failures.values(), valid)
        first = self.run_plan("live", prompt=PROMPT)
        for problem in failures:
            with self.subTest(problem=problem):
                self.assert_run_error("live", view_id=first["view_id"], variables={CONTROL_ID: 20})

        updated = self.run_plan("live", view_id=first["view_id"], variables={CONTROL_ID: 20})
        restored = self.run_plan("live", view_id=updated["view_id"], variables={CONTROL_ID: 35})
        self.assertEqual(restored["simulation"], first["simulation"])
        self.assertIs(restored["cached"], True)
        self.assertEqual(len(calls), len(failures) + 2)
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

    @unittest.skipUnless(find_spec("pydantic"), "The planner dependencies are required.")
    def test_native_pipeline_applies_a_planned_tariff_and_verifies_each_attempt(self):
        from backend.final_check import FinalCheck
        from backend.plan import PlanningResult, save_plan
        from backend.semantics import SemanticIssue, SemanticReview
        from backend.workspace import init_workspace, open_workspace

        tariff = "electricity_price"
        prompt = PROMPT + "\nLet me vary the electricity price from $0.10 to $0.50 per kWh in $0.10 steps."
        fixture = reference_handoff({})["result"]
        fixed_cost = sum(value for name, value in fixture["evaluation"]["costs"].items() if name != "electricity")
        reviewed_tariffs = []
        stages = ("init", "plan", "build", "test", "verify")
        reached = {stage: threading.Event() for stage in stages}
        proceed = {stage: threading.Event() for stage in stages}

        def pause_stage(stage):
            reached[stage].set()
            if not proceed[stage].wait(5):
                raise TimeoutError(f"The test did not release the {stage} stage.")

        def parse(workspace, **_kwargs):
            pause_stage("init")
            problem = (RECORDING_DIR / "problem.json").read_text(encoding="utf-8")
            workspace.resolve("problem.json").write_text(problem, encoding="utf-8")

        def plan(workspace, *_args, **_kwargs):
            pause_stage("plan")
            planning = PlanningResult(
                problem_title="EV fleet electricity price", model="fixture", attempts=1,
                max_qubits=5, worker_seconds=5,
                research={"status": "unavailable", "summary": "Fixture plan"},
                review={"verdict": "approve"},
                plan={
                    "summary": "Reprice charging for the verified fleet schedule.",
                    "objective": "Minimize dispatch cost.",
                    "constraints": [], "assumptions": [],
                    "controls": [{
                        "key": tariff, "type": "number", "label": "Electricity price",
                        "default": 0.20, "minimum": 0.10, "maximum": 0.50, "step": 0.10,
                        "group": " Costs ", "unit": "$/kWh", "rerun_mode": "rebuild",
                    }],
                    "selected_solver": "Fleet scheduler",
                    "solver_examples": [{
                        "name": "Fleet scheduler", "method": "QAOA", "classical_steps": [],
                        "quantum_steps": ["Measure the chosen schedule."], "encoding": "Route selectors",
                        "qubits": {"decision_qubits": 5, "auxiliary_qubits": 0},
                        "verification": ["Recompute charging costs from the schedule."],
                        "reference_method": "Enumerate feasible schedules.",
                        "build_steps": ["Price charging with the requested electricity rate."],
                    }],
                },
            )
            save_plan(workspace, planning)
            return planning

        def build(workspace, *_args, **_kwargs):
            pause_stage("build")
            workspace.resolve("replay.json").write_text(json.dumps(fixture), encoding="utf-8")
            workspace.resolve("main.py").write_text(dedent("""
                import argparse
                import json
                from pathlib import Path

                parser = argparse.ArgumentParser()
                parser.add_argument("--electricity_price", type=float, default=0.20)
                price = parser.parse_args().electricity_price
                replay = json.loads(Path("replay.json").read_text(encoding="utf-8"))
                simulation = replay["simulation"]
                energy = sum(event["energy_kwh"] for van in simulation["vans"]
                             for event in van["events"] if event["type"] == "charge")
                costs = replay["evaluation"]["costs"]
                # Emulate a stale tariff calculation to exercise rejected verification.
                costs["electricity"] = energy * (0.20 if price == 0.40 else price)
                cost = sum(costs.values())
                result = {
                    "status": "ok", "controls": {"electricity_price": price},
                    "objective": {"value": cost, "unit": "USD"},
                    "solution": {"cost_breakdown": costs,
                                 "verification": {"feasible": True, "violations": []}},
                    "simulation": simulation, "reference": {"objective_value": cost},
                    "quantum": {"qubits": 5, "decision_qubits": 5, "counts": {"00101": 16}},
                    "runtime_seconds": 0,
                }
                Path("results.json").write_text(json.dumps(result), encoding="utf-8")
            """), encoding="utf-8")

        def review(_workspace, runs, **_kwargs):
            if not reached["test"].is_set():
                pause_stage("test")
            prices = []
            issues = []
            for run in runs:
                price = run.controls.get(tariff, 0.20)
                prices.append(price)
                result = run.results
                charged = sum(event["battery_end_kwh"] - event["battery_start_kwh"]
                              for van in result["simulation"]["vans"] for event in van["events"]
                              if event["type"] == "charge")
                if (result["controls"][tariff] != price
                        or not isclose(result["solution"]["cost_breakdown"]["electricity"], charged * price)
                        or not isclose(result["objective"]["value"], fixed_cost + charged * price)):
                    issues.append(SemanticIssue(
                        file="results", requirement="Use the effective tariff.",
                        problem="The electricity cost uses a different tariff.", fix="Recalculate the cost.",
                    ))
            reviewed_tariffs.append(prices)
            return SemanticReview(
                verdict="falls_short" if issues else "matches",
                summary="Checked the requested tariff against energy and cost.",
                checks_done=["Recomputed charging costs from battery changes."], issues=issues, model="fixture",
            )

        approves = FinalCheck(name="Fixture review", returns_to="build", verdict="matches",
                              summary="Approved the finite fleet plan.", issues=[])
        problem_approval = FinalCheck(name="Fixture problem review", returns_to="init", verdict="matches",
                                      summary="Approved the prompt interpretation.", issues=[])
        plan_approval = FinalCheck(name="Fixture plan review", returns_to="plan", verdict="matches",
                                   summary="Approved the finite fleet plan.", issues=[])
        needs_repair = FinalCheck(
            name="Fixture review", returns_to="build", verdict="falls_short",
            summary="Found an issue in the solver.", issues=["Recheck the charging cost calculation."],
        )
        final_reviews = iter([needs_repair, approves])

        def verify(*_args, **_kwargs):
            pause_stage("verify")
            return next(final_reviews)

        views = {}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.dict(os.environ, {"QUANTUM_BACKEND_URL": "", "GEMINI_API_KEY": "fixture-key"}),
                patch("server.VIEWS", views),
                patch("pipeline_live.planner_session", return_value=nullcontext(object())),
                patch("backend.workspace.init_workspace", side_effect=lambda *args, **kwargs: init_workspace(
                    *args, root=root, **kwargs,
                )),
                patch("backend.workspace.open_workspace", side_effect=lambda workspace_id: open_workspace(
                    workspace_id, root=root,
                )),
                patch("backend.pipeline.parse_problem_statement", side_effect=parse),
                patch("backend.pipeline.run_planning", side_effect=plan) as planner,
                patch("backend.pipeline.run_build_phase", side_effect=build),
                patch("backend.pipeline.check_problem_against_prompt", return_value=problem_approval),
                patch("backend.pipeline.check_plan_against_problem", return_value=plan_approval),
                patch("backend.pipeline.check_implementation_against_plan", side_effect=verify),
                patch("backend.testing.evaluate_semantics", side_effect=review),
                patch("backend.semantics.evaluate_semantics", side_effect=review),
            ):
                with self.request("/api/config") as response:
                    self.assertTrue(json.load(response)["pipeline_backend_configured"])
                queued = self.start_pipeline(prompt)
                self.assertEqual(queued["history"], [])
                job_id = queued["id"]
                try:
                    for index, stage in enumerate(stages):
                        self.assertTrue(reached[stage].wait(4), f"Pipeline did not reach {stage}.")
                        snapshot = self.read_pipeline(job_id)
                        self.assertEqual((snapshot["status"], snapshot["stage"], snapshot["attempt"]),
                                         ("running", stage, 1))
                        self.assertEqual([visit["stage"] for visit in snapshot["history"]], list(stages[:index + 1]))
                        self.assertEqual(snapshot["history"][-1]["status"], "running")
                        self.assertNotIn("result", snapshot)
                        if stage == "init":
                            self.assertEqual(self.start_pipeline(prompt)["id"], job_id)
                        proceed[stage].set()
                finally:
                    for event in proceed.values():
                        event.set()
                    snapshot = self.wait_for_pipeline(job_id)
                self.assertEqual(snapshot["status"], "completed", snapshot)
                self.assertEqual(
                    [(visit["stage"], visit["attempt"], visit["status"]) for visit in snapshot["history"]],
                    [(stage, 1, "completed") for stage in stages[:-1]] + [
                        ("verify", 1, "revising"), ("build", 2, "completed"),
                        ("test", 2, "completed"), ("verify", 2, "completed"),
                    ],
                )
                first_verification = snapshot["history"][4]
                self.assertTrue(any(
                    "Recheck the charging cost calculation." in step.get("detail", "")
                    for step in first_verification["steps"]
                ), first_verification)
                final_step = snapshot["history"][-1]["steps"][-1]
                self.assertEqual((final_step["label"], final_step["status"]),
                                 ("Publish the verified schedule", "completed"))
                first = snapshot["result"]
                workspace = open_workspace(first["plan_id"], root=root)
                baseline = {name: workspace.resolve(name).read_bytes() for name in ("results.json", "handoff.json")}

                updated = self.run_plan("live", view_id=first["view_id"], variables={tariff: 0.30})
                message = self.assert_run_error("live", 422, view_id=updated["view_id"], variables={tariff: 0.40})
                self.assertIn("independent review", message)
                restored = self.run_plan("live", view_id=updated["view_id"], variables={tariff: 0.20})
                self.assertIs(restored["cached"], True)
                self.assertEqual(restored["evaluation"], first["evaluation"])
                self.assertEqual(views, {view["view_id"]: view for view in (first, updated, restored)})
                for name, before in baseline.items():
                    self.assertEqual(workspace.resolve(name).read_bytes(), before)
                self.assertEqual(list(root.iterdir()), [workspace.path])

        self.assertEqual(planner.call_count, 1)
        self.assertEqual(first["problem"]["original_prompt"], prompt)
        self.assertEqual(first["effective_values"], {tariff: 0.20})
        control, = first["variables"]
        self.assertEqual(
            (control["id"], control["minimum"], control["maximum"], control["step"], control["group"]),
            (tariff, 0.10, 0.50, 0.10, "Costs"),
        )
        self.assertEqual(updated["run_id"], first["run_id"])
        self.assertEqual(updated["plan_id"], first["plan_id"])
        self.assertNotEqual(updated["attempt_id"], first["attempt_id"])
        self.assertEqual(updated["effective_values"], {tariff: 0.30})
        self.assertAlmostEqual(updated["evaluation"]["costs"]["electricity"], 1.50)
        self.assertAlmostEqual(updated["evaluation"]["objective_value"], first["evaluation"]["objective_value"] + 0.50)
        self.assertEqual(reviewed_tariffs, [[0.20, 0.30], [0.30], [0.40]])

    def test_pipeline_input_failure_remains_visible_in_its_stage(self):
        def needs_input(_request, *, on_stage, on_progress):
            on_stage("init")
            on_stage("plan")
            on_progress("Review the prompt", status="running", detail="Fixture credential fixture-key")
            raise UnverifiedResult("Which depot is the starting point?")

        with (
            patch.dict(os.environ, {"QUANTUM_BACKEND_URL": "", "GEMINI_API_KEY": "fixture-key"}),
            patch("server.run_plan", side_effect=needs_input) as runner,
        ):
            job_id = self.start_pipeline("An EV fleet with an unspecified depot.")["id"]
            snapshot = self.wait_for_pipeline(job_id)
        runner.assert_called_once()
        self.assertEqual((snapshot["status"], snapshot["stage"], snapshot["attempt"]), ("failed", "plan", 1))
        self.assertEqual(snapshot["error"], "Which depot is the starting point?")
        self.assertEqual(snapshot["history"][-1]["status"], "failed")
        self.assertEqual(snapshot["history"][-1]["steps"], [
            {"id": 1, "label": "Review the prompt", "status": "failed", "detail": "Fixture credential [redacted]"},
        ])
        self.assertNotIn("result", snapshot)

    def test_history_browses_disk_workspaces_and_reopens_a_verified_schedule(self):
        from backend.workspace import init_workspace

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"QUANTUM_WORKSPACES_ROOT": directory}):
            def create(prompt, created_at, **metadata):
                workspace = init_workspace(prompt, **metadata)
                meta = workspace.resolve("meta.json")
                details = workspace.meta
                details["created_at"] = created_at
                meta.write_text(json.dumps(details), encoding="utf-8")
                return workspace

            unfinished = create("Schedule four vans without a depot.", "2026-11-13T12:00:00+00:00", scene_type="ev_fleet")
            unfinished.resolve("PLAN.md").write_text("# Early plan", encoding="utf-8")
            unfinished.resolve("private-context.txt").write_text("Scratch notes", encoding="utf-8")
            generic = create("A separate graph problem from the CLI.", "2026-11-14T12:00:00+00:00")
            generic.resolve("handoff.json").write_text(json.dumps({
                "workspace_id": generic.id, "decision": "GO",
                "problem": {"title": "Graph optimization", "summary": "Graph solver output."},
                "results": {"status": "ok", "solution": {"vertices": [1, 2]}},
            }), encoding="utf-8")

            def save_verified(workspace, title, charge):
                original = reference_handoff({CONTROL_ID: charge})
                evaluation = original["result"]["evaluation"]
                saved = {
                    "workspace_id": workspace.id, "decision": "GO",
                    "problem": {"title": title, "summary": "Verified fleet schedule."},
                    "controls": original["controls"],
                    "results": {
                        "status": "ok", "controls": {CONTROL_ID: charge},
                        "simulation": original["result"]["simulation"],
                        "objective": {"unit": "USD", "value": evaluation["objective_value"]},
                        "solution": {"verification": {"feasible": True, "violations": []},
                                     "cost_breakdown": evaluation["costs"]},
                        "quantum": {"qubits": 5, "decision_qubits": 5, "counts": {"00101": 16}},
                    },
                }
                workspace.resolve("handoff.json").write_text(json.dumps(saved), encoding="utf-8")
                return evaluation["objective_value"]

            solved = create("Find the lowest-cost routes for the example EV fleet.",
                            "2026-11-15T12:00:00+00:00", scene_type="ev_fleet")
            earlier = create("Route the fleet with a lower starting charge.", "2026-11-14T22:00:00+00:00")
            solved_cost = save_verified(solved, "Morning fleet routes", 35)
            earlier_cost = save_verified(earlier, "Earlier EV run from the CLI", 20)
            (Path(directory) / "unrelated-scratch").mkdir()

            with (
                patch("server.PIPELINE_RUNS", {}),
                patch("server.VIEWS", {}) as views,
                patch("server.pipeline_handoff") as provider,
            ):
                with self.request("/api/workspaces") as response:
                    runs = json.load(response)["workspaces"]
                self.assertEqual([(item["id"], item["status"], item["can_open"]) for item in runs], [
                    (solved.id, "verified", True), (earlier.id, "verified", True), (generic.id, "saved", False),
                    (unfinished.id, "incomplete", False),
                ])
                self.assertEqual(runs[0]["title"], "Morning fleet routes")
                self.assertNotIn("prompt", runs[0])

                with self.request(f"/api/workspaces/{generic.id}") as response:
                    detail = json.load(response)
                self.assertEqual(detail["prompt"], generic.prompt)
                self.assertIn("handoff", [artifact["key"] for artifact in detail["artifacts"]])

                with self.request(f"/api/workspaces/{unfinished.id}/artifacts/plan") as response:
                    self.assertEqual(response.read(), b"# Early plan")
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                with self.assertRaises(HTTPError) as forbidden:
                    self.request(f"/api/workspaces/{unfinished.id}/artifacts/private-context.txt")
                self.assertEqual(forbidden.exception.code, 404)
                forbidden.exception.close()
                with self.assertRaises(HTTPError) as incompatible:
                    self.request(f"/api/workspaces/{generic.id}/open", {})
                self.assertEqual(incompatible.exception.code, 422)
                incompatible.exception.close()

                for workspace, expected_cost in ((solved, solved_cost), (earlier, earlier_cost)):
                    with self.request(f"/api/workspaces/{workspace.id}/open", {}) as response:
                        result = json.load(response)
                    self.assertEqual(result["plan_id"], workspace.id)
                    self.assertEqual(result["problem"]["original_prompt"], workspace.prompt)
                    self.assertEqual(result["evaluation"]["objective_value"], expected_cost)
                    self.assertEqual(result["source"]["workspace_id"], workspace.id)
                    self.assertEqual(views[result["view_id"]], result)
                self.assertEqual(len(views), 2)
                provider.assert_not_called()

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
