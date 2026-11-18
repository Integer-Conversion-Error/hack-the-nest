"""Exercise builder inputs across native planning, worker reruns, and history."""

import copy
import json
import os
import textwrap
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_live import load_scenario, pipeline_handoff, project_result

from backend.files import FileToolError, delete_file, update_file
from backend.plan import PlanControl
from backend.planning import plan_fidelity_fingerprint
from backend.semantics import SemanticReview
from backend.workspace import init_workspace
from fleet_handoff import InvalidHandoff, normalize_handoff
from scenario import effective_scenario, example_scenario, scenario_digest, scenario_prompt
from test_scenario import direct_result, numeric_control
from workspace_runs import SavedResultUnavailable, list_saved_runs, open_saved_run, saved_run


SPEED = "driving_speed_km_per_hour"


class ScenarioPipelineTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict(os.environ, {"QUANTUM_WORKSPACES_ROOT": str(self.root), "MODEL_PROVIDER": "gemini"})
        environment.start()
        self.addCleanup(environment.stop)
        self.scenario = example_scenario()
        self.prompt = scenario_prompt(self.scenario)
        self.control = numeric_control(SPEED, 30, 10, 60, 5)
        self.controls = [self.control]
        self.output = direct_result(self.scenario)
        self.output["controls"] = {SPEED: 30}
        self.workspace = init_workspace(self.prompt, root=self.root, scene_type="ev_fleet")
        self.write_source(self.workspace)
        self.artifact = {
            "workspace_id": self.workspace.id, "decision": "GO", "controls": self.controls,
            "problem": {"title": self.scenario["title"], "summary": "Dispatch the assigned deliveries."},
            "results": self.output,
        }
        self.workspace.resolve("handoff.json").write_text(json.dumps(self.artifact), encoding="utf-8")
        self.identity = {"run_id": "builder-run", "plan_id": self.workspace.id, "attempt_id": "first"}

    def write_source(self, workspace):
        workspace.resolve("scenario.json").write_text(json.dumps(self.scenario), encoding="utf-8")
        metadata = workspace.meta
        metadata["scenario_digest"] = scenario_digest(self.scenario)
        workspace.resolve("meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    def test_initial_pipeline_callback_verifies_default_and_partial_control_overlays(self):
        planning = SimpleNamespace(plan=SimpleNamespace(controls=[PlanControl.model_validate(self.control)]))
        problem = SimpleNamespace(title=self.scenario["title"], summary="Assigned deliveries")
        seen = []

        def run_pipeline(workspace, *, validate_results, **_kwargs):
            self.assertEqual(load_scenario(workspace), self.scenario)
            with self.assertRaises(FileToolError):
                update_file(workspace, "scenario.json", "{}")
            validate_results(copy.deepcopy(self.output), {})
            expected = effective_scenario(self.scenario, self.controls, {SPEED: 40})
            changed = direct_result(expected)
            changed["controls"] = {SPEED: 40}
            validate_results(changed, {SPEED: 40})
            stale = copy.deepcopy(self.output)
            stale["controls"] = {SPEED: 40}
            with self.assertRaisesRegex(ValueError, "drive duration"):
                validate_results(stale, {SPEED: 40})
            seen.append(workspace)
            return {**self.artifact, "workspace_id": workspace.id}

        with (
            patch("pipeline_live.planner_session", return_value=nullcontext(object())),
            patch("backend.plan.load_plan", return_value=planning),
            patch("backend.problem_statement.load_problem_statement", return_value=problem),
            patch("backend.pipeline.run_pipeline", side_effect=run_pipeline),
        ):
            handoff = pipeline_handoff(self.prompt, {}, None, "initial", scenario=self.scenario)
        self.assertEqual(len(seen), 1)
        self.assertEqual(handoff["scenario_context"]["effective_scenario"], self.scenario)
        self.assertEqual(handoff["scenario_context"]["base_digest"], scenario_digest(self.scenario))
        self.assertEqual(handoff["schema_version"], 2)

    def write_worker(self, stale=False):
        """Run a real subprocess with synthetic contract data, without simulator/model calls."""
        output = copy.deepcopy(self.output)
        output["quantum"] = {"qubits": 1, "decision_qubits": 1, "counts": {"1": 1},
                             "method": "Synthetic worker contract test; no quantum execution"}
        self.workspace.resolve("fixture.json").write_text(json.dumps(output), encoding="utf-8")
        code = textwrap.dedent("""
            import argparse
            import json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument('--driving_speed_km_per_hour', type=float, default=30)
            args = parser.parse_args()
            output = json.loads(Path('fixture.json').read_text())
            output['controls']['driving_speed_km_per_hour'] = args.driving_speed_km_per_hour
            if UPDATE_EVENTS:
                for scene in (output['simulation'], output['reference']['simulation']):
                    for van in scene['vans']:
                        minute = 0
                        for event in van['events']:
                            duration = event['end_min'] - event['start_min']
                            if event['type'] == 'drive':
                                duration *= 30 / args.driving_speed_km_per_hour
                            event['start_min'], event['end_min'] = minute, minute + duration
                            minute += duration
            Path('results.json').write_text(json.dumps(output))
        """).replace("UPDATE_EVENTS", str(not stale))
        self.workspace.resolve("main.py").write_text(code, encoding="utf-8")

    def recalculate(self):
        original = project_result(self.artifact, self.output, self.prompt, self.identity, workspace=self.workspace)
        previous = normalize_handoff(original, "live", self.prompt)
        with patch("pipeline_live.planner_session", return_value=nullcontext(object())):
            return pipeline_handoff(self.prompt, {SPEED: 40}, previous, "second")

    def test_settings_reuse_executes_worker_and_edits_the_effective_attempt(self):
        self.write_worker()
        before = self.workspace.resolve("scenario.json").read_bytes()
        review = SemanticReview(verdict="matches", summary="Fixture matches.", checks_done=[], issues=[], model="test")
        with patch("backend.semantics.evaluate_semantics", return_value=review) as judge:
            result = self.recalculate()
        context = result["scenario_context"]
        self.assertEqual(context["effective_scenario"]["timing"]["speed_kmh"], 40)
        self.assertEqual(context["base_digest"], scenario_digest(self.scenario))
        self.assertNotEqual(context["effective_digest"], context["base_digest"])
        self.assertEqual(result["result"]["effective_values"], {SPEED: 40})
        self.assertEqual(self.workspace.resolve("scenario.json").read_bytes(), before)
        self.assertFalse(self.workspace.resolve("results.json").exists())
        self.assertEqual([entry for entry in self.root.iterdir() if "-attempt-" in entry.name], [])
        judge.assert_called_once()
        reopened = open_saved_run(self.workspace.id)
        self.assertEqual(reopened["scenario_context"]["effective_scenario"]["timing"]["speed_kmh"], 30)

    def test_claimed_control_change_with_stale_physics_is_rejected_before_review(self):
        self.write_worker(stale=True)
        with patch("backend.semantics.evaluate_semantics") as judge:
            with self.assertRaisesRegex(ValueError, "drive duration"):
                self.recalculate()
        judge.assert_not_called()

    def test_source_digest_and_prompt_changes_reject_saved_opening_but_preserve_listing(self):
        original_source = self.workspace.resolve("scenario.json").read_bytes()
        for mutation in ("missing", "changed", "prompt", "unidentified", "symlink"):
            with self.subTest(mutation=mutation):
                self.workspace.resolve("scenario.json").write_bytes(original_source)
                self.workspace.resolve("prompt.md").write_text(self.prompt, encoding="utf-8")
                metadata = self.workspace.meta
                metadata["scenario_digest"] = scenario_digest(self.scenario)
                if mutation == "missing":
                    self.workspace.resolve("scenario.json").unlink()
                elif mutation == "changed":
                    altered = copy.deepcopy(self.scenario)
                    altered["vehicles"][0]["initial_charge_pct"] = 80
                    self.workspace.resolve("scenario.json").write_text(json.dumps(altered), encoding="utf-8")
                elif mutation == "prompt":
                    self.workspace.resolve("prompt.md").write_text("Different scenario", encoding="utf-8")
                elif mutation == "unidentified":
                    del metadata["scenario_digest"]
                else:
                    external = self.root / "outside-scenario.json"
                    external.write_bytes(original_source)
                    self.workspace.resolve("scenario.json").unlink()
                    (self.workspace.path / "scenario.json").symlink_to(external)
                self.workspace.resolve("meta.json").write_text(json.dumps(metadata), encoding="utf-8")
                with self.assertRaises(InvalidHandoff):
                    load_scenario(self.workspace)
                runs = list_saved_runs()
                self.assertEqual(len(runs), 1)
                self.assertFalse(runs[0]["can_edit"])
                self.assertFalse(runs[0]["can_open"])
                with self.assertRaises(SavedResultUnavailable):
                    open_saved_run(self.workspace.id)

    def test_history_preserves_source_and_context_and_failed_runs_remain_editable(self):
        reopened = open_saved_run(self.workspace.id)
        self.assertEqual(reopened["scenario_context"]["effective_scenario"], self.scenario)
        self.assertEqual(reopened["provenance"], "recorded")
        detail = saved_run(self.workspace.id)
        self.assertTrue(detail["can_edit"])
        self.assertTrue(detail["can_open"])
        self.assertEqual(detail["scenario"], self.scenario)
        self.assertIn("scenario", [artifact["key"] for artifact in detail["artifacts"]])
        with self.assertRaises(FileToolError):
            delete_file(self.workspace, "scenario.json")
        self.workspace.resolve("handoff.json").unlink()
        unfinished = saved_run(self.workspace.id)
        self.assertTrue(unfinished["can_edit"])
        self.assertFalse(unfinished["can_open"])
        self.assertEqual(unfinished["scenario"], self.scenario)

    def test_full_scenario_evidence_changes_the_planning_approval_identity(self):
        problem = SimpleNamespace(quantum=SimpleNamespace(qubit_budget=None), model_dump=lambda **_kwargs: {"title": "Fleet"})
        plan = SimpleNamespace(model_dump=lambda **_kwargs: {"method": "QAOA"})
        with patch("backend.planning.load_problem_statement", return_value=problem):
            before = plan_fidelity_fingerprint(self.workspace, plan=plan, model="test")
            changed = copy.deepcopy(self.scenario)
            changed["deliveries"][1]["position"] = [1, 3]
            self.workspace.resolve("scenario.json").write_text(json.dumps(changed), encoding="utf-8")
            after = plan_fidelity_fingerprint(self.workspace, plan=plan, model="test")
        self.assertNotEqual(before, after)

    def test_only_native_verified_handoffs_can_attach_builder_edit_context(self):
        from server import live_result

        handoff = project_result(self.artifact, self.output, self.prompt, self.identity, workspace=self.workspace)
        with (
            patch("server.uuid4", return_value="first"),
            patch("server.planner_configured", return_value=True),
            patch("server.native_cache_revision", return_value=None),
            patch("server.backend_url", return_value=""),
            patch("server.pipeline_handoff", return_value=handoff),
        ):
            native, _ = live_result(self.prompt, {}, None, None)
        self.assertEqual(native["scenario_context"]["effective_scenario"], self.scenario)
        with (
            patch("server.uuid4", return_value="first"),
            patch("server.planner_configured", return_value=False),
            patch("server.backend_url", return_value="https://external.example"),
            patch("server.request_handoff", return_value=handoff),
        ):
            external, _ = live_result(self.prompt, {}, None, None)
        self.assertNotIn("scenario_context", external)
