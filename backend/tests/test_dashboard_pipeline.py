import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import patch

from backend.build import build_contract
from backend.final_check import FinalCheck, FinalDecision
from backend.pipeline import run_pipeline
from backend.plan import PLAN_JSON, PlanControl
from backend.runner import RESULTS_JSON, run_solver
from backend.semantics import SemanticReview
from backend.testing import control_variants
from backend.testing import test_once as run_test_once
from backend.workspace import init_workspace


class DashboardPipelineTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = init_workspace("Schedule the fleet", root=self.root, scene_type="ev_fleet")
        self.workspace.resolve("main.py").write_text(dedent("""
            import argparse
            import json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--electricity_rate", type=float, default=0.2)
            args = parser.parse_args()
            Path("results.json").write_text(json.dumps({
                "status": "ok",
                "controls": {"electricity_rate": args.electricity_rate},
                "objective": {"value": args.electricity_rate},
                "solution": {"selected": ["route"]},
                "reference": None,
                "quantum": {"qubits": 1, "counts": {"1": 5}},
                "runtime_seconds": 0.1,
            }), encoding="utf-8")
        """), encoding="utf-8")

    def test_build_contract_only_requires_a_scene_for_dashboard_workspaces(self):
        planning = SimpleNamespace(max_qubits=5, worker_seconds=10)
        regular = init_workspace("Optimize something", root=self.root)

        self.assertEqual(self.workspace.meta["scene_type"], "ev_fleet")
        self.assertNotIn("scene_type", regular.meta)
        self.assertIn("solution.cost_breakdown", build_contract(planning, self.workspace))
        self.assertNotIn("solution.cost_breakdown", build_contract(planning, regular))

    def test_validator_receives_overrides_and_rejects_unusable_output(self):
        def validate(results, overrides):
            self.assertEqual(results["controls"], {"electricity_rate": 0.25})
            self.assertEqual(overrides, {"electricity_rate": 0.25})
            raise ValueError("Missing EV simulation.")

        outcome = run_solver(self.workspace, {"electricity_rate": 0.25}, validate_results=validate)

        self.assertFalse(outcome.passed)
        self.assertEqual(outcome.issues, ["Missing EV simulation."])

    def test_stage_updates_follow_build_repairs_and_final_review_retries(self):
        problem = SimpleNamespace(
            is_optimization_problem=True, title="Schedule the fleet", quantum=SimpleNamespace(method="QAOA"),
        )
        planning = SimpleNamespace(plan=SimpleNamespace(selected=SimpleNamespace(name="Fleet solver")), attempts=1)
        failed_review = SemanticReview(verdict="falls_short", summary="Revise", checks_done=[], issues=[], model="test")
        passed_review = SemanticReview(verdict="matches", summary="Ready", checks_done=[], issues=[], model="test")
        failed_check = FinalCheck(
            name="Problem", returns_to="init", verdict="falls_short", summary="Revise", issues=["Check departure time"],
        )
        decisions = [
            FinalDecision(decision="NO_GO", failed_stage="init", checks=[failed_check], model="test"),
            FinalDecision(decision="GO", failed_stage=None, checks=[], model="test"),
        ]
        stages = []
        with (
            patch("backend.pipeline.parse_problem_statement"),
            patch("backend.pipeline.load_problem_statement", return_value=problem),
            patch("backend.pipeline.run_planning", return_value=planning),
            patch("backend.pipeline.run_build_phase"),
            patch("backend.testing.run_build_phase"),
            patch("backend.testing.evaluate_semantics", side_effect=[failed_review, passed_review, passed_review]),
            patch("backend.pipeline.run_final_checks", side_effect=decisions),
            patch("backend.pipeline.write_handoff", return_value={"decision": "GO"}),
        ):
            result = run_pipeline(self.workspace, on_stage=stages.append)

        self.assertEqual(result["decision"], "GO")
        self.assertEqual(stages, [
            "init", "plan", "build", "test", "build", "test", "verify",
            "init", "plan", "build", "test", "verify",
        ])

    def test_test_phase_keeps_the_validated_default_result_after_a_range_probe(self):
        replayed = []

        def validate(results, overrides):
            replayed.append((results["controls"]["electricity_rate"], overrides))

        review = SemanticReview(verdict="matches", summary="Ok", checks_done=[], issues=[], model="test")
        with (
            patch("backend.testing.control_variants", return_value=[{"electricity_rate": 0.25}]),
            patch("backend.testing.evaluate_semantics", return_value=review),
        ):
            outcome = run_test_once(self.workspace, validate_results=validate)

        self.assertTrue(outcome.passed)
        self.assertEqual(replayed, [(0.2, {}), (0.25, {"electricity_rate": 0.25})])
        self.assertEqual(json.loads(self.workspace.resolve(RESULTS_JSON).read_text(encoding="utf-8")), outcome.runs[0].results)

    def test_automatic_probes_include_numeric_ranges_and_implicit_booleans(self):
        self.workspace.resolve(PLAN_JSON).touch()
        controls = [
            PlanControl(
                key="electricity_rate", label="Electricity", type="number",
                minimum=0.2, maximum=0.31, default=0.2, step=0.05, rerun_mode="rebuild",
            ),
            PlanControl(
                key="allow_waiting", label="Allow waiting", type="boolean", default=True, rerun_mode="rebuild",
            ),
        ]
        planning = SimpleNamespace(plan=SimpleNamespace(controls=controls))
        with patch("backend.testing.load_plan", return_value=planning):
            self.assertEqual(control_variants(self.workspace), [{"electricity_rate": 0.25}, {"allow_waiting": False}])

    def test_range_probe_prefers_an_interior_charge_that_differs_from_the_default(self):
        self.workspace.resolve(PLAN_JSON).touch()
        for maximum, step, default, expected in ((35, 1, 35, 27), (35, None, 27, 28), (21, 1, 21, 20)):
            charge = PlanControl(
                key="van_2_initial_charge_pct", label="Van 2 starting charge", type="integer",
                minimum=20, maximum=maximum, step=step, default=default, rerun_mode="rebuild",
            )
            planning = SimpleNamespace(plan=SimpleNamespace(controls=[charge]))
            with self.subTest(maximum=maximum, step=step, default=default):
                with patch("backend.testing.load_plan", return_value=planning):
                    self.assertEqual(control_variants(self.workspace), [{charge.key: expected}])


if __name__ == "__main__":
    unittest.main()
