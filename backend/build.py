"""Build step 1 of the agent loop: the agent writes the solver's Python files.

The model reads the problem statement and the approved plan, then creates, reads,
updates and deletes files in the workspace through the tools in
backend/files.py until it calls `finish`. It cannot run code here; running
main.py belongs to the Test step.

Reads   <workspace>/problem.json, plan.json
Writes  <workspace>/main.py and the other solver modules (by the agent)
        <workspace>/qr_runtime.py  - Quantum Rings helper, copied in, read-only
        <workspace>/build.json     - record of this build

Usage:
    python -m backend.build <workspace-id>
    python -m backend.build <workspace-id> --feedback "main.py crashes with ..."
"""

import argparse
import json
import shutil
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from backend import files
from backend.model_provider import generate, model_name, model_run
from backend.model_types import Message, ModelError, Tool
from backend.progress import ProgressCallback, progress_step, report
from backend.plan import PLAN_JSON, load_plan
from backend.problem_statement import ENV_FILE, PROBLEM_JSON, load_problem_statement
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, open_workspace

BUILD_JSON = "build.json"
ENTRY_POINT = "main.py"
RUNTIME_HELPER = "qr_runtime.py"
TEMPLATES = Path(__file__).resolve().parent / "templates"

DEFAULT_MODEL = "gemini-4-hydrogen"  # override with the GEMINI_MODEL environment variable
DEFAULT_MAX_TURNS = 40
MAX_HISTORY_TURNS = 5
MAX_HISTORY_CHARS = 32_000
MAX_CHECKPOINT_NOTES = 12
_WRITE_TOOLS = frozenset({"create_file", "update_file", "patch_file", "delete_file"})

_PATH = {"type": "string", "description": "File path relative to the workspace, e.g. 'main.py'."}
_CONTENT = {"type": "string", "description": "The complete text of the file."}


