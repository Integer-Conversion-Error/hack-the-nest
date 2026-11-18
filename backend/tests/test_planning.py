import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.model_provider import ModelRequestError, RunContext, model_session
from backend.model_types import ModelResponse
from backend.plan import Plan, PlanDraft, PlanReview, PlanRevision, Research, Source
from backend.planning import ReinterpretationRequired, plan_fidelity_fingerprint, run_planning
from backend.problem_statement import PROBLEM_JSON, ProblemStatement
from backend.workspace import init_workspace


class PlanningRepairTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = init_workspace("Original prompt marker", root=Path(directory.name))
        self.problem = ProblemStatement.model_validate({
            "is_optimization_problem": True, "title": "One bit", "summary": "Choose a bit",
            "problem_class": "binary optimization", "given_data": [], "decision_variables": ["x"],
            "objective_sense": "minimize", "objective_terms": [], "constraints": [],
            "quantum": {"method": "QAOA", "method_reason": "Binary encoding", "quantum_part": "Sample x"},
            "expected_output": ["Chosen bit"], "assumptions": [], "open_questions": [],
        })
        self.workspace.resolve(PROBLEM_JSON).write_text(self.problem.model_dump_json(), encoding="utf-8")
        self.plan = Plan.model_validate({
            "summary": "Sample one bit", "objective": "Minimize x", "constraints": [], "assumptions": [],
            "controls": [], "selected_solver": "one bit", "solver_examples": [{
                "name": "one bit", "method": "QAOA", "encoding": "One qubit for x", "classical_steps": [],
                "quantum_steps": ["Sample x"], "qubits": {"decision_qubits": 1, "auxiliary_qubits": 0},
                "verification": ["Check x"], "reference_method": "Enumerate both bits", "build_steps": ["Write main.py"],
            }],
        })
        self.draft = PlanDraft(status="ready", plan=self.plan)
        self.approval = PlanReview(verdict="approve")
        self.url = "https://example.test/encoding"
        self.research = Research(status="grounded", summary=f"Use one qubit. See {self.url}",
                                 sources=[Source(title="Encoding docs", url=self.url)])

    def run_plan(self, feedback=None, *, answers=(), previous_draft=None, client=None):
        with patch.dict(os.environ, {"MODEL_PROVIDER": "gemini"}):
            return run_planning(self.workspace, feedback, answers=answers, model="fixture", client=client,
                                max_retries=1, previous_draft=previous_draft)

    def test_transient_review_failure_retries_only_the_review(self):
        requests = []
        answers = iter([self.draft, ModelRequestError("Transient", retryable=True), self.approval])

        class Adapter:
            def generate(self, request, *, timeout):
                requests.append(request)
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return ModelResponse(text=answer.model_dump_json())

        context = RunContext(sleep=lambda _: None)
        with (
            patch.dict(os.environ, {"MODEL_PROVIDER": "gemini"}),
            patch("backend.planning._research", return_value=self.research) as research,
            model_session(Adapter(), context=context) as session,
        ):
            result = self.run_plan(client=session)

        research.assert_called_once()
        self.assertEqual([request.stage for request in requests], ["plan", "plan_review", "plan_review"])
        review = requests[-1].messages[0].text
        self.assertEqual(review.count(self.plan.model_dump_json()), 1)
        self.assertIn("S1 Encoding docs", review)
        self.assertNotIn(self.url, review)
        self.assertNotIn("Original prompt marker", review)
        self.assertEqual(result.research.sources[0].url, self.url)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(context.retries, 1)

    def test_feedback_edits_only_named_fields_and_keeps_evidence_and_answers(self):
        with (
            patch("backend.planning._research", return_value=self.research) as research,
            patch("backend.planning._structured", side_effect=[self.draft, self.approval]),
        ):
            original = self.run_plan(answers=("Use a fixed input.",))

        revised = PlanRevision.model_validate({"status": "ready", "edits": [{
            "op": "replace", "path": ["solver_examples", 0, "verification", 0],
            "value_json": '"Verify the measured bit independently"',
        }]})
        with (
            patch("backend.planning._research") as refresh,
            patch("backend.planning._structured", side_effect=[revised, self.approval]) as call,
        ):
            result = self.run_plan("Strengthen the independent verification.")

        research.assert_called_once()
        refresh.assert_not_called()
        self.assertEqual([request.args[2] for request in call.call_args_list], [PlanRevision, PlanReview])
        self.assertEqual(call.call_args_list[0].args[-1].count(original.plan.model_dump_json()), 1)
        self.assertNotIn(original.plan.model_dump_json(), call.call_args.args[-1])
        expected = original.plan.model_dump()
        expected["solver_examples"][0]["verification"][0] = "Verify the measured bit independently"
        self.assertEqual(result.plan.model_dump(), expected)
        self.assertEqual(result.research, original.research)
        self.assertEqual(result.answers, original.answers)
        with patch.dict(os.environ, {"MODEL_PROVIDER": "gemini"}):
            self.assertEqual(result.fidelity_fingerprint, plan_fidelity_fingerprint(self.workspace, model="fixture"))

    def test_fidelity_fingerprint_uses_the_configured_planning_reviewer(self):
        with (
            patch.dict(os.environ, {"MODEL_PROVIDER": "gemini", "GEMINI_PLAN_MODEL": "planner",
                                    "GEMINI_PLAN_REVIEW_MODEL": "reviewer-a"}),
            patch("backend.planning._research", return_value=self.research),
            patch("backend.planning._structured", side_effect=[self.draft, self.approval]),
        ):
            result = run_planning(self.workspace)
            self.assertEqual(result.model, "planner")
            self.assertEqual(result.fidelity_fingerprint, plan_fidelity_fingerprint(self.workspace))
        with patch.dict(os.environ, {"MODEL_PROVIDER": "gemini", "GEMINI_PLAN_REVIEW_MODEL": "reviewer-b"}):
            self.assertNotEqual(result.fidelity_fingerprint, plan_fidelity_fingerprint(self.workspace))

    def test_reinterpreting_first_draft_preserves_candidate_and_compatible_research(self):
        reinterpret = PlanReview(verdict="needs_reinterpretation", reason="Tighten the summary.")
        with (
            patch("backend.planning._research", return_value=self.research),
            patch("backend.planning._structured", side_effect=[self.draft, reinterpret]),
            self.assertRaises(ReinterpretationRequired) as stopped,
        ):
            self.run_plan()

        self.problem.summary = "Choose the lowest bit."
        self.workspace.resolve(PROBLEM_JSON).write_text(self.problem.model_dump_json(), encoding="utf-8")
        with (
            patch("backend.planning._research") as refresh,
            patch("backend.planning._structured", side_effect=[PlanRevision(status="ready"), self.approval]) as call,
        ):
            result = self.run_plan("Apply the corrected problem.", previous_draft=stopped.exception.checkpoint)

        refresh.assert_not_called()
        self.assertEqual(call.call_args_list[0].args[2], PlanRevision)
        self.assertEqual(result.plan, self.plan)
        self.assertEqual(result.research, self.research)


if __name__ == "__main__":
    unittest.main()
