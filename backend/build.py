"""Build step 1 of the agent loop: the agent writes the solver's Python files.

Gemini reads the problem statement and the approved plan, then creates, reads,
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
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from backend import files
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
REQUEST_ATTEMPTS = 4
RETRYABLE_CODES = (408, 429, 500, 502, 503, 504)

_PATH = {"type": "string", "description": "File path relative to the workspace, e.g. 'main.py'."}
_CONTENT = {"type": "string", "description": "The complete text of the file."}


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties)}


# name -> (description for Gemini, parameter schema, function to call or None for finish)
TOOLS: dict[str, tuple[str, dict, Callable | None]] = {
    "list_files": ("List every file in the workspace with its size.", _schema({}), files.list_files),
    "read_file": ("Read a file's content.", _schema({"path": _PATH}), files.read_file),
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
    "delete_file": ("Delete a file you created.", _schema({"path": _PATH}), files.delete_file),
    "finish": (
        "Declare the implementation complete.",
        _schema({"summary": {"type": "string", "description": "What each file does and anything left out."}}),
        None,
    ),
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


def gemini_client():
    from google import genai

    try:
        return genai.Client()
    except ValueError as error:
        raise BuildError("Configure GEMINI_API_KEY before running Build.") from error


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


def generate(client, model: str, system: str, contents, **config_fields):
    """One Gemini request, retried on temporary service errors."""
    from google.genai import errors, types

    config = types.GenerateContentConfig(system_instruction=system, **config_fields)
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except errors.APIError as error:
            if error.code not in RETRYABLE_CODES or attempt == REQUEST_ATTEMPTS:
                raise BuildError(f"Gemini request failed (HTTP {error.code}): {error.message}") from error
            time.sleep(2**attempt)


def run_build(
    workspace: Workspace,
    feedback: str | None = None,
    *,
    model: str | None = None,
    client=None,
    max_turns: int = DEFAULT_MAX_TURNS,
    on_event: Callable[[str], None] | None = None,
) -> BuildResult:
    """Have the agent write the solver files for the workspace's approved plan.

    feedback: issues from a later check (plan comparison or Test) when Build is being redone.
              Existing files are kept so the agent can fix them.
    on_event: called with a one-line description of each tool call, for progress output.
    """
    from google.genai import types

    for required, step in ((PROBLEM_JSON, "Init"), (PLAN_JSON, "Plan")):
        if not workspace.resolve(required).is_file():
            raise BuildError(f"Run {step} first to create {required} for workspace {workspace.id}.")
    problem = load_problem_statement(workspace)
    planning = load_plan(workspace)

    shutil.copyfile(TEMPLATES / RUNTIME_HELPER, workspace.resolve(RUNTIME_HELPER))

    # Prompts are read from prompts.md now, before Gemini is called.
    system = load_prompt("build.system", contract=build_contract(planning, workspace))
    message = load_prompt(
        "build.user",
        problem=problem.model_dump_json(indent=2),
        plan=planning.plan.model_dump_json(indent=2),
        files=json.dumps(files.list_files(workspace)["files"], indent=2),
    )
    if feedback:
        message += "\n\n" + load_prompt("build.retry", feedback=feedback)

    tools = [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(name=name, description=description, parameters_json_schema=schema)
                for name, (description, schema, _) in TOOLS.items()
            ]
        )
    ]
    tool_config = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))
    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    client = client or gemini_client()

    contents = [types.Content(role="user", parts=[types.Part(text=message)])]
    summary = None
    for turn in range(1, max_turns + 1):
        response = generate(client, model, system, contents, tools=tools, tool_config=tool_config)
        calls = response.function_calls or []
        if not calls:
            contents.append(types.Content(role="user", parts=[types.Part(text=load_prompt("build.nudge"))]))
            continue
        contents.append(response.candidates[0].content)  # keep the model's turn exactly as sent

        replies = []
        for call in calls:
            args = dict(call.args or {})
            if call.name == "finish":
                issues = unfinished_issues(workspace)
                if issues:
                    result = {"error": load_prompt("build.unfinished", issues="\n".join(f"- {i}" for i in issues))}
                else:
                    summary = str(args.get("summary", "")).strip() or "(no summary given)"
                    result = {"accepted": True}
            else:
                result = _call_tool(workspace, call.name, args)
            if on_event:
                status = f"refused: {result['error']}" if "error" in result else "ok"
                if result.get("syntax_error"):
                    status = f"syntax error, {result['syntax_error']}"
                on_event(f"turn {turn}: {call.name} {args.get('path', '')} ({status})".replace("  ", " "))
            replies.append(
                types.Part(function_response=types.FunctionResponse(name=call.name, response=result, id=call.id))
            )
        contents.append(types.Content(role="user", parts=replies))

        if summary is not None:
            written = [f["path"] for f in files.list_files(workspace)["files"] if not f["read_only"]]
            result = BuildResult(model=model, turns=turn, summary=summary, files=written, feedback=feedback)
            workspace.resolve(BUILD_JSON).write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
            return result

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
    except (OSError, BuildError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Build failed: {error}")

    print(f"\nFinished in {result.turns} turn(s). Files: {', '.join(result.files)}")
    print(f"Summary: {result.summary}")
    print(f"Wrote: {workspace.resolve(BUILD_JSON)}")


if __name__ == "__main__":
    main()
