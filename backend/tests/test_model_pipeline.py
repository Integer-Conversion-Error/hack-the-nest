"""Exercise real pipeline orchestration with a provider-neutral model double."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.model_provider import ModelSession, RunContext
from backend.model_types import Citation, Message, ModelBudgetExceeded, ModelRequestError, ModelResponse, ToolCall, Usage
from backend.pipeline import run_pipeline
from backend.workspace import init_workspace


PROBLEM = {
    "is_optimization_problem": True, "title": "Choose a bit", "summary": "Minimize a Boolean decision.",
    "problem_class": "binary optimization", "given_data": [], "decision_variables": ["x"],
    "objective_sense": "minimize", "objective_terms": [], "constraints": [],
    "quantum": {"method": "QAOA", "method_reason": "Binary encoding", "quantum_part": "Choose x"},
    "expected_output": ["Chosen bit"], "assumptions": [], "open_questions": [],
}
PLAN = {
    "summary": "Sample one bit", "objective": "Minimize x", "constraints": [], "assumptions": [],
    "controls": [], "selected_solver": "one bit", "solver_examples": [{
        "name": "one bit", "method": "QAOA", "encoding": "One qubit for x", "classical_steps": [],
        "quantum_steps": ["Sample x"], "qubits": {"decision_qubits": 1, "auxiliary_qubits": 0},
        "verification": ["Check x is 0"], "reference_method": "Enumerate both bits", "build_steps": ["Write main.py"],
    }],
}
RESULT = {
    "status": "ok", "controls": {}, "objective": {"value": 0}, "solution": {"x": 0}, "reference": None,
    "quantum": {"qubits": 1, "counts": {"0": 1}, "circuit_diagram": {"file": "circuit.png"}},
    "runtime_seconds": 0.01,
}
SOLVER = (
    "from pathlib import Path\n"
    f"Path('results.json').write_text({json.dumps(RESULT)!r}, encoding='utf-8')\n"
    "Path('circuit.png').touch()\n"
)


class PipelineModel:
    def __init__(self, failures=()):
        self.requests = []
        self.build_turn = 0
        self.failures = set(failures)

    def generate(self, request, *, timeout):
        self.requests.append(request)
        if request.stage in self.failures:
            self.failures.remove(request.stage)
            raise ModelRequestError("temporary", code=503, retryable=True, retry_after=0)
        judge = {"verdict": "matches", "summary": "Matches", "issues": []}
        if request.stage == "interpret":
            response = ModelResponse(text=json.dumps(PROBLEM))
        elif request.stage == "research":
            response = ModelResponse(text="Use a single bit.", citations=[Citation(url="https://example.org/research")])
        elif request.stage == "plan":
            response = ModelResponse(text=json.dumps({"status": "ready", "plan": PLAN}))
        elif request.stage == "plan_review":
            response = ModelResponse(text=json.dumps({"verdict": "approve"}))
        elif request.stage == "build":
            self.build_turn += 1
            call = (
                ToolCall(id="create", name="create_file", arguments={"path": "main.py", "content": SOLVER})
                if self.build_turn == 1 else
                ToolCall(id="finish", name="finish", arguments={"summary": "Wrote main.py"})
            )
            response = ModelResponse(tool_calls=[call], message=Message(role="assistant", tool_calls=[call]))
        elif request.stage == "build_review":
            response = ModelResponse(text=json.dumps({**judge, "accepted_deviations": []}))
        elif request.stage == "semantic_review":
            response = ModelResponse(text=json.dumps({**judge, "checks_done": ["Checked the bit"]}))
        elif request.stage == "final_review":
            response = ModelResponse(text=json.dumps(judge))
        else:
            raise AssertionError(f"Unexpected stage {request.stage}")
        response.usage = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cost=0.001)
        response.actual_model = request.model
        response.request_id = f"fixture-{len(self.requests)}"
        return response

    def close(self):
        pass


class ModelPipelineTests(unittest.TestCase):
    def test_both_providers_reach_go_with_models_selected_for_every_stage(self):
        stages = {"interpret", "research", "plan", "plan_review", "build", "build_review",
                  "semantic_review", "final_review"}
        for provider in ("gemini", "openrouter"):
            environment = {"MODEL_PROVIDER": provider, f"{provider.upper()}_MODEL": "wrong/global-model"}
            environment.update({f"{provider.upper()}_{stage.upper()}_MODEL": f"fixture/{stage}" for stage in stages})
            with self.subTest(provider=provider), TemporaryDirectory() as directory:
                with patch.dict(os.environ, environment, clear=True):
                    workspace = init_workspace("Choose the smallest bit.", root=Path(directory))
                    adapter = PipelineModel()
                    handoff = run_pipeline(workspace, client=adapter)

                self.assertEqual(handoff["decision"], "GO")
                self.assertEqual(handoff["results"]["solution"], {"x": 0})
                self.assertEqual({request.stage for request in adapter.requests}, stages)
                self.assertTrue(all(request.model == f"fixture/{request.stage}" for request in adapter.requests))
                plan = json.loads(workspace.resolve("plan.json").read_text(encoding="utf-8"))
                self.assertEqual(plan["research"]["status"], "grounded")
                self.assertEqual(plan["model"], "fixture/plan")
                rows = [json.loads(line) for line in workspace.resolve("model_usage.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), len(adapter.requests))
                self.assertEqual(len({row["run_id"] for row in rows}), 1)
                self.assertTrue(all(row["provider"] == provider for row in rows))

    def test_pipeline_stages_do_not_reset_the_shared_transport_retry_allowance(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "MODEL_PROVIDER": "openrouter", "OPENROUTER_MODEL": "fixture/model",
        }, clear=True):
            workspace = init_workspace("Choose the smallest bit.", root=Path(directory))
            adapter = PipelineModel(failures=("research", "plan_review"))
            context = RunContext(max_retries=1, sleep=lambda seconds: None)
            session = ModelSession(adapter, "openrouter", context)

            with self.assertRaises(ModelBudgetExceeded):
                run_pipeline(workspace, client=session)

            self.assertEqual([request.stage for request in adapter.requests],
                             ["interpret", "research", "research", "plan", "plan_review"])
            self.assertFalse(workspace.resolve("handoff.json").exists())
            self.assertFalse(workspace.resolve("plan.json").exists())
            summary = json.loads(workspace.resolve("model_usage.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["requests"], 5)
            self.assertEqual(summary["failed_requests"], 2)

    def test_a_plan_revision_uses_the_original_pipeline_request_budget(self):
        class RevisingPlanner(PipelineModel):
            def __init__(self):
                super().__init__()
                self.plan_count = 0

            def generate(self, request, *, timeout):
                response = super().generate(request, timeout=timeout)
                if request.stage == "plan":
                    self.plan_count += 1
                    draft = json.loads(response.text)
                    draft["plan"]["summary"] = f"Plan revision {self.plan_count}"
                    response.text = json.dumps(draft)
                if request.stage == "plan_review":
                    response.text = json.dumps({"verdict": "revise", "issues": ["Clarify the encoding."]})
                return response

        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "MODEL_PROVIDER": "gemini", "GEMINI_MODEL": "fixture/model",
        }, clear=True):
            workspace = init_workspace("Choose the smallest bit.", root=Path(directory))
            adapter = RevisingPlanner()
            context = RunContext(max_requests=5)
            session = ModelSession(adapter, "gemini", context)

            with self.assertRaises(ModelBudgetExceeded):
                run_pipeline(workspace, client=session)

            self.assertEqual([request.stage for request in adapter.requests],
                             ["interpret", "research", "plan", "plan_review", "plan"])
            self.assertEqual(context.requests, 5)
            self.assertEqual(context.retries, 0)
            self.assertFalse(workspace.resolve("handoff.json").exists())


if __name__ == "__main__":
    unittest.main()
