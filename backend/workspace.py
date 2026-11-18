"""Init step of the agent loop: one isolated working directory per user prompt.

Each workspace lives in workspaces/<id>/ and starts with two files:
  prompt.md  - the user's natural-language prompt, verbatim
  meta.json  - id, creation time, and the hard limits the agent must respect

Usage from the agent's tool dispatcher:
    ws = init_workspace(prompt_text, name="ev-fleet-dispatch")
    ws.resolve("main.py")   # safe absolute path inside the workspace

Usage from a terminal:
    python -m backend.workspace example-user-prompts/ev-fleet-dispatch-optimization.md
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from tempfile import TemporaryDirectory

WORKSPACES_ROOT = Path(__file__).resolve().parent.parent / "workspaces"

PROMPT_FILE = "prompt.md"
META_FILE = "meta.json"

# Hard limits checked in the Plan phase and enforced when main.py is run.
DEFAULT_MAX_QUBITS = 200          # free Quantum Rings hackathon license
DEFAULT_RUN_TIMEOUT_SECONDS = 300


class WorkspaceError(Exception):
    """Raised for bad workspace ids, missing workspaces, or paths that escape one."""


@dataclass(frozen=True)
class Workspace:
    id: str
    path: Path

    @property
    def prompt(self) -> str:
        return self.resolve(PROMPT_FILE).read_text(encoding="utf-8")

    @property
    def meta(self) -> dict:
        return json.loads(self.resolve(META_FILE).read_text(encoding="utf-8"))

    def resolve(self, relative_path: str) -> Path:
        """Turn an agent-supplied path into an absolute one, refusing anything outside the workspace."""
        target = (self.path / relative_path).resolve()
        if not target.is_relative_to(self.path):
            raise WorkspaceError(f"Path escapes the workspace: {relative_path!r}")
        return target


def fingerprint(*values) -> str:
    """Identify the exact JSON inputs to an approval or saved run."""
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """Keep the previous artifact intact if the replacement cannot be published."""
    with TemporaryDirectory(dir=path.parent, prefix=".publish-") as staging:
        replacement = Path(staging) / path.name
        replacement.write_bytes(data)
        replacement.replace(path)


def _slugify(text: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length].strip("-")


def workspace_root(root: Path | None = None) -> Path:
    """Use the workspace directory selected by the caller or local configuration."""
    if root is None:
        configured = os.getenv("QUANTUM_WORKSPACES_ROOT", "").strip()
        root = Path(configured) if configured else WORKSPACES_ROOT
    return Path(root).expanduser().resolve()


def init_workspace(
    prompt: str,
    name: str = "",
    root: Path | None = None,
    max_qubits: int = DEFAULT_MAX_QUBITS,
    run_timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    *,
    scene_type: Literal["ev_fleet"] | None = None,
) -> Workspace:
    """Create a fresh workspace holding the user's prompt and return it."""
    if not prompt.strip():
        raise WorkspaceError("Prompt is empty.")

    now = datetime.now(timezone.utc)
    parts = [now.strftime("%Y%m%d-%H%M%S"), _slugify(name), secrets.token_hex(2)]
    workspace_id = "-".join(part for part in parts if part)

    path = workspace_root(root) / workspace_id
    path.mkdir(parents=True, exist_ok=False)  # never reuse a directory

    (path / PROMPT_FILE).write_text(prompt, encoding="utf-8")
    meta = {
        "id": workspace_id,
        "created_at": now.isoformat(timespec="seconds"),
        "limits": {
            "max_qubits": max_qubits,
            "run_timeout_seconds": run_timeout_seconds,
        },
    }
    if scene_type is not None:
        meta["scene_type"] = scene_type
    (path / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return Workspace(id=workspace_id, path=path)


def open_workspace(workspace_id: str, root: Path | None = None) -> Workspace:
    """Look up an existing workspace by id (for every tool call after init)."""
    if not re.fullmatch(r"[a-z0-9-]+", workspace_id):
        raise WorkspaceError(f"Invalid workspace id: {workspace_id!r}")
    path = workspace_root(root) / workspace_id
    workspace = Workspace(id=workspace_id, path=path)
    if path.resolve() != path or not workspace.resolve(META_FILE).is_file():
        raise WorkspaceError(f"No such workspace: {workspace_id!r}")
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a workspace from a prompt file.")
    parser.add_argument("prompt_file", type=Path, help="Markdown/text file with the user's prompt")
    args = parser.parse_args()

    try:
        prompt = args.prompt_file.read_text(encoding="utf-8")
        workspace = init_workspace(prompt, name=args.prompt_file.stem)
    except (OSError, WorkspaceError) as error:
        sys.exit(f"Could not create workspace: {error}")

    print(f"Workspace id: {workspace.id}")
    print(f"Path:         {workspace.path}")
    print(f"Limits:       {workspace.meta['limits']}")


if __name__ == "__main__":
    main()
