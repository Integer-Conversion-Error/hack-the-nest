"""OpenRouter Chat Completions transport; retries belong to the shared runtime."""

import copy
import json
import math
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.model_types import Citation, Message, ModelRequest, ModelRequestError, ModelResponse, ToolCall, Usage
from backend.providers import retry_after


_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_RETRYABLE_CODES = (408, 429, 500, 502, 503, 504)
_ERROR_HINTS = {
    400: "Check whether the selected model supports the requested tools and JSON schema.",
    401: "Check OPENROUTER_API_KEY.",
    402: "Check your OpenRouter credit balance.",
    403: "Check the permissions and limits on your OpenRouter key.",
    404: "Check the selected model and whether an endpoint supports the required parameters.",
}


def _strict_schema(schema):
    """Normalize object schemas for providers that enforce strict JSON outputs."""
    if not isinstance(schema, dict):
        return schema
    result = {key: copy.deepcopy(value) for key, value in schema.items() if key != "default"}
    # Property/definition names are user data; a property named "default" must
    # survive even though a JSON Schema default annotation must be removed.
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        if isinstance(result.get(key), dict):
            result[key] = {name: _strict_schema(child) for name, child in result[key].items()}
    for key in ("items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"):
        if isinstance(result.get(key), dict):
            result[key] = _strict_schema(result[key])
    for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
        if isinstance(result.get(key), list):
            result[key] = [_strict_schema(child) for child in result[key]]
    if "properties" in result:
        result["additionalProperties"] = False
        result["required"] = list(result["properties"])
    return result


def _count(section, key):
    value = section.get(key) if isinstance(section, dict) else None
    return value if type(value) is int and value >= 0 else None


