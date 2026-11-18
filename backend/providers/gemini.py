"""Gemini SDK conversion isolated behind the provider-neutral model contract."""

from importlib import import_module

from backend.model_types import (
    Citation, Message, ModelConfigurationError, ModelRequest, ModelRequestError, ModelResponse, ToolCall, Usage,
)
from backend.providers import retry_after

_RETRYABLE_CODES = (408, 429, 500, 502, 503, 504)


def _transport_errors():
    errors = [TimeoutError, OSError]
    for module in ("httpx", "httpx2"):
        try:
            errors.append(import_module(module).RequestError)
        except ImportError:
            continue
    return tuple(errors)


def _contents(messages, types):
    contents = []
    pending_tools = []
    native_ids = {}
    for message in messages:
        if message.role == "tool":
            pending_tools.append(types.Part(function_response=types.FunctionResponse(
                id=native_ids.get(message.tool_call_id, message.tool_call_id), name=message.name, response=message.result,
            )))
            continue
        if pending_tools:
            contents.append(types.Content(role="user", parts=pending_tools))
            pending_tools = []
        if message.role == "assistant" and "gemini" in message.provider_state:
            native = message.provider_state["gemini"]
            contents.append(native)
            native_calls = [part.function_call for part in native.parts or [] if part.function_call is not None]
            for call, original in zip(message.tool_calls, native_calls):
                native_ids[call.id] = original.id
            continue
        parts = [types.Part(text=message.text)] if message.text is not None else []
        parts.extend(types.Part(function_call=types.FunctionCall(
            id=call.id, name=call.name, args=call.arguments,
        )) for call in message.tool_calls)
        contents.append(types.Content(role="model" if message.role == "assistant" else "user", parts=parts))
    if pending_tools:
        contents.append(types.Content(role="user", parts=pending_tools))
    return contents


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _metadata(response):
    usage = response.usage_metadata
    generated = _count(usage.candidates_token_count) if usage is not None else None
    thoughts = _count(usage.thoughts_token_count) if usage is not None else None
    output_tokens = (generated or 0) + (thoughts or 0) if generated is not None or thoughts is not None else None
    return ModelResponse(
        actual_model=response.model_version, actual_provider="google", request_id=response.response_id,
        usage=Usage(
            input_tokens=_count(usage.prompt_token_count) if usage is not None else None,
            output_tokens=output_tokens, reasoning_tokens=thoughts,
            cached_tokens=_count(usage.cached_content_token_count) if usage is not None else None,
            total_tokens=_count(usage.total_token_count) if usage is not None else None,
        ),
    )


def _response(response):
    metadata = _metadata(response)
    try:
        return _completion(response, metadata)
    except ModelRequestError as error:
        error.response = metadata
        raise


def _completion(response, metadata):
    candidate = (response.candidates or [None])[0]
    content = candidate.content if candidate is not None else None
    text_parts, calls = [], []
    ids = set()
    for part in content.parts or [] if content is not None else []:
        if part.text is not None and not part.thought:
            text_parts.append(part.text)
        if part.function_call is not None:
            call = part.function_call
            call_id = call.id or f"call_{len(calls)}"
            if not call.name or not isinstance(call.args or {}, dict) or call_id in ids:
                raise ModelRequestError("Gemini returned a malformed tool call.")
            ids.add(call_id)
            calls.append(ToolCall(id=call_id, name=call.name, arguments=call.args or {}))
    citations = []
    grounding = candidate.grounding_metadata if candidate is not None else None
    if grounding is not None:
        for chunk in grounding.grounding_chunks or []:
            web = chunk.web
            if web is not None and web.uri and web.uri.startswith(("http://", "https://")):
                citations.append(Citation(url=web.uri, title=web.title))
    finish = candidate.finish_reason if candidate is not None else None
    text = "".join(text_parts)
    return ModelResponse(
        text=text, tool_calls=calls,
        message=Message(role="assistant", text=text or None, tool_calls=calls,
                        provider_state={"gemini": content} if content is not None else {}),
        citations=citations, finish_reason=finish.value if hasattr(finish, "value") else finish,
        actual_model=metadata.actual_model, actual_provider=metadata.actual_provider,
        request_id=metadata.request_id, usage=metadata.usage,
    )


class GeminiClient:
    def __init__(self, api_key: str | None = None):
        try:
            from google import genai
        except ImportError:
            raise ModelConfigurationError("Install google-genai to use Gemini.") from None
        try:
            self._client = genai.Client(api_key=api_key) if api_key else genai.Client()
        except ValueError:
            raise ModelConfigurationError("Set GEMINI_API_KEY or GOOGLE_API_KEY to use Gemini.") from None

    def close(self):
        self._client.close()

    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        from google.genai import errors, types

        config = {
            "system_instruction": request.system,
            "http_options": types.HttpOptions(
                timeout=max(1, int(timeout * 1000)), retry_options=types.HttpRetryOptions(attempts=1),
            ),
            "max_output_tokens": request.max_output_tokens,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if request.schema is not None:
            config.update(response_mime_type="application/json", response_json_schema=request.schema)
        tools = []
        if request.tools:
            tools.append(types.Tool(function_declarations=[types.FunctionDeclaration(
                name=tool.name, description=tool.description, parameters_json_schema=tool.parameters,
            ) for tool in request.tools]))
        if request.search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        if tools:
            config["tools"] = tools
        if request.tools:
            mode = {"auto": "AUTO", "required": "ANY", "none": "NONE"}[request.tool_choice]
            config["tool_config"] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode))
        try:
            response = self._client.models.generate_content(
                model=request.model, contents=_contents(request.messages, types),
                config=types.GenerateContentConfig(**config),
            )
        except errors.APIError as error:
            raise ModelRequestError(
                f"Gemini request failed (HTTP {error.code}).", code=error.code, retryable=error.code in _RETRYABLE_CODES,
                retry_after=retry_after(getattr(error.response, "headers", None)),
            ) from None
        except _transport_errors():
            raise ModelRequestError("Could not complete the request to Gemini.", retryable=True) from None
        return _response(response)
