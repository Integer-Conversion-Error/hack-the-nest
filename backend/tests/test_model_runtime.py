import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.model_provider import ModelSession, RunContext, model_name, model_provider, model_session
from backend.model_types import (
    Message,
    ModelBudgetExceeded,
    ModelConfigurationError,
    ModelRequest,
    ModelRequestError,
    ModelResponse,
    Usage,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


class ScriptedAdapter:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False

    def generate(self, request, *, timeout):
        self.calls.append((request, timeout))
        if not self.outcomes:
            raise AssertionError("Unexpected outbound model attempt")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def request(stage="plan"):
    return ModelRequest(
        model="fixture/model", system="private-system", stage=stage,
        messages=[Message(role="user", text="private-user-prompt")],
    )


class ModelRoutingTests(unittest.TestCase):
    def test_openrouter_stage_and_review_overrides_have_defined_precedence(self):
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "openrouter", "OPENROUTER_MODEL": "vendor/general",
            "OPENROUTER_BUILD_MODEL": "vendor/coder", "OPENROUTER_REVIEW_MODEL": "vendor/reviewer",
            "OPENROUTER_PLAN_REVIEW_MODEL": "vendor/plan-reviewer", "GEMINI_MODEL": "wrong-provider",
        }, clear=True):
            self.assertEqual(model_name("gemini-default", stage="plan"), "vendor/general")
            self.assertEqual(model_name("gemini-default", stage="build"), "vendor/coder")
            self.assertEqual(model_name("gemini-default", stage="semantic_review"), "vendor/reviewer")
            self.assertEqual(model_name("gemini-default", stage="plan_review"), "vendor/plan-reviewer")
            self.assertEqual(model_name("gemini-default", "explicit/model", stage="build"), "explicit/model")

    def test_gemini_remains_default_and_uses_its_own_stage_models(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(model_provider(), "gemini")
            self.assertEqual(model_name("original-default", stage="interpret"), "original-default")
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-global",
            "GEMINI_RESEARCH_MODEL": "gemini-research", "GEMINI_REVIEW_MODEL": "gemini-review",
            "OPENROUTER_BUILD_MODEL": "wrong-provider",
        }, clear=True):
            self.assertEqual(model_name("original-default", stage="research"), "gemini-research")
            self.assertEqual(model_name("original-default", stage="build_review"), "gemini-review")
            self.assertEqual(model_name("original-default", stage="build"), "gemini-global")

    def test_openrouter_never_silently_uses_a_gemini_model_id(self):
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openrouter"}, clear=True):
            with self.assertRaises(ModelConfigurationError):
                model_name("gemini-default", stage="plan")
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "openrouter", "OPENROUTER_BUILD_MODEL": "vendor/coder",
        }, clear=True):
            self.assertEqual(model_name("gemini-default", stage="build"), "vendor/coder")

    def test_invalid_provider_fails_before_creating_a_client(self):
        with patch.dict(os.environ, {"MODEL_PROVIDER": "typo"}, clear=True):
            with self.assertRaises(ModelConfigurationError):
                model_provider()

    def test_invalid_or_nonfinite_budget_settings_are_rejected(self):
        for name, value in (
            ("MODEL_MAX_REQUESTS", "0"), ("MODEL_MAX_RETRIES", "-1"),
            ("MODEL_TIMEOUT_SECONDS", "nan"), ("MODEL_REQUEST_TIMEOUT_SECONDS", "0"),
            ("MODEL_MAX_OUTPUT_TOKENS", "broken"), ("MODEL_MAX_TOKENS", "-1"),
            ("MODEL_MAX_COST", "inf"),
        ):
            with self.subTest(setting=name), patch.dict(os.environ, {name: value}, clear=True):
                with self.assertRaises(ModelConfigurationError):
                    RunContext.from_env()


