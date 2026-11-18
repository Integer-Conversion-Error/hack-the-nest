import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from backend.build import BuildError
from backend.build_review import BUILD_REVIEW_JSON, cached_build_review, compare_with_plan
from backend.plan import PLAN_JSON, Plan, PlanningResult, PlanReview, QubitEstimate, Research, SolverExample
from backend.problem_statement import PROBLEM_JSON, Constraint, ProblemStatement, QuantumRequirements
from backend.runner import RunOutcome
from backend.semantics import MAX_SOURCE_FOLLOWUPS, SemanticReview, describe_runs, evaluate_semantics
from backend.testing import TEST_JSON, test_once as check_once
from backend.workspace import init_workspace


class BuildReviewReuseTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"MODEL_PROVIDER": "gemini"})
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = init_workspace("Minimize the selected option's cost.", root=Path(directory.name))
        solver = SolverExample(
            name="QAOA", method="qaoa", classical_steps=[], quantum_steps=["Measure the circuit."],
            encoding="One bit", qubits=QubitEstimate(decision_qubits=1, auxiliary_qubits=0),
            verification=["Check the scalar cost."], reference_method="Enumerate two choices.",
            build_steps=["Write the solver."],
        )
        planning = PlanningResult(
            problem_title="Cost", model="test-model", attempts=1, max_qubits=4, worker_seconds=5,
            plan=Plan(summary="Minimize", objective="Cost", constraints=[], assumptions=[], controls=[],
                      solver_examples=[solver], selected_solver="QAOA"),
            research=Research(status="unavailable", summary="Research record stays on disk."),
            review=PlanReview(verdict="approve"),
        )
        problem = ProblemStatement(
            is_optimization_problem=True, title="Cost", summary="Minimize cost", problem_class="selection",
            given_data=[], decision_variables=["Selected option"], objective_sense="minimize", objective_terms=[],
            constraints=[], quantum=QuantumRequirements(method="QAOA", method_reason="Binary", quantum_part="Choose"),
            expected_output=["Cost"], assumptions=[], open_questions=[],
        )
        self.workspace.resolve(PLAN_JSON).write_text(planning.model_dump_json(), encoding="utf-8")
        self.workspace.resolve(PROBLEM_JSON).write_text(problem.model_dump_json(), encoding="utf-8")
        self.workspace.resolve("main.py").write_text("print('solver')\n", encoding="utf-8")
        self.workspace.resolve("qr_runtime.py").write_text("# Runtime helper\n", encoding="utf-8")
        self.workspace.resolve("data.json").write_text("{}", encoding="utf-8")
        self.response = SimpleNamespace(text=json.dumps({
            "verdict": "matches", "summary": "Plan implemented.", "issues": [], "accepted_deviations": [],
        }))

    def test_rejects_a_known_syntax_failure_locally(self):
        self.workspace.resolve("main.py").write_text("invalid python (", encoding="utf-8")
        with patch("backend.build_review.generate") as generate:
            review = compare_with_plan(self.workspace, model="reviewer")
        self.assertFalse(review.matches)
        self.assertIn("syntax error", review.feedback())
        generate.assert_not_called()

    def test_reuses_only_a_passing_review_of_matching_inputs(self):
        with patch("backend.build_review.generate", return_value=self.response) as generate:
            approval = compare_with_plan(self.workspace, model="reviewer")
            self.workspace.resolve("results.json").write_text("{}", encoding="utf-8")
            self.workspace.resolve(".review-plan.json").write_text("{}", encoding="utf-8")
            self.workspace.resolve("model_usage.json").write_text('{"requests": 7}', encoding="utf-8")
            self.workspace.resolve("model_usage.jsonl").write_text('{"stage": "build"}\n', encoding="utf-8")
            self.workspace.resolve("dispatch_animation.html").write_text("<p>Recalculated</p>", encoding="utf-8")
            self.workspace.resolve(".publish-plan").mkdir()
            self.workspace.resolve(".publish-plan/plan.json").write_text("{}", encoding="utf-8")
            self.assertEqual(compare_with_plan(self.workspace, model="reviewer"), approval)
        generate.assert_called_once()
        self.assertNotIn("Research record stays on disk", generate.call_args.args[3])
        self.assertIsNone(cached_build_review(self.workspace, model="different-reviewer"))
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openrouter"}):
            self.assertIsNone(cached_build_review(self.workspace, model="reviewer"))

        for path in ("main.py", "qr_runtime.py", "data.json", PROBLEM_JSON, PLAN_JSON):
            with self.subTest(path=path):
                source = self.workspace.resolve(path)
                original = source.read_bytes()
                source.write_bytes(original + b"\n")
                self.assertIsNone(cached_build_review(self.workspace, model="reviewer"))
                source.write_bytes(original)

        legacy = approval.model_dump(exclude={"fingerprint"})
        self.workspace.resolve(BUILD_REVIEW_JSON).write_text(json.dumps(legacy), encoding="utf-8")
        self.assertIsNone(cached_build_review(self.workspace, model="reviewer"))

    def test_configured_build_reviewer_change_invalidates_approval(self):
        with (
            patch.dict(os.environ, {"GEMINI_BUILD_REVIEW_MODEL": "reviewer-a"}),
            patch("backend.build_review.generate", return_value=self.response),
        ):
            approval = compare_with_plan(self.workspace)
            self.assertEqual(approval.model, "reviewer-a")
            self.assertIsNotNone(cached_build_review(self.workspace))
        with patch.dict(os.environ, {"GEMINI_BUILD_REVIEW_MODEL": "reviewer-b"}):
            self.assertIsNone(cached_build_review(self.workspace))

    def test_does_not_publish_an_approval_if_inputs_changed_during_review(self):
        def replace_data(*args, **kwargs):
            self.workspace.resolve("data.json").write_text('{"cost": 12}', encoding="utf-8")
            return self.response

        with patch("backend.build_review.generate", side_effect=replace_data):
            with self.assertRaisesRegex(BuildError, "inputs changed"):
                compare_with_plan(self.workspace, model="reviewer")
        self.assertIsNone(cached_build_review(self.workspace, model="reviewer"))

    def run_outcome(self, controls=None):
        return RunOutcome(
            command="python main.py", controls=controls or {}, exit_code=0, timed_out=False,
            seconds=0.1, stdout_tail="", stderr_tail="", issues=[],
            results={"status": "ok", "controls": controls or {}, "objective": {"value": 1},
                     "solution": {"option": 1}},
        )

    def test_worker_cannot_approve_results_after_changing_solver_inputs(self):
        def mutate_solver(*args, **kwargs):
            self.workspace.resolve("main.py").write_text("print('changed while running')\n", encoding="utf-8")
            return self.run_outcome()

        with (
            patch("backend.testing.run_solver", side_effect=mutate_solver),
            patch("backend.testing.evaluate_semantics") as judge,
        ):
            with self.assertRaisesRegex(BuildError, "inputs changed during execution"):
                check_once(self.workspace, reuse=True)
        judge.assert_not_called()
        self.assertFalse(self.workspace.resolve(TEST_JSON).exists())
        self.assertFalse(self.workspace.resolve("handoff.json").exists())

    def test_cached_run_set_must_include_each_expected_control_variant_in_order(self):
        semantic = SemanticReview(verdict="matches", summary="Verified", checks_done=[], issues=[], model="reviewer")
        variants = [{"price": 2}, {"price": 3}]
        with (
            patch("backend.testing.control_variants", return_value=variants),
            patch("backend.testing.run_solver", side_effect=[self.run_outcome(), *map(self.run_outcome, variants)]),
            patch("backend.testing.evaluate_semantics", return_value=semantic),
        ):
            check_once(self.workspace, reuse=True)
        saved = json.loads(self.workspace.resolve(TEST_JSON).read_text(encoding="utf-8"))
        for runs in (saved["runs"][:1], [saved["runs"][0], saved["runs"][2], saved["runs"][1]]):
            with self.subTest(controls=[run["controls"] for run in runs]):
                self.workspace.resolve(TEST_JSON).write_text(json.dumps({**saved, "runs": runs}), encoding="utf-8")
                with (
                    patch("backend.testing.control_variants", return_value=variants),
                    patch("backend.testing.run_solver", side_effect=[self.run_outcome(), *map(self.run_outcome, variants)]) as worker,
                    patch("backend.testing.evaluate_semantics", return_value=semantic),
                ):
                    check_once(self.workspace, reuse=True)
                self.assertEqual(worker.call_count, 3)


