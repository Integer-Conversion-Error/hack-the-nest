"""Browse saved pipeline workspaces without running a planner or solver."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fleet_handoff import is_text, normalize_handoff
from pipeline_live import load_scenario, project_result

from backend.workspace import META_FILE, PROMPT_FILE, Workspace, WorkspaceError, open_workspace, workspace_root

HANDOFF_FILE = "handoff.json"
ARTIFACTS = {
    "scenario": ("Scenario inputs", "scenario.json"),
    "problem": ("Problem statement", "PROBLEM.md", "problem.json"),
    "plan": ("Plan", "PLAN.md", "plan.json"),
    "build": ("Build report", "build.json"),
    "solver": ("Solver entry point", "main.py"),
    "test": ("Test results", "test.json"),
    "verification": ("Final review", "final.json"),
    "handoff": ("Handoff", HANDOFF_FILE),
}


class SavedRunNotFound(Exception):
    """The selected workspace or artifact does not exist."""


class SavedRunUnavailable(Exception):
    """The file exists but cannot be read as a saved run."""


class SavedResultUnavailable(SavedRunUnavailable):
    """The workspace has no compatible verified replay."""


def _open(run_id: str, root: Path | None = None) -> Workspace:
    try:
        return open_workspace(run_id, root=root)
    except WorkspaceError:
        raise SavedRunNotFound("Saved run not found.") from None
    except (OSError, RuntimeError):
        raise SavedRunUnavailable("Could not read the saved run.") from None


def _file(workspace: Workspace, filename: str) -> Path | None:
    """Use the actual file at the allowlisted path, without following aliases."""
    path = workspace.path / filename
    try:
        if path.resolve() == path and path.is_file():
            return path
    except (OSError, RuntimeError):
        pass
    return None


def _read_json(workspace: Workspace, filename: str) -> dict | None:
    path = _file(workspace, filename)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _created_at(workspace: Workspace, meta: dict) -> str:
    value = meta.get("created_at")
    try:
        created = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        created = None
    if created is None:
        created = datetime.fromtimestamp(workspace.path.stat().st_mtime, timezone.utc)
    elif created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(timezone.utc).isoformat(timespec="seconds")


def _title(workspace: Workspace, prompt: str, handoff: dict) -> str:
    problem = handoff.get("problem")
    title = problem.get("title") if isinstance(problem, dict) else None
    if not is_text(title):
        title = (_read_json(workspace, "problem.json") or {}).get("title")
    if is_text(title):
        return title.strip()
    lines = (line.lstrip("# ").strip() for line in prompt.splitlines())
    return next((line[:120] for line in lines if line), workspace.id)


def _replay(workspace: Workspace, prompt: str, handoff: dict) -> dict | None:
    identity = {"run_id": str(uuid4()), "plan_id": workspace.id, "attempt_id": str(uuid4())}
    try:
        projected = project_result(handoff, handoff["results"], prompt, identity, workspace=workspace, saved_image=True)
        projected["result"]["provenance"] = "recorded"
        view = normalize_handoff(projected, "live", prompt, expected_values=projected["result"]["effective_values"])
        if projected.get("scenario_context") is not None:
            view["scenario_context"] = projected["scenario_context"]
        view["source"] = {"workspace_id": workspace.id, "file": HANDOFF_FILE}
        json.dumps(view, allow_nan=False)
    except (KeyError, TypeError, ValueError, RecursionError):
        return None
    return view


def _scenario(workspace: Workspace) -> dict | None:
    try:
        return load_scenario(workspace)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def _inspect(workspace: Workspace) -> tuple[dict, str, dict | None]:
    meta = _read_json(workspace, META_FILE)
    prompt_file = _file(workspace, PROMPT_FILE)
    if meta is None or meta.get("id") != workspace.id or prompt_file is None:
        raise SavedRunUnavailable("The saved run has no readable prompt or workspace metadata.")
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
        created_at = _created_at(workspace, meta)
    except (OSError, ValueError, OverflowError):
        raise SavedRunUnavailable("Could not read the saved run.") from None
    if not is_text(prompt):
        raise SavedRunUnavailable("The saved run has no readable prompt.")

    handoff = _read_json(workspace, HANDOFF_FILE) or {}
    approved = handoff.get("decision") == "GO" and handoff.get("workspace_id") == workspace.id
    replay = _replay(workspace, prompt, handoff) if approved else None
    summary = {
        "id": workspace.id,
        "title": _title(workspace, prompt, handoff if approved else {}),
        "created_at": created_at,
        "status": "verified" if replay is not None else "saved" if approved else "incomplete",
        "can_open": replay is not None,
        "can_edit": _scenario(workspace) is not None,
    }
    return summary, prompt, replay


def _artifact(workspace: Workspace, key: str) -> Path | None:
    definition = ARTIFACTS.get(key)
    if definition is None:
        return None
    return next((path for filename in definition[1:] if (path := _file(workspace, filename)) is not None), None)


def list_saved_runs() -> list[dict]:
    try:
        root = workspace_root()
        entries = list(root.iterdir())
    except FileNotFoundError:
        return []
    except (OSError, RuntimeError):
        raise SavedRunUnavailable("Could not read saved runs.") from None
    summaries = []
    for entry in entries:
        try:
            summary, _, _ = _inspect(_open(entry.name, root))
        except (SavedRunNotFound, SavedRunUnavailable):
            continue
        summaries.append(summary)
    return sorted(summaries, key=lambda run: (run["created_at"], run["id"]), reverse=True)


def saved_run(run_id: str) -> dict:
    workspace = _open(run_id)
    summary, prompt, _ = _inspect(workspace)
    artifacts = [
        {"key": key, "label": definition[0], "filename": path.name}
        for key, definition in ARTIFACTS.items() if (path := _artifact(workspace, key)) is not None
    ]
    scenario = _scenario(workspace)
    return {**summary, "prompt": prompt, "artifacts": artifacts,
            **({"scenario": scenario} if scenario is not None else {})}


def open_saved_run(run_id: str) -> dict:
    _, _, replay = _inspect(_open(run_id))
    if replay is None:
        raise SavedResultUnavailable("This saved run has no verified fleet result to open.")
    return replay


def saved_artifact(run_id: str, key: str) -> tuple[str, bytes]:
    workspace = _open(run_id)
    path = _artifact(workspace, key)
    if path is None:
        raise SavedRunNotFound("Saved artifact not found.")
    try:
        return path.name, path.read_bytes()
    except FileNotFoundError:
        raise SavedRunNotFound("Saved artifact not found.") from None
    except OSError:
        raise SavedRunUnavailable("Could not read the saved artifact.") from None
