"""Provider wire contracts; no credentials or live model requests required."""

import json
import subprocess
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from backend.model_types import Message, ModelRequest, ModelRequestError, Tool, ToolCall
from backend.providers.openrouter import OpenRouterClient


def completion(**fields):
    data = {
        "id": "generation-fixture", "model": "vendor/actual-model", "provider": "Example provider",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Done"}}],
    }
    data.update(fields)
    return BytesIO(json.dumps(data).encode())


def request(**fields):
    values = dict(model="vendor/requested-model", system="Instructions", stage="build", messages=[Message("user", "Request")])
    values.update(fields)
    return ModelRequest(**values)


class OpenRouterAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = OpenRouterClient("fixture-key")

    @patch("backend.providers.openrouter.urlopen")
    def test_schema_output_limit_and_actual_usage_metadata(self, send):
        send.return_value = completion(usage={
            "prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25, "cost": 0.001,
            "prompt_tokens_details": {"cached_tokens": 2}, "completion_tokens_details": {"reasoning_tokens": 5},
        })
        schema = {"title": "Result", "type": "object", "properties": {
            "default": {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None},
        }}
        response = self.client.generate(request(schema=schema, max_output_tokens=123), timeout=2.5)
        outgoing = send.call_args.args[0]
        payload = json.loads(outgoing.data)
        self.assertEqual(outgoing.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(outgoing.get_header("Authorization"), "Bearer fixture-key")
        self.assertEqual(send.call_args.kwargs["timeout"], 2.5)
        self.assertEqual(payload["max_tokens"], 123)
        self.assertTrue(payload["provider"]["require_parameters"])
        normalized = payload["response_format"]["json_schema"]
        self.assertTrue(normalized["strict"])
        self.assertEqual(normalized["schema"]["required"], ["default"])
        self.assertNotIn("default", normalized["schema"]["properties"]["default"])
        self.assertEqual(schema["properties"]["default"]["default"], None)
        self.assertEqual((response.actual_model, response.actual_provider, response.request_id),
                         ("vendor/actual-model", "Example provider", "generation-fixture"))
        usage = response.usage
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.reasoning_tokens, usage.cached_tokens,
                          usage.total_tokens, usage.cost), (10, 15, 5, 2, 25, 0.001))

    @patch("backend.providers.openrouter.urlopen")
    def test_tool_ids_original_arguments_and_reasoning_survive_round_trip(self, send):
        calls = [
            {"id": "a", "type": "function", "function": {"name": "read", "arguments": '{ "path": "a.py" }'}},
            {"id": "b", "type": "function", "function": {"name": "read", "arguments": '{"path":"b.py"}'}},
        ]
        reasoning = [{"type": "reasoning.encrypted", "data": "opaque-signature"}]
        raw = {"role": "assistant", "content": None, "tool_calls": calls, "reasoning_details": reasoning}
        send.side_effect = [completion(choices=[{"finish_reason": "tool_calls", "message": raw}]), completion()]
        tools = [Tool("read", "Read a file", {"type": "object"})]
        initial = request(tools=tools, tool_choice="required")
        response = self.client.generate(initial, timeout=4)
        self.assertEqual([(call.id, call.arguments) for call in response.tool_calls],
                         [("a", {"path": "a.py"}), ("b", {"path": "b.py"})])
        continuation = request(tools=tools, messages=[*initial.messages, response.message,
            Message("tool", tool_call_id="a", name="read", result={"text": "a"}),
            Message("tool", tool_call_id="b", name="read", result={"text": "b"}),
        ])
        self.client.generate(continuation, timeout=3)
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["messages"][2], raw)
        self.assertEqual([item["tool_call_id"] for item in payload["messages"][3:]], ["a", "b"])
        self.assertEqual(json.loads(send.call_args_list[0].args[0].data)["tool_choice"], "required")

    @patch("backend.providers.openrouter.urlopen")
    def test_search_and_only_web_citations(self, send):
        send.return_value = completion(choices=[{"message": {"content": "Research", "annotations": [
            {"type": "url_citation", "url_citation": {"url": "https://example.org", "title": "Evidence"}},
            {"type": "url_citation", "url_citation": {"url": "file:///secret"}},
        ]}, "finish_reason": "stop"}])
        response = self.client.generate(request(stage="research", search=True), timeout=4)
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload["tools"], [{"type": "openrouter:web_search"}])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual([(item.url, item.title) for item in response.citations], [("https://example.org", "Evidence")])

    @patch("backend.providers.openrouter.urlopen")
    def test_http_errors_expose_retry_after_without_payload_or_credentials(self, send):
        send.side_effect = HTTPError("https://openrouter.ai", 429, "private provider message", {"Retry-After": "3"},
                                     BytesIO(b"private provider payload fixture-key"))
        with self.assertRaises(ModelRequestError) as raised:
            self.client.generate(request(), timeout=4)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after, 3)
        self.assertNotIn("private", str(raised.exception))
        self.assertNotIn("fixture-key", str(raised.exception))
        self.assertEqual(send.call_count, 1)

    @patch("backend.providers.openrouter.urlopen")
    def test_nonretryable_and_in_band_failures(self, send):
        send.side_effect = [HTTPError("https://openrouter.ai", 401, "Unauthorized", {}, BytesIO()),
                            completion(error={"code": 503, "message": "private"})]
        with self.assertRaises(ModelRequestError) as unauthorized:
            self.client.generate(request(), timeout=4)
        self.assertFalse(unauthorized.exception.retryable)
        self.assertIn("OPENROUTER_API_KEY", str(unauthorized.exception))
        with self.assertRaises(ModelRequestError) as unavailable:
            self.client.generate(request(), timeout=4)
        self.assertTrue(unavailable.exception.retryable)
        self.assertEqual(unavailable.exception.code, 503)

    @patch("backend.providers.openrouter.urlopen")
    def test_rejects_duplicate_or_malformed_tool_calls(self, send):
        call = {"id": "a", "type": "function", "function": {"name": "read", "arguments": '{}'}}
        send.side_effect = [completion(choices=[{"message": {"tool_calls": [call, call]}}],
                                      usage={"total_tokens": 9, "cost": 0.005}),
                            completion(choices=[{"message": {"tool_calls": [dict(call, function={"name": "read", "arguments": '[]'})]}}])]
        for _ in range(2):
            with self.assertRaisesRegex(ModelRequestError, "malformed tool call") as raised:
                self.client.generate(request(), timeout=4)
            metadata = raised.exception.response
            self.assertIsNone(metadata.message)
            self.assertEqual(metadata.request_id, "generation-fixture")
            if _ == 0:
                self.assertEqual((metadata.usage.total_tokens, metadata.usage.cost), (9, 0.005))

    @patch("backend.providers.openrouter.urlopen")
    def test_transport_and_invalid_json_are_sanitized(self, send):
        send.side_effect = [TimeoutError("secret fixture-key"), BytesIO(b"not-json private")]
        with self.assertRaises(ModelRequestError) as timeout:
            self.client.generate(request(), timeout=0.5)
        self.assertTrue(timeout.exception.retryable)
        self.assertNotIn("secret", str(timeout.exception))
        with self.assertRaisesRegex(ModelRequestError, "invalid JSON"):
            self.client.generate(request(), timeout=0.5)

    @patch("backend.providers.openrouter.urlopen")
    def test_invalid_usage_values_stay_unknown_instead_of_poisoning_accounting(self, send):
        send.return_value = completion(usage={
            "prompt_tokens": True, "completion_tokens": -5, "total_tokens": float("inf"), "cost": float("nan"),
            "prompt_tokens_details": {"cached_tokens": "4"}, "completion_tokens_details": {"reasoning_tokens": 2.5},
        })
        usage = self.client.generate(request(), timeout=1).usage
        self.assertEqual(vars(usage), {key: None for key in vars(usage)})

    @patch("backend.providers.openrouter.urlopen")
    def test_missing_tool_id_is_rejected_before_network(self, send):
        with self.assertRaisesRegex(ModelRequestError, "missing its tool call ID"):
            self.client.generate(request(messages=[Message("tool", name="read", result={})]), timeout=1)
        send.assert_not_called()

    def test_openrouter_import_does_not_require_google_sdk(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'google' or name.startswith('google.'):
        raise AssertionError('OpenRouter imported the Google SDK')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from backend.providers.openrouter import OpenRouterClient
OpenRouterClient('fixture').close()
"""
        outcome = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                                 capture_output=True, text=True)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)


class GeminiAdapterTests(unittest.TestCase):
    def setUp(self):
        from backend.providers.gemini import GeminiClient
        self.client = GeminiClient.__new__(GeminiClient)
        self.client._client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock()))

    def test_signed_native_content_tools_and_usage_round_trip(self):
        from google.genai import types
        native = types.Content(role="model", parts=[
            types.Part(text="Thought", thought=True, thought_signature=b"signed-thought"),
            types.Part(function_call=types.FunctionCall(id="a", name="read", args={"path": "a.py"})),
            types.Part(function_call=types.FunctionCall(name="read", args={"path": "b.py"})),
        ])
        self.client._client.models.generate_content.side_effect = [
            types.GenerateContentResponse(candidates=[types.Candidate(content=native, finish_reason="STOP")],
                model_version="gemini-actual", response_id="response-fixture", usage_metadata={
                    "prompt_token_count": 10, "candidates_token_count": 5, "thoughts_token_count": 2,
                    "total_token_count": 17, "cached_content_token_count": 3,
                }),
            types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
                role="model", parts=[types.Part(text="Done")]), finish_reason="STOP")]),
        ]
        first = self.client.generate(request(tools=[Tool("read", "Read", {"type": "object"})], tool_choice="required"), timeout=2.5)
        self.assertEqual(first.text, "")
        self.assertEqual([call.id for call in first.tool_calls], ["a", "call_1"])
        self.assertEqual((first.usage.input_tokens, first.usage.output_tokens, first.usage.reasoning_tokens,
                          first.usage.cached_tokens, first.usage.total_tokens), (10, 7, 2, 3, 17))
        self.assertEqual((first.actual_model, first.actual_provider, first.request_id), ("gemini-actual", "google", "response-fixture"))
        second = self.client.generate(request(messages=[Message("user", "Request"), first.message,
            Message("tool", tool_call_id="a", name="read", result={"text": "a"}),
            Message("tool", tool_call_id="call_1", name="read", result={"text": "b"}),
        ]), timeout=1)
        self.assertEqual(second.text, "Done")
        sent = self.client._client.models.generate_content.call_args.kwargs["contents"]
        self.assertIs(sent[1], native)
        self.assertEqual(sent[1].parts[0].thought_signature, b"signed-thought")
        self.assertEqual([part.function_response.id for part in sent[2].parts], ["a", None])
        config = self.client._client.models.generate_content.call_args_list[0].kwargs["config"]
        self.assertEqual(config.http_options.timeout, 2500)
        self.assertEqual(config.http_options.retry_options.attempts, 1)
        self.assertTrue(config.automatic_function_calling.disable)
        self.assertEqual(config.max_output_tokens, 8192)
        self.assertEqual(config.tool_config.function_calling_config.mode.value, "ANY")

    def test_schema_search_and_citations(self):
        from google.genai import types
        self.client._client.models.generate_content.return_value = types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text='{"ok":true}')]), finish_reason="STOP",
            grounding_metadata=types.GroundingMetadata(grounding_chunks=[
                types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://example.org", title="Evidence")),
                types.GroundingChunk(web=types.GroundingChunkWeb(uri="file:///secret")),
            ]),
        )])
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        response = self.client.generate(request(schema=schema, search=True), timeout=3)
        config = self.client._client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.response_json_schema, schema)
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIsNotNone(config.tools[0].google_search)
        self.assertEqual([(item.url, item.title) for item in response.citations], [("https://example.org", "Evidence")])

    def test_gemini_errors_are_sanitized_and_do_not_retry_in_adapter(self):
        from google.genai.errors import APIError
        self.client._client.models.generate_content.side_effect = APIError(
            429, {"message": "private fixture-key"}, response=SimpleNamespace(headers={"Retry-After": "2"}),
        )
        with self.assertRaises(ModelRequestError) as raised:
            self.client.generate(request(), timeout=1)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(raised.exception.retry_after, 2)
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(self.client._client.models.generate_content.call_count, 1)

    def test_empty_gemini_response_retains_usage_for_runtime_accounting(self):
        from google.genai import types
        self.client._client.models.generate_content.return_value = types.GenerateContentResponse(
            candidates=[], usage_metadata={"prompt_token_count": 4, "total_token_count": 4},
        )
        response = self.client.generate(request(), timeout=1)
        self.assertEqual(response.text, "")
        self.assertEqual(response.tool_calls, [])
        self.assertEqual(response.usage.total_tokens, 4)

    def test_gemini_transport_failure_is_sanitized(self):
        self.client._client.models.generate_content.side_effect = TimeoutError("private key")
        with self.assertRaises(ModelRequestError) as raised:
            self.client.generate(request(), timeout=0.5)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private", str(raised.exception))

    def test_malformed_gemini_tool_call_retains_accounting_only(self):
        from google.genai import types
        self.client._client.models.generate_content.return_value = types.GenerateContentResponse(
            response_id="malformed-fixture", model_version="gemini-actual",
            usage_metadata={"total_token_count": 8}, candidates=[types.Candidate(content=types.Content(
                role="model", parts=[types.Part(function_call=types.FunctionCall(name="", args={}))],
            ))],
        )
        with self.assertRaisesRegex(ModelRequestError, "malformed tool call") as raised:
            self.client.generate(request(), timeout=1)
        metadata = raised.exception.response
        self.assertEqual((metadata.request_id, metadata.usage.total_tokens), ("malformed-fixture", 8))
        self.assertIsNone(metadata.message)
        self.assertEqual(metadata.text, "")


if __name__ == "__main__":
    unittest.main()