class SemanticEvidenceTests(unittest.TestCase):
    def test_keeps_run_differences_and_moves_equal_fields_to_shared_evidence(self):
        def run(value, option):
            return RunOutcome(
                command=f"python main.py --cost {value}", controls={"cost": value},
                exit_code=0, timed_out=False, seconds=0.1, stdout_tail="", stderr_tail="", issues=[],
                results={"status": "ok", "objective": {"value": value}, "solution": {"option": option}},
            )

        evidence = json.loads(describe_runs([run(1, True), run(2, 1)]))
        self.assertEqual(evidence["shared_result_fields"], {"status": "ok"})
        first, second = evidence["runs"]
        self.assertEqual(first["requested_controls"], {"cost": 1})
        self.assertEqual(second["result_fields"]["objective"], {"value": 2})
        self.assertIs(first["result_fields"]["solution"]["option"], True)
        self.assertIs(type(second["result_fields"]["solution"]["option"]), int)


class SemanticSourceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = init_workspace("Minimize cost with a feasible choice.", root=Path(directory.name))
        problem = ProblemStatement(
            is_optimization_problem=True, title="Cost", summary="Minimize cost", problem_class="selection",
            given_data=[], decision_variables=["Selected option"], objective_sense="minimize", objective_terms=[],
            constraints=[Constraint(description="Capacity is five.", kind="hard")],
            quantum=QuantumRequirements(method="QAOA", method_reason="Binary", quantum_part="Choose"),
            expected_output=["Cost"], assumptions=[], open_questions=[],
        )
        self.workspace.resolve(PROBLEM_JSON).write_text(problem.model_dump_json(), encoding="utf-8")
        self.workspace.resolve("main.py").write_text(
            "# Formatting comments that fill a large source file.\n" * 500
            + "def enforce_capacity(selected):\n    return selected <= 5\n",
            encoding="utf-8",
        )
        self.verdict = {"verdict": "matches", "summary": "Checks passed.", "checks_done": [], "issues": []}

    def test_requested_source_range_is_available_before_the_verdict(self):
        requests = [{"path": "main.py", "start_line": 501, "end_line": 502}]
        coverage = [{"path": "main.py", "reason": "Read capacity check at lines 501–502 for C1."}]
        responses = [
            SimpleNamespace(text=json.dumps({**self.verdict, "verdict": "needs_source", "source_requests": requests})),
            SimpleNamespace(text=json.dumps({**self.verdict, "source_coverage": coverage})),
        ]
        with patch("backend.semantics.generate", side_effect=responses) as generate:
            review = evaluate_semantics(self.workspace, [], model="reviewer")

        first, second = (call.args[3] for call in generate.call_args_list)
        self.assertIn("enforce_capacity:501-502", first)
        self.assertIn('"id":"C1"', first)
        self.assertNotIn("return selected <= 5", first)
        self.assertIn("return selected <= 5", second)
        self.assertTrue(review.matches)
        self.assertEqual(review.source_coverage[0].path, "main.py")

    def test_never_approves_partial_source_without_coverage(self):
        with patch("backend.semantics.generate", return_value=SimpleNamespace(text=json.dumps(self.verdict))) as generate:
            with self.assertRaisesRegex(BuildError, "source review limit"):
                evaluate_semantics(self.workspace, [], model="reviewer")

        self.assertEqual(generate.call_count, MAX_SOURCE_FOLLOWUPS + 1)
        self.assertIn("coverage of these partial source files: main.py", generate.call_args.args[3])

    def test_rejects_source_requests_outside_the_inventory(self):
        requests = [{"path": "../private.py", "start_line": 1, "end_line": 2}]
        payload = SimpleNamespace(text=json.dumps({**self.verdict, "verdict": "needs_source", "source_requests": requests}))
        with patch("backend.semantics.generate", return_value=payload), patch("backend.semantics.read_file") as read:
            with self.assertRaisesRegex(BuildError, "unlisted"):
                evaluate_semantics(self.workspace, [], model="reviewer")
        read.assert_not_called()
