"""Model routing, shared transport retries and a content-free usage ledger.

Stage code speaks only the types in model_types. Provider SDKs belong in adapters.
Nested stages share a session through ContextVar; independent runs do not.
"""

import inspect
import json
import math
import os
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable
from uuid import uuid4

from backend.model_types import (
    Message, ModelBudgetExceeded, ModelClient, ModelConfigurationError, ModelError,
    ModelRequest, ModelRequestError, ModelResponse, Stage, Usage,
)
from backend.progress import ProgressCallback, report

STAGES = frozenset({
    "interpret", "research", "plan", "plan_review", "build", "build_review", "semantic_review", "final_review",
})
_active_session: ContextVar["ModelSession | None"] = ContextVar("model_session", default=None)


def model_provider() -> str:
    provider = os.getenv("MODEL_PROVIDER", "gemini").strip().lower() or "gemini"
    if provider not in ("gemini", "openrouter"):
        raise ModelConfigurationError("Set MODEL_PROVIDER to gemini or openrouter.")
    return provider


def model_name(gemini_default: str, override: str | None = None, *, stage: Stage = "build") -> str:
    if stage not in STAGES:
        raise ModelConfigurationError(f"Unknown model stage: {stage}.")
    provider = model_provider()
    prefix = provider.upper()
    candidates = [override, os.getenv(f"{prefix}_{stage.upper()}_MODEL")]
    if stage.endswith("_review"):
        candidates.append(os.getenv(f"{prefix}_REVIEW_MODEL"))
    candidates.append(os.getenv(f"{prefix}_MODEL"))
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    if provider == "gemini":
        return gemini_default
    raise ModelConfigurationError(f"Set OPENROUTER_MODEL or OPENROUTER_{stage.upper()}_MODEL.")


def model_configured() -> bool:
    try:
        provider = model_provider()
        if provider == "gemini":
            return bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip())
        if not os.getenv("OPENROUTER_API_KEY", "").strip():
            return False
        for stage in STAGES:
            model_name("", stage=stage)
        return True
    except ModelConfigurationError:
        return False


def _number(name: str, default, *, integer=False, minimum=0, optional=False):
    raw = os.getenv(name, "").strip()
    if not raw:
        return None if optional else default
    try:
        value = int(raw) if integer else float(raw)
        if not math.isfinite(value) or value < minimum:
            raise ValueError
    except (ValueError, OverflowError):
        raise ModelConfigurationError(f"{name} must be a finite {'integer' if integer else 'number'} >= {minimum}.") from None
    return value