class SharedModelBudgetTests(unittest.TestCase):
    def context(self, **settings):
        clock = FakeClock()
        return RunContext(clock=clock, sleep=clock.sleep, **settings), clock

    def test_retry_allowance_is_shared_between_distinct_stages(self):
        context, _ = self.context(max_retries=1)
        adapter = ScriptedAdapter(
            ModelRequestError("temporary", code=429, retryable=True), ModelResponse(text="plan"),
            ModelRequestError("temporary", code=503, retryable=True),
        )
        session = ModelSession(adapter, "openrouter", context)

        self.assertEqual(session.request(request("plan")).text, "plan")
        with self.assertRaises(ModelBudgetExceeded):
            session.request(request("build"))

        self.assertEqual([call.stage for call, _ in adapter.calls], ["plan", "plan", "build"])

    def test_failed_attempts_also_consume_the_request_cap(self):
        context, _ = self.context(max_requests=2, max_retries=8)
        adapter = ScriptedAdapter(
            ModelRequestError("temporary", retryable=True),
            ModelRequestError("temporary", retryable=True),
        )
        session = ModelSession(adapter, "gemini", context)

        with self.assertRaises(ModelBudgetExceeded):
            session.request(request())

        self.assertEqual(len(adapter.calls), 2)

    def test_permanent_errors_are_not_retried(self):
        context, clock = self.context()
        failure = ModelRequestError("invalid credentials", code=401)
        adapter = ScriptedAdapter(failure)
        session = ModelSession(adapter, "openrouter", context)

        with self.assertRaises(ModelRequestError) as caught:
            session.request(request())

        self.assertIs(caught.exception, failure)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(clock.delays, [])

    def test_retry_after_and_request_timeouts_respect_the_remaining_deadline(self):
        context, clock = self.context(timeout_seconds=10, request_timeout_seconds=30)
        adapter = ScriptedAdapter(
            ModelRequestError("rate limited", code=429, retryable=True, retry_after=3),
            ModelResponse(text="ok"),
        )
        session = ModelSession(adapter, "openrouter", context)

        self.assertEqual(session.request(request()).text, "ok")

        self.assertEqual(clock.delays, [3])
        self.assertEqual([timeout for _, timeout in adapter.calls], [10, 7])

    def test_an_expired_run_never_dispatches_a_request(self):
        context, clock = self.context(timeout_seconds=5)
        clock.now = 5
        adapter = ScriptedAdapter()
        session = ModelSession(adapter, "gemini", context)

        with self.assertRaises(ModelBudgetExceeded):
            session.request(request())

        self.assertEqual(adapter.calls, [])

    def test_a_retry_that_would_exceed_the_deadline_does_not_sleep_or_dispatch(self):
        context, clock = self.context(timeout_seconds=5)
        adapter = ScriptedAdapter(ModelRequestError("rate limited", retryable=True, retry_after=6))
        session = ModelSession(adapter, "openrouter", context)

        with self.assertRaises(ModelBudgetExceeded):
            session.request(request())

        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(clock.delays, [])

    def test_each_request_gets_the_output_cap_and_remaining_token_allowance(self):
        context, _ = self.context(max_output_tokens=5, max_tokens=12)
        adapter = ScriptedAdapter(
            ModelResponse(usage=Usage(total_tokens=9)), ModelResponse(usage=Usage(total_tokens=3)),
        )
        session = ModelSession(adapter, "openrouter", context)
        session.request(request("research"))
        session.request(request("plan"))

        self.assertEqual([call.max_output_tokens for call, _ in adapter.calls], [5, 3])

    def test_reported_token_and_cost_usage_stop_subsequent_dispatch(self):
        for setting, usage in (
            ({"max_tokens": 12}, Usage(input_tokens=8, output_tokens=4, total_tokens=12)),
            ({"max_cost": 0.25}, Usage(cost=0.25)),
        ):
            with self.subTest(setting=setting):
                context, _ = self.context(**setting)
                adapter = ScriptedAdapter(ModelResponse(text="ok", usage=usage))
                session = ModelSession(adapter, "openrouter", context)
                session.request(request("research"))
                with self.assertRaises(ModelBudgetExceeded):
                    session.request(request("plan"))
                self.assertEqual(len(adapter.calls), 1)

    def test_nested_model_sessions_share_the_same_budget(self):
        context, _ = self.context(max_requests=1)
        adapter = ScriptedAdapter(ModelResponse(text="first"))
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openrouter"}, clear=True):
            with model_session(client=adapter, context=context) as outer:
                with model_session() as inner:
                    self.assertIs(inner, outer)
                    inner.request(request("interpret"))
                with self.assertRaises(ModelBudgetExceeded):
                    outer.request(request("plan"))
        self.assertEqual(len(adapter.calls), 1)

    def test_nested_use_of_the_same_raw_client_keeps_the_active_budget(self):
        context, _ = self.context(max_requests=1)
        adapter = ScriptedAdapter(ModelResponse())
        with patch.dict(os.environ, {"MODEL_PROVIDER": "openrouter"}, clear=True):
            with model_session(client=adapter, context=context) as outer:
                outer.request(request("interpret"))
                with model_session(client=adapter) as inner:
                    self.assertIs(inner, outer)
                    with self.assertRaises(ModelBudgetExceeded):
                        inner.request(request("plan"))
        self.assertEqual(len(adapter.calls), 1)