def _cost(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    return None


def _service_error(code=None, *, headers=None):
    message = f"OpenRouter request failed (HTTP {code})." if code else "OpenRouter could not complete generation."
    if code in _ERROR_HINTS:
        message += " " + _ERROR_HINTS[code]
    return ModelRequestError(
        message, code=code, retryable=code in _RETRYABLE_CODES,
        retry_after=retry_after(headers),
    )


def _messages(request: ModelRequest):
    messages = [{"role": "system", "content": request.system}] if request.system else []
    for message in request.messages:
        if message.role == "assistant" and "openrouter" in message.provider_state:
            messages.append(copy.deepcopy(message.provider_state["openrouter"]))
            continue
        if message.role == "tool":
            if not message.tool_call_id:
                raise ModelRequestError("An OpenRouter tool result was missing its tool call ID.")
            messages.append({
                "role": "tool", "tool_call_id": message.tool_call_id,
                "content": json.dumps(message.result, ensure_ascii=False, separators=(",", ":")),
            })
            continue
        item = {"role": message.role, "content": message.text}
        if message.tool_calls:
            item["tool_calls"] = [{
                "id": call.id, "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            } for call in message.tool_calls]
        messages.append(item)
    return messages


def _payload(request: ModelRequest):
    payload = {
        "model": request.model, "messages": _messages(request),
        "max_tokens": request.max_output_tokens,
    }
    if request.schema is not None:
        title = request.schema.get("title", "response").lstrip("_") or "response"
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": title, "strict": True, "schema": _strict_schema(request.schema)},
        }
    tools = [{
        "type": "function", "function": {
            "name": tool.name, "description": tool.description, "parameters": tool.parameters,
        },
    } for tool in request.tools]
    if request.search:
        tools.append({"type": "openrouter:web_search"})
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = request.tool_choice
    if request.schema is not None or tools:
        payload["provider"] = {"require_parameters": True}
    return payload


def _error_code(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _metadata(data, *, headers=None):
    data = data if isinstance(data, dict) else {}
    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    completion_details = usage.get("completion_tokens_details") if isinstance(usage, dict) else None
    return ModelResponse(
        actual_model=data.get("model") if isinstance(data.get("model"), str) else None,
        actual_provider=data.get("provider") if isinstance(data.get("provider"), str) else None,
        request_id=data.get("id") if isinstance(data.get("id"), str) else (
            headers.get("x-request-id") if headers is not None else None
        ),
        usage=Usage(
            input_tokens=_count(usage, "prompt_tokens"), output_tokens=_count(usage, "completion_tokens"),
            reasoning_tokens=_count(completion_details, "reasoning_tokens"),
            cached_tokens=_count(prompt_details, "cached_tokens"), total_tokens=_count(usage, "total_tokens"),
            cost=_cost(usage.get("cost")) if isinstance(usage, dict) else None,
        ),
    )


def _response(data, *, headers=None):
    metadata = _metadata(data, headers=headers)
    try:
        return _completion(data, metadata, headers=headers)
    except ModelRequestError as error:
        # A malformed or failed completion can still be billable. Keep only its
        # safe accounting metadata; no provider payload enters the error ledger.
        error.response = metadata
        raise


def _completion(data, metadata, *, headers=None):
    if not isinstance(data, dict):
        raise ModelRequestError("OpenRouter returned an invalid response.")
    if data.get("error") is not None:
        error = data["error"]
        raise _service_error(_error_code(error.get("code")) if isinstance(error, dict) else None, headers=headers)
    try:
        choice = data["choices"][0]
        if choice.get("error") is not None or choice.get("finish_reason") == "error":
            error = choice.get("error")
            raise _service_error(_error_code(error.get("code")) if isinstance(error, dict) else None, headers=headers)
        raw = choice["message"]
        content = raw.get("content")
        calls = raw.get("tool_calls") or []
        annotations = raw.get("annotations") or []
        finish_reason = choice.get("finish_reason")
        if content is not None and not isinstance(content, str):
            raise TypeError
        if not isinstance(calls, list) or not isinstance(annotations, list):
            raise TypeError
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise TypeError
    except (AttributeError, IndexError, KeyError, TypeError):
        raise ModelRequestError("OpenRouter returned an invalid completion.") from None

    tool_calls = []
    ids = set()
    for call in calls:
        try:
            call_id = call["id"]
            function = call["function"]
            name = function["name"]
            arguments = json.loads(function["arguments"])
            if not isinstance(call_id, str) or not call_id or call_id in ids:
                raise ValueError
            if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ModelRequestError("OpenRouter returned a malformed tool call.") from None
        ids.add(call_id)
        tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

    citations = []
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
            continue
        citation = annotation.get("url_citation", annotation)
        if not isinstance(citation, dict):
            continue
        url, title = citation.get("url"), citation.get("title")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            citations.append(Citation(url=url, title=title if isinstance(title, str) else None))

    # Only replay assistant fields accepted by the API. Keep opaque reasoning and
    # original argument strings verbatim: some providers cryptographically sign them.
    replay = {key: copy.deepcopy(raw[key]) for key in (
        "role", "content", "tool_calls", "reasoning_details", "reasoning", "reasoning_content", "annotations",
    ) if key in raw}
    replay["role"] = "assistant"
    return ModelResponse(
        text=content or "", tool_calls=tool_calls,
        message=Message(role="assistant", text=content, tool_calls=tool_calls, provider_state={"openrouter": replay}),
        citations=citations, finish_reason=finish_reason,
        actual_model=metadata.actual_model, actual_provider=metadata.actual_provider,
        request_id=metadata.request_id, usage=metadata.usage,
    )


class OpenRouterClient:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def close(self):
        """Each request closes its connection before returning."""

    def generate(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        outgoing = Request(_CHAT_URL, data=json.dumps(_payload(request)).encode("utf-8"), headers={
            "Content-Type": "application/json",
        })
        outgoing.add_unredirected_header("Authorization", f"Bearer {self._api_key}")
        try:
            with urlopen(outgoing, timeout=timeout) as response:
                data = json.load(response)
                headers = getattr(response, "headers", None)
        except HTTPError as error:
            failure = _service_error(error.code, headers=error.headers)
            error.close()
            raise failure from None
        except (OSError, HTTPException):
            raise ModelRequestError("Could not complete the request to OpenRouter.", retryable=True) from None
        except (UnicodeError, ValueError):
            raise ModelRequestError("OpenRouter returned an invalid JSON response.") from None
        return _response(data, headers=headers)