@dataclass
class RunContext:
    max_requests: int = 100
    max_retries: int = 8
    timeout_seconds: float = 1800
    request_timeout_seconds: float = 120
    max_output_tokens: int = 8192
    max_tokens: int | None = None
    max_cost: float | None = None
    usage_path: Path | None = None
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    run_id: str = field(default_factory=lambda: str(uuid4()))
    requests: int = field(default=0, init=False)
    retries: int = field(default=0, init=False)
    total_tokens: int = field(default=0, init=False)
    total_cost: float = field(default=0.0, init=False)
    _started: float = field(init=False, repr=False)
    _records: list[dict] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        for name in ("max_requests", "max_output_tokens", "max_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise ModelConfigurationError(f"{name} must be a positive integer.")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ModelConfigurationError("max_retries must be a nonnegative integer.")
        for name in ("timeout_seconds", "request_timeout_seconds", "max_cost"):
            value = getattr(self, name)
            minimum = 0 if name == "max_cost" else 0.001
            if value is not None and (not math.isfinite(value) or value < minimum):
                raise ModelConfigurationError(f"{name} must be finite and >= {minimum}.")
        self._started = self.clock()

    @classmethod
    def from_env(cls, workspace=None):
        return cls(
            max_requests=_number("MODEL_MAX_REQUESTS", 100, integer=True, minimum=1),
            max_retries=_number("MODEL_MAX_RETRIES", 8, integer=True),
            timeout_seconds=_number("MODEL_TIMEOUT_SECONDS", 1800, minimum=0.001),
            request_timeout_seconds=_number("MODEL_REQUEST_TIMEOUT_SECONDS", 120, minimum=0.001),
            max_output_tokens=_number("MODEL_MAX_OUTPUT_TOKENS", 8192, integer=True, minimum=1),
            max_tokens=_number("MODEL_MAX_TOKENS", None, integer=True, minimum=1, optional=True),
            max_cost=_number("MODEL_MAX_COST", None, optional=True),
            usage_path=workspace.resolve("model_usage.jsonl") if workspace else None,
        )

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - (self.clock() - self._started))

    def check(self, *, before_request=False):
        if self.remaining_seconds() <= 0:
            raise ModelBudgetExceeded("The shared model deadline expired.")
        if before_request and self.requests >= self.max_requests:
            raise ModelBudgetExceeded("The shared model request budget is exhausted.")
        if self.max_tokens is not None and (
            self.total_tokens >= self.max_tokens if before_request else self.total_tokens > self.max_tokens
        ):
            raise ModelBudgetExceeded("The shared model token budget is exhausted.")
        if self.max_cost is not None and (
            self.total_cost >= self.max_cost if before_request else self.total_cost > self.max_cost
        ):
            raise ModelBudgetExceeded("The shared model cost budget is exhausted.")

    def record(self, record: dict):
        self._records.append(record)
        usage = record.get("usage") or {}
        self.total_tokens += usage.get("total_tokens") or 0
        self.total_cost += usage.get("cost") or 0
        if self.usage_path:
            # Only metadata is recorded: never messages, tool payloads, or credentials.
            with self.usage_path.open("a", encoding="utf-8") as ledger:
                ledger.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self.save_summary()

    def summary(self) -> dict:
        successes = [record for record in self._records if record["status"] == "ok"]
        totals = {}
        for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens", "total_tokens", "cost"):
            values = [(record.get("usage") or {}).get(key) for record in self._records]
            totals[key] = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
        return {
            "schema_version": 1, "run_id": self.run_id, "requests": self.requests, "retries": self.retries,
            "successful_requests": len(successes), "failed_requests": len(self._records) - len(successes),
            "usage": totals,
            "cost_complete": bool(self._records) and all(
                (record.get("usage") or {}).get("cost") is not None for record in self._records
            ),
            "tokens_complete": bool(self._records) and all(
                (record.get("usage") or {}).get("total_tokens") is not None for record in self._records
            ),
        }

    def save_summary(self):
        if not self.usage_path:
            return
        summary = self.usage_path.with_suffix(".json")
        temporary = summary.with_name(f".{summary.name}.{self.run_id}.tmp")
        try:
            temporary.write_text(json.dumps(self.summary(), indent=2, allow_nan=False) + "\n", encoding="utf-8")
            temporary.replace(summary)
        finally:
            temporary.unlink(missing_ok=True)


