"""HTTP coverage for structured input, settings reuse, and source persistence."""

import copy
import json
import os
import threading
import unittest
from collections import OrderedDict
from tempfile import TemporaryDirectory
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from backend.workspace import init_workspace
from pipeline_live import project_result
from scenario import effective_scenario, example_scenario, scenario_digest, scenario_prompt
from server import Handler
from test_scenario import direct_result
from workspace_runs import list_saved_runs, saved_run


CONTROL = {"key": "driving_cost_per_km", "type": "number", "label": "Driving cost",
           "default": 0.3, "minimum": 0.1, "maximum": 1, "step": 0.1, "rerun_mode": "rebuild"}


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class ScenarioHttpTests(unittest.TestCase):
    def setUp(self):
        self.source = example_scenario()
        self.calls = []
        for name, value in (("VIEWS", {}), ("VIEW_BACKENDS", {}), ("SOLVER_RESULTS", OrderedDict()),
                            ("PIPELINE_RUNS", {})):
            replacement = patch("server." + name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for replacement in (
            patch.dict(os.environ, {"QUANTUM_BACKEND_URL": "", "QUANTUM_WORKSPACES_ROOT": self.directory.name}),
            patch("server.native_pipeline_configured", return_value=True),
            patch("server.planner_configured", return_value=True),
            patch("server.native_cache_revision", return_value="fixture-policy"),
            patch("server.pipeline_handoff", side_effect=self.solve),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def request(self, path, payload=None):
        encoded = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(self.url + path, encoded, {"Content-Type": "application/json"}), timeout=5) as response:
            return response.status, json.load(response)

    def solve(self, prompt, values, previous, attempt_id, *, scenario=None, on_stage=None, on_progress=None):
        self.calls.append({"scenario": copy.deepcopy(scenario), "values": dict(values), "previous": previous})
        source = scenario if scenario is not None else self.source
        effective = effective_scenario(source, [CONTROL], values)
        output = direct_result(effective)
        output["controls"] = values or {CONTROL["key"]: CONTROL["default"]}
        if on_stage:
            on_stage("init")
        if on_progress:
            on_progress("Validate fixture schedule", status="completed")
        identity = {"run_id": previous["run_id"] if previous else "scenario-test-run",
                    "plan_id": previous["plan_id"] if previous else "scenario-test-plan",
                    "attempt_id": attempt_id}
        artifact = {"controls": [CONTROL], "problem": {"title": source["title"], "summary": "HTTP fixture"}}
        return project_result(artifact, output, prompt, identity, source_scenario=source)

    def start(self, scenario):
        status, job = self.request("/api/pipeline-runs", {"scenario": scenario})
        self.assertEqual(status, 202)
        deadline = monotonic() + 5
        while job["status"] in ("queued", "running") and monotonic() < deadline:
            sleep(0.01)
            _, job = self.request("/api/pipeline-runs/" + job["id"])
        self.assertEqual(job["status"], "completed", job)
        return job

    def test_structured_run_recalculates_numeric_settings_and_reuses_verified_cache(self):
        job = self.start(self.source)
        first = job["result"]
        self.assertEqual(job["prompt"], scenario_prompt(self.source))
        self.assertEqual(first["scenario_context"]["effective_scenario"], self.source)
        self.assertEqual(len(first["simulation"]["depots"]), 2)
        self.assertEqual(first["simulation"]["vans"][0]["delivery_ids"], ["D1", "D2"])
        payload = {"mode": "live", "view_id": first["view_id"], "variables": {CONTROL["key"]: 0.5}}
        _, changed = self.request("/api/run", payload)
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self.calls[1]["scenario"])
        self.assertEqual(changed["plan_id"], first["plan_id"])
        self.assertNotEqual(changed["attempt_id"], first["attempt_id"])
        self.assertEqual(changed["scenario_context"]["effective_scenario"]["costs"]["driving_per_km"], 0.5)
        self.assertGreater(changed["evaluation"]["objective_value"], first["evaluation"]["objective_value"])
        _, cached = self.request("/api/run", payload)
        self.assertTrue(cached["cached"])
        self.assertEqual(cached["scenario_context"], changed["scenario_context"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.source["costs"]["driving_per_km"], 0.3)

    def test_bad_structured_inputs_and_topology_on_apply_never_call_solver(self):
        bad = copy.deepcopy(self.source)
        bad["deliveries"][0]["vehicle_id"] = "V99"
        for payload in ({"scenario": bad}, {"scenario": self.source, "prompt": "conflicting"}):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/pipeline-runs", payload)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.calls, [])
        first = self.start(self.source)["result"]
        with self.assertRaises(HTTPError) as error:
            self.request("/api/run", {"mode": "live", "view_id": first["view_id"],
                                      "scenario": self.source, "variables": {}})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(len(self.calls), 1)

    def test_missing_backend_reports_setup_without_invoking_solver(self):
        with patch("server.native_pipeline_configured", return_value=False):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/pipeline-runs", {"scenario": self.source})
        self.assertEqual(error.exception.code, 503)
        self.assertEqual(self.calls, [])

    def test_incomplete_saved_builder_run_remains_editable_and_downloadable(self):
        workspace = init_workspace(scenario_prompt(self.source), root=Path(self.directory.name), scene_type="ev_fleet")
        workspace.resolve("scenario.json").write_text(json.dumps(self.source))
        metadata = workspace.meta
        metadata["scenario_digest"] = scenario_digest(self.source)
        workspace.resolve("meta.json").write_text(json.dumps(metadata))
        self.assertTrue(list_saved_runs()[0]["can_edit"])
        run = saved_run(workspace.id)
        self.assertFalse(run["can_open"])
        self.assertEqual(run["scenario"], self.source)
        _, download = self.request(f"/api/workspaces/{workspace.id}/artifacts/scenario")
        self.assertEqual(download, self.source)


if __name__ == "__main__":
    unittest.main()
