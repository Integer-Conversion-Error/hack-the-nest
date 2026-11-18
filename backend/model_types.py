"""Provider-neutral messages, requests and results used by the agent pipeline."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Stage = Literal[
    "interpret", "research", "plan", "plan_review", "build", "build_review", "semantic_review", "final_review",
]


class ModelError(Exception):
    """A model could not be configured, called, or run within its budget."""


class ModelConfigurationError(ModelError):
    pass


class ModelBudgetExceeded(ModelError):
    pass


class ModelRequestError(ModelError):
    def __init__(self, message: str, *, code: int | None = None, retryable: bool = False,
                 retry_after: float | None = None, response: "ModelResponse | None" = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.response = response


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    result: dict | None = None
    # Opaque adapter-owned assistant state (e.g. signed thoughts). Never logged.
    provider_state: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Citation:
    url: str
    title: str | None = None


@dataclass
class Usage:
    input_tokens: int | None = None
    # Completion tokens include reasoning tokens, matching OpenRouter billing.
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None


@dataclass
class ModelRequest:
    model: str
    system: str
    messages: list[Message]
    stage: Stage
    tools: list[Tool] = field(default_factory=list)
    schema: dict | None = None
    search: bool = False
    tool_choice: Literal["auto", "required", "none"] = "auto"
    max_output_tokens: int = 8192


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: Message | None = None
    citations: list[Citation] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    actual_model: str | None = None
    actual_provider: str | None = None
    request_id: str | None = None


class ModelClient(Protocol):
    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse: ...

    def close(self) -> None: ...