class ModelUsageTests(unittest.TestCase):
    def test_usage_trace_records_attempts_without_prompt_or_tool_secrets(self):
        with TemporaryDirectory() as directory:
            usage_path = Path(directory) / "model_usage.jsonl"
            clock = FakeClock()
            context = RunContext(usage_path=usage_path, clock=clock, sleep=clock.sleep)
            adapter = ScriptedAdapter(
                ModelRequestError("private-provider-payload", code=429, retryable=True),
                ModelResponse(
                    text="private-generated-code", request_id="generation-123",
                    actual_model="vendor/actual-model", actual_provider="actual-host",
                    message=Message(role="assistant", provider_state={"reasoning": "private-reasoning"}),
                    usage=Usage(input_tokens=10, output_tokens=7, reasoning_tokens=3,
                                cached_tokens=2, total_tokens=17, cost=0.02),
                ),
            )
            session = ModelSession(adapter, "openrouter", context)
            session.request(request("build"))

            raw = usage_path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(rows), 2)
            for private in ("private-system", "private-user-prompt", "private-provider-payload",
                            "private-generated-code", "private-reasoning"):
                self.assertNotIn(private, raw)
            self.assertIn("generation-123", raw)
            self.assertIn("vendor/actual-model", raw)
            self.assertIn("actual-host", raw)
            summary = json.loads((Path(directory) / "model_usage.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["requests"], summary["retries"], summary["successful_requests"],
                              summary["failed_requests"]), (2, 1, 1, 1))
            self.assertEqual(summary["usage"]["total_tokens"], 17)
            self.assertEqual(summary["usage"]["output_tokens"], 7)
            self.assertEqual(summary["usage"]["reasoning_tokens"], 3)
            self.assertEqual(summary["usage"]["cost"], 0.02)
            self.assertFalse(summary["tokens_complete"])
            self.assertFalse(summary["cost_complete"])

    def test_failed_requests_leave_a_trace_before_exception_propagates(self):
        with TemporaryDirectory() as directory:
            usage_path = Path(directory) / "model_usage.jsonl"
            context = RunContext(usage_path=usage_path)
            session = ModelSession(ScriptedAdapter(ModelRequestError("not authorized", code=401)),
                                   "gemini", context)
            with self.assertRaises(ModelRequestError):
                session.request(request("interpret"))

            rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertIn("interpret", json.dumps(rows))
            self.assertIn("401", json.dumps(rows))

    def test_missing_usage_stays_unknown_and_later_runs_append_to_the_trace(self):
        with TemporaryDirectory() as directory:
            usage_path = Path(directory) / "model_usage.jsonl"
            first = RunContext(usage_path=usage_path)
            ModelSession(ScriptedAdapter(ModelResponse()), "gemini", first).request(request())
            summary = first.summary()
            self.assertIsNone(summary["usage"]["cost"])
            self.assertIsNone(summary["usage"]["total_tokens"])
            self.assertFalse(summary["cost_complete"])
            self.assertFalse(summary["tokens_complete"])

            second = RunContext(usage_path=usage_path)
            ModelSession(ScriptedAdapter(ModelResponse(usage=Usage(total_tokens=9, cost=0.01))),
                         "openrouter", second).request(request("build"))
            rows = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["run_id"], rows[1]["run_id"])
            self.assertIsNone(rows[0]["usage"]["cost"])
            self.assertEqual(rows[1]["usage"]["cost"], 0.01)
            self.assertTrue(second.summary()["cost_complete"])

    def test_billed_invalid_completions_still_consume_budget_and_record_usage(self):
        with TemporaryDirectory() as directory:
            usage_path = Path(directory) / "model_usage.jsonl"
            context = RunContext(usage_path=usage_path, max_cost=0.25, sleep=lambda seconds: None)
            failure = ModelRequestError("private-provider-payload", code=502, retryable=True, response=ModelResponse(
                usage=Usage(input_tokens=10, output_tokens=7, total_tokens=17, cost=0.25),
                actual_model="vendor/actual-model", actual_provider="actual-host", request_id="billed-failure",
            ))
            adapter = ScriptedAdapter(failure)
            session = ModelSession(adapter, "openrouter", context)

            with self.assertRaises(ModelBudgetExceeded):
                session.request(request("build"))

            self.assertEqual(len(adapter.calls), 1)
            raw = usage_path.read_text(encoding="utf-8")
            self.assertNotIn("private-provider-payload", raw)
            record = json.loads(raw)
            self.assertEqual(record["request_id"], "billed-failure")
            self.assertEqual(record["actual_model"], "vendor/actual-model")
            self.assertEqual(record["actual_provider"], "actual-host")
            self.assertEqual(record["usage"]["total_tokens"], 17)
            self.assertEqual(record["usage"]["cost"], 0.25)
            self.assertEqual(context.summary()["usage"]["total_tokens"], 17)
            self.assertEqual(context.summary()["usage"]["cost"], 0.25)


if __name__ == "__main__":
    unittest.main()
