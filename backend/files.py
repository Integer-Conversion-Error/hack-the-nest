"""Build step 1: create, read, update and delete files in a workspace.

These are the operations the Build agent calls to write the solver. Every
path is relative to the workspace and cannot leave it. Each function returns
a JSON-friendly dict (sent back to the agent as the tool result) and raises
FileToolError with a message the agent can act on.

    create_file(ws, "main.py", "print('hi')\n")
    read_file(ws, "main.py")
    patch_file(ws, "main.py", "'hi'", "'hello'")
    update_file(ws, "main.py", "print('hello')\n")
    delete_file(ws, "main.py")
    list_files(ws)
"""

from pathlib import Path

from backend.workspace import Workspace, WorkspaceError, write_atomic

# Files written by the pipeline itself or by running the solver. The agent can
# read them but not change them, and they don't count as files it wrote.
READ_ONLY_FILES = frozenset(
    {
        "prompt.md", "meta.json", "problem.json", "PROBLEM.md", "plan.json", "PLAN.md",
        "qr_runtime.py", "build.json", "build_review.json", "results.json", "circuit.png", "test.json", "final.json", "handoff.json", "model_usage.json", "model_usage.jsonl",
    }
)
WRITABLE_SUFFIXES = frozenset({".py", ".json", ".md", ".txt"})
IGNORED_DIRS = frozenset({"__pycache__"})

MAX_FILE_BYTES = 100_000
MAX_FILES = 40
MAX_READ_LINES = 100
MAX_READ_CHARS = 8_000


class FileToolError(Exception):
    """A file operation was refused. The message says what to do instead."""


def _locate(workspace: Workspace, path: str) -> tuple[Path, str]:
    """Resolve an agent-supplied path. Returns (absolute path, normalized relative path)."""
    if not isinstance(path, str) or not path.strip():
        raise FileToolError("Give a file path relative to the workspace, such as 'main.py'.")
    try:
        target = workspace.resolve(path.strip())
    except WorkspaceError as error:
        raise FileToolError(f"{error}. Use a path relative to the workspace.") from error
    relative = target.relative_to(workspace.path)
    if not relative.parts:
        raise FileToolError("The path points at the workspace folder, not a file.")
    if any(part.startswith(".") or part in IGNORED_DIRS for part in relative.parts):
        raise FileToolError(f"Hidden and cache paths are not allowed: {path!r}")
    if target.is_dir():
        raise FileToolError(f"{relative.as_posix()!r} is a folder, not a file.")
    return target, relative.as_posix()


def _check_writable(relative: str) -> None:
    if relative in READ_ONLY_FILES:
        raise FileToolError(f"{relative!r} is read-only. It belongs to the pipeline.")
    suffix = Path(relative).suffix.lower()
    if suffix not in WRITABLE_SUFFIXES:
        raise FileToolError(f"Only {', '.join(sorted(WRITABLE_SUFFIXES))} files can be written, not {suffix or 'no extension'!r}.")


def _check_content(content: str) -> bytes:
    if not isinstance(content, str):
        raise FileToolError("File content must be text.")
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise FileToolError("File content must be valid UTF-8 text.") from error
    if len(data) > MAX_FILE_BYTES:
        raise FileToolError(f"The file is {len(data)} bytes; the limit is {MAX_FILE_BYTES}. Split it into modules.")
    return data


def syntax_error(relative: str, content: str) -> str | None:
    """Return a description of a Python syntax error, or None. Does not run the code."""
    if not relative.endswith(".py"):
        return None
    try:
        compile(content, relative, "exec")
    except SyntaxError as error:
        return f"line {error.lineno}: {error.msg}"
    except ValueError as error:  # e.g. null bytes in the source
        return str(error)
    return None


def _written(relative: str, data: bytes, content: str) -> dict:
    return {"path": relative, "bytes": len(data), "syntax_error": syntax_error(relative, content)}


def list_files(workspace: Workspace) -> dict:
    """List every file in the workspace with its size and whether the agent may change it."""
    files = []
    for path in sorted(workspace.path.rglob("*")):
        relative = path.relative_to(workspace.path)
        if path.is_file() and not any(part.startswith(".") or part in IGNORED_DIRS for part in relative.parts):
            name = relative.as_posix()
            files.append({"path": name, "bytes": path.stat().st_size, "read_only": name in READ_ONLY_FILES})
    return {"files": files}


