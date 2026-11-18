import json
import unittest
from contextlib import nullcontext
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.workspace import init_workspace
from ev_reference import PROMPT, reference_handoff
from fleet_handoff import UnverifiedResult, normalize_handoff
from pipeline_live import pipeline_handoff, project_result


@unittest.skipUnless(find_spec("pydantic"), "The planner dependencies are required.")
class RecalculationIntegrityTests(unittest.TestCase):
    def setUp(self):
        from backend.runner import RunOutcome
        from backend.semantics import SemanticReview

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = init_workspace(PROMPT, root=Path(directory.name))
        self.workspace.resolve("main.py").write_text("print('approved solver')\n", encoding="utf-8")
        self.workspace.resolve("data.json").write_text('{"cost": 1}', encoding="utf-8")
        original = reference_handoff({})
        result = original["result"]
        self.values = result["effective_values"]
        self.output = {
            "status": "ok", "controls": self.values,
            "objective": {"value": result["evaluation"]["objective_value"], "unit": "USD"},
            "solution": {"verification": {"feasible": True, "violations": []},
                         "cost_breakdown": result["evaluation"]["costs"]},
            "simulation": result["simulation"],
            "quantum": {"qubits": 5, "decision_qubits": 5, "counts": {"00101": 16}},
            "reference": {}, "runtime_seconds": 0,
        }
        artifact = {
            "workspace_id": self.workspace.id, "decision": "GO", "controls": original["controls"],
            "problem": {"title": original["problem"]["title"], "summary": original["problem"]["statement"]},
            "results": self.output,
        }
        self.workspace.resolve("handoff.json").write_text(json.dumps(artifact), encoding="utf-8")
        identity = {"run_id": "run", "plan_id": self.workspace.id, "attempt_id": "first"}
        self.previous = normalize_handoff(project_result(artifact, self.output, PROMPT, identity), "live", PROMPT)
        self.outcome = RunOutcome(
            command="python main.py", controls=self.values, exit_code=0, timed_out=False,
            seconds=0.1, stdout_tail="", stderr_tail="", results=self.output, issues=[],
        )
        self.review = SemanticReview(verdict="matches", summary="Approved", checks_done=[], issues=[], model="test")

    def recalculate(self, worker, review):
        with (
            patch("backend.workspace.open_workspace", return_value=self.workspace),
            patch("backend.runner.run_solver", side_effect=worker),
            patch("pipeline_live.planner_session", return_value=nullcontext(object())),
            patch("backend.semantics.evaluate_semantics", side_effect=review) as judge,
        ):
            result = pipeline_handoff(PROMPT, self.values, self.previous, "second")
        return result, judge

    def test_rejects_worker_changes_before_semantic_review(self):
        for filename in ("main.py", "data.json"):
            with self.subTest(filename=filename):
                original = self.workspace.resolve(filename).read_bytes()

                def mutate(attempt, values):
                    attempt.resolve(filename).write_text("changed during execution\n", encoding="utf-8")
                    return self.outcome

                with patch("backend.semantics.evaluate_semantics") as judge:
                    with self.assertRaisesRegex(UnverifiedResult, "inputs changed during recalculation"):
                        self.recalculate(mutate, judge)
                judge.assert_not_called()
                self.assertEqual(self.workspace.resolve(filename).read_bytes(), original)

    def test_rejects_inputs_changed_during_semantic_review(self):
        original = self.workspace.resolve("data.json").read_bytes()

        def mutate(attempt, *_args, **_kwargs):
            attempt.resolve("data.json").write_text('{"cost": 100}', encoding="utf-8")
            return self.review

        with self.assertRaisesRegex(UnverifiedResult, "inputs changed during review"):
            self.recalculate(lambda *_args: self.outcome, mutate)
        self.assertEqual(self.workspace.resolve("data.json").read_bytes(), original)

    def test_unchanged_recalculation_still_returns_verified_results(self):
        result, judge = self.recalculate(lambda *_args: self.outcome, lambda *_args, **_kwargs: self.review)
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["result"]["effective_values"], self.values)
        self.assertEqual(result["result"]["evaluation"]["objective_value"], self.output["objective"]["value"])
        judge.assert_called_once()


if __name__ == "__main__":
    unittest.main()