class ModelSession:
    def __init__(self, adapter: ModelClient, provider: str, context: RunContext):
        self.adapter, self.provider, self.context = adapter, provider, context

    def request(self, request: ModelRequest, *, on_progress: ProgressCallback | None = None) -> ModelResponse:
        attempt = 0
        while True:
            self.context.check(before_request=True)
            output_limit = min(request.max_output_tokens, self.context.max_output_tokens)
            if self.context.max_tokens is not None:
                output_limit = min(output_limit, self.context.max_tokens - self.context.total_tokens)
            bounded = replace(request, max_output_tokens=output_limit)
            self.context.requests += 1
            attempt += 1
            started = self.context.clock()
            record = {
                "schema_version": 1, "run_id": self.context.run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": request.stage, "provider": self.provider, "model": request.model, "attempt": attempt,
            }
            try:
                response = self.adapter.generate(bounded, timeout=min(
                    self.context.request_timeout_seconds, self.context.remaining_seconds(),
                ))
            except ModelRequestError as error:
                record.update(status="error", code=error.code, retryable=error.retryable,
                              elapsed_seconds=round(self.context.clock() - started, 6), usage=None)
                if error.response is not None:
                    record.update(
                        actual_model=error.response.actual_model, actual_provider=error.response.actual_provider,
                        request_id=error.response.request_id, finish_reason=error.response.finish_reason,
                        usage=asdict(error.response.usage),
                    )
                self.context.record(record)
                if not error.retryable:
                    raise
                if self.context.retries >= self.context.max_retries:
                    raise ModelBudgetExceeded("The shared model retry budget is exhausted.") from error
                self.context.check(before_request=True)
                retry_after = error.retry_after
                if retry_after is not None and (not math.isfinite(retry_after) or retry_after < 0):
                    retry_after = None
                delay = retry_after if retry_after is not None else min(2 ** min(self.context.retries, 4), 16) + random.uniform(0, 0.25)
                if delay >= self.context.remaining_seconds():
                    raise ModelBudgetExceeded("The shared model deadline would expire before retrying.") from error
                self.context.retries += 1
                self.context.save_summary()
                report(
                    on_progress, "Retry the model request",
                    detail=f"{request.stage}: waiting {delay:.2f} seconds; shared retries used "
                           f"{self.context.retries} of {self.context.max_retries}.",
                )
                self.context.sleep(delay)
                continue
            record.update(
                status="ok", elapsed_seconds=round(self.context.clock() - started, 6),
                actual_model=response.actual_model, actual_provider=response.actual_provider,
                request_id=response.request_id, finish_reason=response.finish_reason, usage=asdict(response.usage),
            )
            self.context.record(record)
            self.context.check()
            return response


class _LazyClient:
    """Open a provider connection only when a stage actually makes a request."""

    def __init__(self, provider: str):
        self.provider = provider
        self.client = None

    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        if self.client is None:
            if self.provider == "openrouter":
                key = os.getenv("OPENROUTER_API_KEY", "").strip()
                if not key:
                    raise ModelConfigurationError("Set OPENROUTER_API_KEY to use OpenRouter.")
                from backend.providers.openrouter import OpenRouterClient

                self.client = OpenRouterClient(key)
            else:
                key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
                if not key:
                    raise ModelConfigurationError("Set GEMINI_API_KEY or GOOGLE_API_KEY to use Gemini.")
                from backend.providers.gemini import GeminiClient

                self.client = GeminiClient(key)
        return self.client.generate(request, timeout=timeout)

    def close(self):
        if self.client is not None:
            self.client.close()


@contextmanager
def model_session(client=None, workspace=None, context: RunContext | None = None):
    active = _active_session.get()
    if isinstance(client, ModelSession):
        session, owned = client, False
    elif active is not None and (client is None or client is active.adapter) and (
        context is None or context is active.context
    ):
        session, owned = active, False
    else:
        provider = model_provider()
        run_context = context or RunContext.from_env(workspace)
        if client is not None:
            adapter, owned = client, False
        else:
            adapter, owned = _LazyClient(provider), True
        session = ModelSession(adapter, provider, run_context)
    if workspace is not None and session.context.usage_path is None:
        session.context.usage_path = workspace.resolve("model_usage.jsonl")
    token = _active_session.set(session)
    try:
        yield session
    finally:
        _active_session.reset(token)
        session.context.save_summary()
        if owned:
            session.adapter.close()


def model_run(function):
    """Give workspace entrypoints one run scope, including all nested repairs."""
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        workspace = bound.arguments["workspace"]
        with model_session(bound.arguments.get("client"), workspace=workspace) as session:
            bound.arguments["client"] = session
            return function(*bound.args, **bound.kwargs)

    return wrapped


def generate(client, model: str, system: str, contents, *, stage: Stage, tools=(), schema=None,
             search=False, tool_choice="auto", on_progress: ProgressCallback | None = None) -> ModelResponse:
    messages = [Message(role="user", text=contents)] if isinstance(contents, str) else contents
    with model_session(client) as session:
        request = ModelRequest(
            model=model, system=system, messages=messages, stage=stage,
            tools=list(tools), schema=schema, search=search, tool_choice=tool_choice,
            max_output_tokens=session.context.max_output_tokens,
        )
        try:
            return session.request(request, on_progress=on_progress)
        except ModelError:
            report(on_progress, "Model request stopped", status="failed", detail=f"The {stage} request could not finish.")
            raise