def read_file(
    workspace: Workspace,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    start_column: int = 1,
) -> dict:
    """Return a bounded 1-based line range, with a cursor when more text is available."""
    target, relative = _locate(workspace, path)
    if not target.is_file():
        raise FileToolError(f"No file {relative!r}. Call list_files to see what exists.")
    if target.stat().st_size > 4 * MAX_FILE_BYTES:
        raise FileToolError(f"{relative!r} exceeds the file tool's read size limit.")
    if type(start_line) is not int or start_line < 1 or type(start_column) is not int or start_column < 1:
        raise FileToolError("start_line and start_column must be positive integers.")
    if end_line is not None and (type(end_line) is not int or end_line < start_line):
        raise FileToolError("end_line must be an integer greater than or equal to start_line.")
    try:
        with target.open(encoding="utf-8", newline="") as stream:
            lines = stream.readlines()
    except UnicodeDecodeError as error:
        raise FileToolError(f"{relative!r} is not a text file.") from error

    if start_line > max(len(lines), 1):
        raise FileToolError(f"{relative!r} has only {len(lines)} line(s).")
    if start_column > 1 and (not lines or start_column > len(lines[start_line - 1])):
        raise FileToolError(f"Line {start_line} of {relative!r} is shorter than start_column.")

    last_line = min(end_line or len(lines), start_line + MAX_READ_LINES - 1, len(lines))
    result = {"path": relative, "start_line": start_line, "end_line": start_line - 1, "total_lines": len(lines)}
    pieces = []
    remaining = MAX_READ_CHARS
    for number in range(start_line, last_line + 1):
        offset = start_column - 1 if number == start_line else 0
        tail = lines[number - 1][offset:]
        if remaining:
            pieces.append(tail[:remaining])
            result["end_line"] = number
        if len(tail) > remaining:
            result["next_line"] = number
            if offset + remaining:
                result["next_column"] = offset + remaining + 1
            break
        remaining -= len(tail)
    else:
        if last_line < len(lines):
            result["next_line"] = last_line + 1
    result["content"] = "".join(pieces)
    return result


def create_file(workspace: Workspace, path: str, content: str) -> dict:
    target, relative = _locate(workspace, path)
    _check_writable(relative)
    if target.exists():
        raise FileToolError(f"{relative!r} already exists. Use patch_file to edit it.")
    writable = [f for f in list_files(workspace)["files"] if not f["read_only"]]
    if len(writable) >= MAX_FILES:
        raise FileToolError(f"The workspace already has {MAX_FILES} files. Delete or merge some first.")
    data = _check_content(content)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _written(relative, data, content)


def update_file(workspace: Workspace, path: str, content: str) -> dict:
    """Replace the whole content of an existing file."""
    target, relative = _locate(workspace, path)
    _check_writable(relative)
    if not target.is_file():
        raise FileToolError(f"No file {relative!r}. Use create_file to make it.")
    data = _check_content(content)
    target.write_bytes(data)
    return _written(relative, data, content)


def patch_file(workspace: Workspace, path: str, old: str, new: str) -> dict:
    """Replace one exact, unique text match without returning the file contents."""
    target, relative = _locate(workspace, path)
    _check_writable(relative)
    if not target.is_file():
        raise FileToolError(f"No file {relative!r}. Use create_file to make it.")
    if not isinstance(old, str) or not isinstance(new, str):
        raise FileToolError("Give the exact old text and the replacement text (empty to delete the match).")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise FileToolError(f"{relative!r} exceeds the file tool's write size limit.")

    try:
        original = target.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise FileToolError(f"{relative!r} is not a text file.") from error
    if not old.strip() and original != old:
        raise FileToolError("Give an exact match that includes nonblank text, or the full contents of a blank file.")
    start = original.find(old)
    if start == -1:
        raise FileToolError(f"Text to replace was not found in {relative!r}. Read the relevant lines again.")
    if original.find(old, start + 1) != -1:
        raise FileToolError(f"Text to replace occurs more than once in {relative!r}. Include more surrounding text.")

    content = original[:start] + new + original[start + len(old):]
    data = _check_content(content)
    write_atomic(target, data)
    return _written(relative, data, content)


def delete_file(workspace: Workspace, path: str) -> dict:
    target, relative = _locate(workspace, path)
    _check_writable(relative)
    if not target.is_file():
        raise FileToolError(f"No file {relative!r}. Call list_files to see what exists.")
    target.unlink()
    return {"path": relative, "deleted": True}