def _schema(properties: dict, *, optional: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": [key for key in properties if key not in optional]}


# name -> (tool description, parameter schema, function to call or None for finish)
TOOLS: dict[str, tuple[str, dict, Callable | None]] = {
    "list_files": ("List every file in the workspace with its size.", _schema({}), files.list_files),
    "read_file": (
        "Read an excerpt of a file. Defaults to the first page. Pass any returned next_line and next_column "
        "as start_line and start_column to continue.",
        _schema({
            "path": _PATH,
            "start_line": {"type": "integer", "description": "First line to read, counting from 1."},
            "end_line": {"type": "integer", "description": "Last line requested, inclusive."},
            "start_column": {"type": "integer", "description": "Starting character of the first line, counting from 1."},
        }, optional=("start_line", "end_line", "start_column")),
        files.read_file,
    ),
    "create_file": (
        "Create a new file. Fails if it already exists. Reports a Python syntax error if there is one.",
        _schema({"path": _PATH, "content": _CONTENT}),
        files.create_file,
    ),
    "update_file": (
        "Replace the whole content of an existing file. Reports a Python syntax error if there is one.",
        _schema({"path": _PATH, "content": _CONTENT}),
        files.update_file,
    ),
    "patch_file": (
        "Make a targeted edit by replacing a unique, exact text match in an existing file.",
        _schema({
            "path": _PATH,
            "old": {"type": "string", "description": "Exact existing text. Add enough surrounding text for a unique match."},
            "new": {"type": "string", "description": "Replacement text. An empty string removes the matching text."},
        }),
        files.patch_file,
    ),
    "delete_file": ("Delete a file you created.", _schema({"path": _PATH}), files.delete_file),
    "finish": (
        "Declare the implementation complete.",
        _schema({"summary": {"type": "string", "description": "What each file does and anything left out."}}),
        None,
    ),
}


TOOL_LABELS = {
    "list_files": "List workspace files",
    "read_file": "Read a workspace file",
    "create_file": "Create a solver file",
    "update_file": "Update a solver file",
    "patch_file": "Patch a solver file",
    "delete_file": "Delete a solver file",
    "finish": "Check that the solver files are ready",
}


class BuildError(Exception):
    """The Build step stopped before the agent finished the implementation."""


class BuildResult(BaseModel):
    model: str
    turns: int
    summary: str
    files: list[str]
    feedback: str | None = None


def build_contract(planning, workspace: Workspace | None = None) -> str:
    """The rules every build must follow, shared by the builder and the plan reviewer."""
    contract = load_prompt("build.contract", max_qubits=planning.max_qubits, worker_seconds=planning.worker_seconds)
    if workspace is not None and workspace.meta.get("scene_type") == "ev_fleet":
        contract += "\n\n" + load_prompt("build.ev_fleet")
    return contract


def source_files(workspace: Workspace) -> list[str]:
    """Paths of the Python files the agent wrote."""
    return [
        entry["path"]
        for entry in files.list_files(workspace)["files"]
        if entry["path"].endswith(".py") and not entry["read_only"]
    ]


def _call_tool(workspace: Workspace, name: str, args: dict) -> dict:
    """Run one file tool. Problems come back as {"error": ...} so the agent can correct itself."""
    if name not in TOOLS or TOOLS[name][2] is None:
        return {"error": f"Unknown tool {name!r}. Available: {', '.join(TOOLS)}."}
    try:
        return TOOLS[name][2](workspace, **args)
    except files.FileToolError as error:
        return {"error": str(error)}
    except TypeError:
        return {"error": f"Wrong arguments for {name}. Expected: {', '.join(TOOLS[name][1]['properties']) or 'none'}."}


def unfinished_issues(workspace: Workspace) -> list[str]:
    """Reasons the workspace cannot count as a finished build yet."""
    issues = []
    if not workspace.resolve(ENTRY_POINT).is_file():
        issues.append(f"{ENTRY_POINT} does not exist.")
    for entry in files.list_files(workspace)["files"]:
        if entry["path"].endswith(".py") and not entry["read_only"]:
            content = workspace.resolve(entry["path"]).read_text(encoding="utf-8")
            problem = files.syntax_error(entry["path"], content)
            if problem:
                issues.append(f"{entry['path']} has a syntax error ({problem}).")
    return issues


def _transcript_chars(content: Message) -> int:
    """Count bounded history without decoding or changing opaque adapter state."""
    size = len(content.text or "")
    size += len(json.dumps([call.__dict__ for call in content.tool_calls], ensure_ascii=False))
    size += len(json.dumps(content.result, ensure_ascii=False))
    for state in content.provider_state.values():
        if hasattr(state, "model_dump_json"):
            try:
                size += len(state.model_dump_json(exclude_none=True))
                continue
            except (TypeError, ValueError, UnicodeError):
                pass
        size += len(repr(state))
    return size


def _compact_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


@model_run
def run_build(
    workspace: Workspace,
    feedback: str | None = None,
    *,
    model: str | None = None,
    client=None,
    max_turns: int = DEFAULT_MAX_TURNS,
    on_event: Callable[[str], None] | None = None,
    on_progress: ProgressCallback | None = None,
) -> BuildResult:
    """Have the agent write the solver files for the workspace's approved plan.

    feedback: issues from a later check (plan comparison or Test) when Build is being redone.
              Existing files are kept so the agent can fix them.
    on_event: called with a one-line description of each tool call, for progress output.
    """
    with progress_step(on_progress, "Load the problem and approved plan"):
        for required, stage in ((PROBLEM_JSON, "Init"), (PLAN_JSON, "Plan")):
            if not workspace.resolve(required).is_file():
                raise BuildError(f"Run {stage} first to create {required} for workspace {workspace.id}.")
        problem = load_problem_statement(workspace)
        planning = load_plan(workspace)
    with progress_step(on_progress, "Copy the Quantum Rings runtime") as step:
        shutil.copyfile(TEMPLATES / RUNTIME_HELPER, workspace.resolve(RUNTIME_HELPER))
        step.completed(RUNTIME_HELPER)

    system = load_prompt("build.system", contract=build_contract(planning, workspace))
    problem_json = problem.model_dump_json(exclude_none=True)
    plan_json = planning.plan.implementation_json()
    repair = "\n\n" + load_prompt("build.retry", feedback=feedback) if feedback else ""

    def workspace_message() -> str:
        return load_prompt(
            "build.user", problem=problem_json, plan=plan_json,
            files=_compact_json(files.list_files(workspace)["files"]),
        ) + repair

    message = workspace_message()

    available = TOOLS if not feedback else {name: tool for name, tool in TOOLS.items() if name != "update_file"}
    tools = [
        Tool(name=name, description=description, parameters=schema)
        for name, (description, schema, _) in available.items()
    ]
    model = model_name(DEFAULT_MODEL, model, stage="build")

    history = deque()
    history_chars = 0
    checkpoint_notes = deque(maxlen=MAX_CHECKPOINT_NOTES)
    checkpointed_turns = 0
    nudge = False
    summary = None
    for turn in range(1, max_turns + 1):
        start = message
        if checkpointed_turns:
            start = workspace_message() + "\n\n" + load_prompt(
                "build.checkpoint",
                turns=checkpointed_turns,
                notes=_compact_json(list(checkpoint_notes)),
            )
        contents = [Message(role="user", text=start)]
        for assistant, reply, _, _ in history:
            contents.extend((assistant, *reply))
        if nudge:
            contents.append(Message(role="user", text=load_prompt("build.nudge")))
        with progress_step(on_progress, f"Build agent turn {turn}") as step:
            response = generate(client, model, system, contents, stage="build", tools=tools,
                                tool_choice="required", on_progress=on_progress)
            calls = response.tool_calls
            step.completed(f"Requested file operations: {len(calls)}.")
        if not calls:
            nudge = True
            report(on_progress, "Ask the build agent to continue")
            continue
        nudge = False

        # Run writes before deciding whether to accept finish in the same batch.
        # Return tool replies in the original order with their matching IDs.
        records = []
        failed_writes = []
        for call in calls:
            args = dict(call.arguments)
            if call.name == "finish":
                result = None
            elif call.name in TOOLS and call.name not in available:
                result = {"error": "Use patch_file to edit the relevant lines of an existing file."}
            else:
                with progress_step(on_progress, TOOL_LABELS.get(call.name, "Handle a solver file request")) as step:
                    result = _call_tool(workspace, call.name, args)
                    if "error" in result:
                        step.failed("The workspace rejected the requested operation.")
                    elif result.get("syntax_error"):
                        step.failed(f"Syntax error in {result['path']} at {result['syntax_error']}.")
                    else:
                        step.completed(result.get("path"))
            records.append((call, args, result))
            if call.name in _WRITE_TOOLS and "error" in result:
                failed_writes.append(f"{call.name}: {result['error']}")
        replies = []
        notes = []
        for call, args, result in records:
            if result is None:
                issues = failed_writes + unfinished_issues(workspace)
                if issues:
                    result = {"error": load_prompt("build.unfinished", issues="\n".join(f"- {i}" for i in issues))}
                else:
                    summary = str(args.get("summary", "")).strip() or "(no summary given)"
                    result = {"accepted": True}
            status = f"refused: {result['error']}" if "error" in result else "ok"
            if result.get("syntax_error"):
                status = f"syntax error, {result['syntax_error']}"
            path = result.get("path") or args.get("path")
            action = f"{call.name} {path}" if path else str(call.name)
            if on_event:
                on_event(f"turn {turn}: {action} ({status})")
            if call.name == "finish":
                report(on_progress, "Check that the solver files are ready", status="failed" if "error" in result else "completed")
            if status != "ok" or call.name in _WRITE_TOOLS:
                notes.append(f"{action[:160]} ({status[:220]})")
            replies.append(
                Message(role="tool", name=call.name, result=result, tool_call_id=call.id)
            )

        if summary is not None:
            written = [f["path"] for f in files.list_files(workspace)["files"] if not f["read_only"]]
            result = BuildResult(model=model, turns=turn, summary=summary, files=written, feedback=feedback)
            with progress_step(on_progress, "Save the solver build record"):
                workspace.resolve(BUILD_JSON).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            return result

        assistant = response.message or Message(role="assistant", text=response.text, tool_calls=calls)
        reply = replies
        exchange_chars = _transcript_chars(assistant) + sum(map(_transcript_chars, reply))
        # Keep the newest exchange even if it alone exceeds the target. The
        # model needs its newest results. Drop only complete older exchanges.
        while history and (len(history) >= MAX_HISTORY_TURNS or history_chars + exchange_chars > MAX_HISTORY_CHARS):
            _, _, evicted_chars, evicted_notes = history.popleft()
            history_chars -= evicted_chars
            checkpointed_turns += 1
            checkpoint_notes.extend(evicted_notes)
            report(on_progress, "Compact build conversation", detail=f"Checkpointed {checkpointed_turns} earlier turns.")
        history.append((assistant, reply, exchange_chars, notes[-MAX_CHECKPOINT_NOTES:]))
        history_chars += exchange_chars

    report(on_progress, "Build turn limit reached", status="failed", detail=f"Used all {max_turns} build turns.")
    raise BuildError(f"The agent did not finish within {max_turns} turns.")


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Have the agent write the solver files for a planned workspace.")
    parser.add_argument("workspace_id")
    parser.add_argument("--feedback", help="Issues to fix when Build is being redone")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        result = run_build(workspace, args.feedback, max_turns=args.max_turns, on_event=print)
    except (OSError, BuildError, ModelError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Build failed: {error}")

    print(f"\nFinished in {result.turns} turn(s). Files: {', '.join(result.files)}")
    print(f"Summary: {result.summary}")
    print(f"Wrote: {workspace.resolve(BUILD_JSON)}")


if __name__ == "__main__":
    main()
