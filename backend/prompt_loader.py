"""Code that reads prompts. It holds no prompt text itself.

All prompt text lives in backend/prompts.md. This module finds a prompt
there by id and fills in its {placeholders}. The file is read on every call,
so edits to prompts.md apply on the next request without restarting
anything. See the intro of prompts.md for the format.

    system = load_prompt("problem_statement.system", max_qubits=200)
"""

import re
from pathlib import Path

PROMPTS_FILE = Path(__file__).resolve().parent / "prompts.md"

_HEADING = re.compile(r"^## +(\S.*?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PromptError(Exception):
    """Raised for a missing prompt id or a placeholder left unfilled."""


def read_prompts(path: Path = PROMPTS_FILE) -> dict[str, str]:
    """Parse prompts.md into {prompt id: raw text}. Headings inside code fences are ignored."""
    prompts: dict[str, list[str]] = {}
    current: list[str] | None = None  # None until the first "##" heading (the intro)
    in_fence = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            prompt_id = heading.group(1)
            if prompt_id in prompts:
                raise PromptError(f"Duplicate prompt id in {path.name}: {prompt_id!r}")
            current = prompts[prompt_id] = []
        elif current is not None:
            current.append(line)

    return {prompt_id: "\n".join(lines).strip("\n") for prompt_id, lines in prompts.items()}


def load_prompt(prompt_id: str, path: Path = PROMPTS_FILE, **values) -> str:
    """Return one prompt with its {placeholders} filled in from values.

    Only {name} placeholders that match a passed value are replaced, so other
    braces in a prompt (e.g. a JSON example) are left alone. A placeholder that
    looks like a value name but was not passed raises PromptError.
    """
    prompts = read_prompts(path)
    if prompt_id not in prompts:
        raise PromptError(f"No prompt {prompt_id!r} in {path.name}. Available: {sorted(prompts)}")

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise PromptError(f"Prompt {prompt_id!r} needs a value for {{{name}}}")
        return str(values[name])

    return _PLACEHOLDER.sub(fill, prompts[prompt_id])
