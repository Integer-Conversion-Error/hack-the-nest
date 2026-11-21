"""HTTP boundaries for optional, data-only problem formulation; no public calls."""

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import formulation as F
import server
from backend.model_types import ModelRequestError
from test_formulation_service import Client, DESCRIPTION, PROJECTS, envelope


class QuietHandler(server.Handler):
    def log_message(self, *_args):
        pass


class FormulationHttpTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "MODEL_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "fixture-only-key", "OPENROUTER_INTERPRET_MODEL": "fixture/interpret",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        for target, value in (("server.backend_url", ""), ("server.native_pipeline_configured", False),
                              ("server.local_solver_available", False), ("server.planner_configured", False), ("server.maps_config", {})):
            replacement = patch(target, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        deny_provider = patch("backend.model_provider._LazyClient.generate", side_effect=AssertionError("Unexpected public model request"))
        self.network = deny_provider.start()
        self.addCleanup(deny_provider.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_port}"

    def request(self, path="/api/problems/formulate", value=None, *, raw=None, method="POST", content_type="application/json", headers=None):
        if raw is None and value is not None:
            raw = json.dumps(value).encode("utf-8")
        outgoing = Request(self.base + path, raw, {"Content-Type": content_type, **(headers or {})}, method=method)
        try:
            response = urlopen(outgoing, timeout=3)
        except HTTPError as failure:
            response = failure
        with response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
            return response.status, json.load(response)

    def test_endpoint_uses_actual_service_and_returns_only_validated_draft_without_running_any_solver(self):
        client = Client(envelope())
        before_views, before_runs = dict(server.VIEWS), dict(server.PIPELINE_RUNS)
        with patch("server.formulate_problem", side_effect=lambda payload: F.formulate_problem(payload, client=client)) as service, \
                patch("server.run_plan", side_effect=AssertionError("Solver must not run")), \
                patch("server.start_pipeline_run", side_effect=AssertionError("Pipeline must not run")):
            status, draft = self.request(value={"description": DESCRIPTION}, headers={"Host": "preview.example.ts.net"})
        self.assertEqual(status, 200)
        self.assertEqual(draft["status"], "ready")
        self.assertEqual(draft["inputs"], PROJECTS)
        self.assertEqual(len(client.requests), 1)
        service.assert_called_once_with({"description": DESCRIPTION})
        self.assertEqual(server.VIEWS, before_views)
        self.assertEqual(server.PIPELINE_RUNS, before_runs)
        self.assertNotIn("inputs_json", draft)
        self.assertNotIn("fixture-only-key", json.dumps(draft))
        self.network.assert_not_called()

    def test_clarify_and_unsupported_are_successful_data_responses_with_no_inputs(self):
        client = Client(envelope("clarify"), envelope("unsupported"))
        with patch("server.formulate_problem", side_effect=lambda payload: F.formulate_problem(payload, client=client)):
            status, clarify = self.request(value={"description": "Schedule energy; I have not provided loads or prices."})
            self.assertEqual(status, 200)
            self.assertEqual(set(clarify), {"status", "question"})
            status, unsupported = self.request(value={"description": "Daily project budgets with exclusion groups are required."})
            self.assertEqual(status, 200)
            self.assertEqual(set(unsupported), {"status", "message"})
        self.network.assert_not_called()

    def test_content_type_size_json_and_request_validation_fail_before_provider_work(self):
        with patch("server.formulate_problem", wraps=F.formulate_problem) as service:
            for raw, kind, expected in [(b'{"description":"work"}', "text/plain", 415),
                                        (b"x" * (F.MAX_REQUEST_BYTES + 1), "application/json", 400),
                                        (b"{", "application/json", 400), (b"[]", "application/json", 400),
                                        (b"", "application/json", 400)]:
                with self.subTest(kind=kind, size=len(raw)):
                    status, error = self.request(raw=raw, content_type=kind)
                    self.assertEqual(status, expected)
                    self.assertIsInstance(error["error"], str)
            service.assert_not_called()
            for value in [{"description": ""}, {"description": "x" * 8001}, {"description": "work", "code": "exec()"},
                          {"description": "work", "clarifications": [{"question": "?", "answer": "a"}] * 9}]:
                with self.subTest(value=str(value)[:80]):
                    status, error = self.request(value=value)
                    self.assertEqual(status, 400)
                    self.assertIsInstance(error["error"], str)
        self.network.assert_not_called()

    def test_provider_failure_invalid_draft_and_retry_keep_safe_json_error_contract(self):
        client = Client(ModelRequestError("private-provider-payload", code=503, retryable=True),
                        envelope(inputs_json=json.dumps({**PROJECTS, "unsupported_constraint": True})), envelope())
        with patch("server.formulate_problem", side_effect=lambda payload: F.formulate_problem(payload, client=client)):
            for expected in (503, 502, 200):
                status, response = self.request(value={"description": DESCRIPTION})
                self.assertEqual(status, expected)
                if status != 200:
                    self.assertEqual(set(response), {"error"})
                    self.assertNotIn("private-provider-payload", response["error"])
                else:
                    self.assertEqual(response["status"], "ready")
        self.assertEqual(len(client.requests), 3)
        self.network.assert_not_called()

    def test_unavailable_formulation_leaves_manual_workbench_configuration_available(self):
        with patch("server.formulation_configured", return_value=False), patch("formulation.formulation_configured", return_value=False):
            status, config = self.request("/api/config", method="GET")
            self.assertEqual(status, 200)
            self.assertIs(config["formulation_configured"], False)
            self.assertIn("maps", config)
            status, error = self.request(value={"description": DESCRIPTION})
            self.assertEqual(status, 503)
            self.assertIn("manually", error["error"])
        with patch("server.formulation_configured", return_value=True):
            status, config = self.request("/api/config", method="GET")
            self.assertEqual(status, 200)
            self.assertIs(config["formulation_configured"], True)
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
