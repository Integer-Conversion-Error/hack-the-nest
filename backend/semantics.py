"""Test step 3: evaluate semantics.

Compares the implementation and its run results with the problem statement.
The plan is assumed to match the problem statement already. Gemini does this
in a fresh conversation, reading the problem statement, the source files and
the results of each run.

    review = evaluate_semantics(workspace, runs)
    review.matches, review.feedback()
"""

import json
import os
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.build import DEFAULT_MODEL, BuildError, gemini_client, generate, source_files
from backend.problem_statement import load_problem_statement
from backend.prompt_loader import load_prompt
from backend.runner import RunOutcome
from backend.workspace import Workspace

MAX_LIST_ITEMS = 12
MAX_TEXT_CHARS = 600


class SemanticIssue(BaseModel):
    file: str = Field(description="The file the issue is in, or 'results' if it shows only in the run results.")
    requirement: str = Field(description="The part of the problem statement that is not met.")
    problem: str = Field(description="What the code or the results show instead.")
    fix: str = Field(description="What to change.")


class _ModelReview(BaseModel):
    """What Gemini returns."""

    verdict: Literal["matches", "falls_short"]
    summary: str = Field(description="Two or three sentences on whether the solver solves the stated problem.")
    checks_done: list[str] = Field(description="Figures you recomputed by hand and what you compared them with.")
    issues: list[SemanticIssue]


class SemanticReview(BaseModel):
    """The saved result of the semantic check."""

    verdict: Literal["matches", "falls_short"]
    summary: str
    checks_done: list[str]
    issues: list[SemanticIssue]
    model: str

    @property
    def matches(self) -> bool:
        return self.verdict == "matches"

    def feedback(self) -> str:
        """The issues as text for the Build step."""
        return "The solver ran, but it does not solve the stated problem correctly:\n" + "\n".join(
            f"- {issue.file}: {issue.problem} Requirement: {issue.requirement} Fix: {issue.fix}"
            for issue in self.issues
        )


def shorten(value, depth: int = 0):
    """Trim long lists, dicts and strings so results fit in a prompt."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT_CHARS else value[:MAX_TEXT_CHARS] + f"... ({len(value)} characters)"
    if isinstance(value, list):
        kept = [shorten(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            kept.append(f"... {len(value) - MAX_LIST_ITEMS} more items")
        return kept
    if isinstance(value, dict):
        items = list(value.items())
        limit = MAX_LIST_ITEMS * 3 if depth < 2 else MAX_LIST_ITEMS
        kept = {str(key): shorten(item, depth + 1) for key, item in items[:limit]}
        if len(items) > limit:
            kept["..."] = f"{len(items) - limit} more entries"
        return kept
    return value


def describe_runs(runs: list[RunOutcome]) -> str:
    parts = []
    for run in runs:
        parts.append(
            f"=== {run.command} (finished in {run.seconds} s) ===\n"
            f"Printed output:\n{run.stdout_tail or '(nothing)'}\n\n"
            f"results.json:\n{json.dumps(shorten(run.results), indent=2, ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def evaluate_semantics(
    workspace: Workspace, runs: list[RunOutcome], *, model: str | None = None, client=None
) -> SemanticReview:
    """Decide whether the implementation and its results solve the stated problem."""
    problem = load_problem_statement(workspace)
    listing = "\n\n".join(
        f"=== {path} ===\n{workspace.resolve(path).read_text(encoding='utf-8')}" for path in source_files(workspace)
    )
    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    response = generate(
        client or gemini_client(),
        model,
        # Prompts are read from prompts.md now, before Gemini is called.
        load_prompt("test_semantics.system"),
        load_prompt(
            "test_semantics.user",
            problem=problem.model_dump_json(indent=2),
            runs=describe_runs(runs),
            files=listing,
        ),
        response_mime_type="application/json",
        response_json_schema=_ModelReview.model_json_schema(),
    )
    try:
        judged = _ModelReview.model_validate_json(response.text or "")
    except ValidationError as error:
        raise BuildError("Gemini returned no usable semantic review.") from error

    # The verdict follows from the issues, whatever label Gemini chose.
    return SemanticReview(
        verdict="falls_short" if judged.issues else "matches",
        summary=judged.summary,
        checks_done=judged.checks_done,
        issues=judged.issues,
        model=model,
    )
