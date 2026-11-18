import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.build import run_build
from backend.files import MAX_READ_CHARS, MAX_READ_LINES, FileToolError, patch_file, read_file
from backend.providers.openrouter import OpenRouterClient
from backend.workspace import init_workspace


_PLANNED_EXAMPLE = Path(__file__).resolve().parents[2] / "example-solver-plans" / "ev-fleet-dispatch-optimization"


class BuildEditingTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = init_workspace("Test the workspace file tools.", root=Path(directory.name))
        self.source = self.workspace.resolve("main.py")

    def test_read_pages_resume_across_line_and_character_limits(self):
        short_lines = "line\n" * MAX_READ_LINES
        long_line = "ç" * (MAX_READ_CHARS + 1) + "\n"
        self.source.write_bytes((short_lines + long_line + "last\n").encode())

        first = read_file(self.workspace, "main.py")
        long_line_start = first["next_line"]
        second = read_file(self.workspace, "main.py", start_line=long_line_start, end_line=long_line_start)
        third = read_file(self.workspace, "main.py", start_line=second["next_line"], start_column=second["next_column"])

        self.assertEqual(first["content"], short_lines)
        self.assertEqual(first["end_line"], MAX_READ_LINES)
        self.assertEqual(len(second["content"]), MAX_READ_CHARS)
        self.assertEqual(second["content"] + third["content"], long_line + "last\n")
        self.assertNotIn("next_line", third)

    def test_patch_only_changes_a_unique_exact_match(self):
        original = b"a = 1\r\nb = 1\r\n"
        self.source.write_bytes(original)
        with self.assertRaisesRegex(FileToolError, "more than once"):
            patch_file(self.workspace, "main.py", "= 1", "= 2")
        with self.assertRaisesRegex(FileToolError, "not found"):
            patch_file(self.workspace, "main.py", "stale", "new")
        with self.assertRaisesRegex(FileToolError, "read-only"):
            patch_file(self.workspace, "prompt.md", "Test", "Change")
        self.assertEqual(self.source.read_bytes(), original)

        status = patch_file(self.workspace, "main.py", "a = 1\r\n", "a = 2\r\n")
        self.assertEqual(self.source.read_bytes(), b"a = 2\r\nb = 1\r\n")
        self.assertIsNone(status["syntax_error"])
        self.assertNotIn("content", status)

    def test_usage_ledgers_cannot_be_patched_by_the_builder(self):
        for name in ("model_usage.json", "model_usage.jsonl"):
            self.workspace.resolve(name).write_text("usage", encoding="utf-8")
            with self.subTest(name=name), self.assertRaisesRegex(FileToolError, "read-only"):
                patch_file(self.workspace, name, "usage", "changed")
            self.assertEqual(self.workspace.resolve(name).read_text(encoding="utf-8"), "usage")

    @patch("backend.build.MAX_HISTORY_CHARS", 2_500)
    @patch("backend.providers.openrouter.urlopen")
    def test_repair_compacts_old_history_and_preserves_recent_tool_pairs(self, send):
        for artifact in ("problem.json", "plan.json"):
            self.workspace.resolve(artifact).write_bytes((_PLANNED_EXAMPLE / artifact).read_bytes())
        self.source.write_bytes(b"value = 1\n")

        def response(*calls, reasoning=""):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}
                    for call_id, name, args in calls
                ],
                "reasoning_details": [{"type": "reasoning.encrypted", "data": reasoning}],
            }
            return BytesIO(json.dumps({"choices": [{"finish_reason": "tool_calls", "message": message}]}).encode())

        first = ("first-patch", "patch_file", {"path": "main.py", "old": "value = 1", "new": "value = 2"})
        read = ("read", "read_file", {"path": "main.py", "start_line": 1, "end_line": 1})
        finish = ("finish", "finish", {"summary": "Updated value"})
        stale = ("stale-patch", "patch_file", {"path": "main.py", "old": "value = 1", "new": "value = 3"})
        corrected = ("final-patch", "patch_file", {"path": "main.py", "old": "value = 2", "new": "value = 3"})
        send.side_effect = [
            response(first, reasoning="old" * 1_000), response(read, reasoning="keep verbatim"),
            response(finish, stale), response(finish, corrected),
        ]

        result = run_build(
            self.workspace, feedback="Change the calculation", model="fixture/model", client=OpenRouterClient("fixture-key"),
        )

        self.assertEqual(result.turns, 4)
        self.assertEqual(self.source.read_bytes(), b"value = 3\n")
        messages = json.loads(send.call_args_list[2].args[0].data)["messages"]
        self.assertIn("Change the calculation", messages[1]["content"])
        self.assertIn("patch_file main.py (ok)", messages[1]["content"])
        self.assertNotIn("first-patch", json.dumps(messages))
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "read")
        self.assertEqual(messages[2]["reasoning_details"][0]["data"], "keep verbatim")
        self.assertEqual(messages[3]["tool_call_id"], "read")
        self.assertEqual(json.loads(messages[3]["content"])["content"], "value = 2\n")

        messages = json.loads(send.call_args_list[3].args[0].data)["messages"]
        self.assertEqual([reply["tool_call_id"] for reply in messages[-2:]], ["finish", "stale-patch"])
        self.assertIn("Text to replace was not found", json.loads(messages[-2]["content"])["error"])
