"""Test step 3: evaluate semantics.

Compares the implementation and its run results with the problem statement.
The plan is assumed to match the problem statement already. The model does this
in a fresh conversation, reading the problem statement, the source files and
the results of each run.

    review = evaluate_semantics(workspace, runs)
    review.matches, review.feedback()
"""

import ast
import json
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError

from backend.build import DEFAULT_MODEL, BuildError, source_files
from backend.files import FileToolError, read_file
from backend.model_provider import generate, model_name, model_provider, model_run
from backend.progress import ProgressCallback, progress_step, report
from backend.problem_statement import ProblemStatement, load_problem_statement
from backend.prompt_loader import load_prompt
from backend.runner import REQUIRED_KEYS, RunOutcome
from backend.workspace import Workspace, fingerprint

MAX_LIST_ITEMS = 12
MAX_TEXT_CHARS = 600
FULL_SOURCE_CHARS = 24_000
INITIAL_SOURCE_CHARS = 16_000
MAX_EXCERPT_CHARS = 3_000
MAX_OUTLINE_CHARS = 700
MAX_SOURCE_FOLLOWUPS = 2
MAX_RANGES_PER_FOLLOWUP = 4
_EVIDENCE_VERSION = 2


class SemanticIssue(BaseModel):
    file: str = Field(description="The file the issue is in, or 'results' if it shows only in the run results.")
    requirement: str = Field(description="The part of the problem statement that is not met.")
    problem: str = Field(description="What the code or the results show instead.")
    fix: str = Field(description="What to change.")


class SourceRange(BaseModel):
    path: str = Field(description="Exact relative path from the source inventory.")
    start_line: StrictInt = Field(ge=1)
    end_line: StrictInt = Field(ge=1)
    start_column: StrictInt = Field(default=1, ge=1, description="Resume within a long line only when given a cursor.")


class SourceCoverage(BaseModel):
    path: str = Field(description="Exact path of a partial file in the source inventory.")
    reason: str = Field(description="Which relevant code was read and why any remaining omissions do not affect the verdict.")


class _ModelReview(BaseModel):
    """The model's review response."""

    verdict: Literal["matches", "falls_short", "needs_source"]
    summary: str = Field(description="Two or three sentences on whether the solver solves the stated problem.")
    checks_done: list[str] = Field(description="Figures you recomputed by hand and what you compared them with.")
    issues: list[SemanticIssue]
    source_requests: list[SourceRange] = Field(default_factory=list, max_length=MAX_RANGES_PER_FOLLOWUP)
    source_coverage: list[SourceCoverage] = Field(default_factory=list)


class SemanticReview(BaseModel):
    """The saved result of the semantic check."""

    verdict: Literal["matches", "falls_short"]
    summary: str
    checks_done: list[str]
    issues: list[SemanticIssue]
    model: str
    source_coverage: list[SourceCoverage] = Field(default_factory=list)

    @property
    def matches(self) -> bool:
        return self.verdict == "matches"

    def feedback(self) -> str:
        """The issues as text for the Build step."""
        return "The solver ran, but it does not solve the stated problem correctly:\n" + "\n".join(
            f"- {issue.file}: {issue.problem} Requirement: {issue.requirement} Fix: {issue.fix}"
            for issue in self.issues
        )


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _review_settings(model: str) -> tuple[str, str, dict]:
    system = load_prompt("test_semantics.system")
    user = load_prompt("test_semantics.user", problem="{problem}", runs="{runs}", files="{files}", excerpts="{excerpts}")
    coverage = load_prompt("test_semantics.coverage_required", paths="{paths}")
    schema = _ModelReview.model_json_schema()
    signature = fingerprint(
        _EVIDENCE_VERSION, model_provider(), model, system, user, coverage, schema, MAX_LIST_ITEMS, MAX_TEXT_CHARS,
        FULL_SOURCE_CHARS, INITIAL_SOURCE_CHARS, MAX_EXCERPT_CHARS, MAX_OUTLINE_CHARS, MAX_SOURCE_FOLLOWUPS,
    )
    return signature, system, schema


def semantics_fingerprint(*, model: str | None = None) -> str:
    """Identify the semantic review prompts, schema, model and evidence format."""
    signature, _, _ = _review_settings(model_name(DEFAULT_MODEL, model, stage="semantic_review"))
    return signature


def shorten(value, depth: int = 0):
    """Trim long lists, dicts and strings so results fit in a prompt."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT_CHARS else value[:MAX_TEXT_CHARS] + f"... ({len(value)} characters)"
    if isinstance(value, list):
        if len(value) <= MAX_LIST_ITEMS:
            return [shorten(item, depth + 1) for item in value]
        half = MAX_LIST_ITEMS // 2
        sample = [*value[:half], f"... {len(value) - MAX_LIST_ITEMS} middle items omitted", *value[-half:]]
        return [shorten(item, depth + 1) for item in sample]
    if isinstance(value, dict):
        if depth == 0:
            ordered_keys = dict.fromkeys((*REQUIRED_KEYS, *value))
            items = [(key, value[key]) for key in ordered_keys if key in value]
        else:
            items = list(value.items())
        limit = MAX_LIST_ITEMS * 3 if depth < 2 else MAX_LIST_ITEMS
        kept = {str(key): shorten(item, depth + 1) for key, item in items[:limit]}
        if len(items) > limit:
            kept["..."] = f"{len(items) - limit} more entries"
        return kept
    return value


def describe_runs(runs: list[RunOutcome]) -> str:
    results = [run.results or {} for run in runs]
    shared = {}
    if len(results) > 1:
        for key, value in results[0].items():
            if all(key in result and _json(value) == _json(result[key]) for result in results[1:]):
                shared[key] = value
    evidence = {"shared_result_fields": shorten(shared)} if shared else {}
    evidence["runs"] = [
        {
            "command": run.command,
            "requested_controls": run.controls,
            "seconds": run.seconds,
            **({"printed_output": shorten(run.stdout_tail)} if run.stdout_tail else {}),
            "result_fields": shorten({key: value for key, value in result.items() if key not in shared}),
        }
        for run, result in zip(runs, results)
    ]
    return _json(evidence)


def _problem_evidence(problem: ProblemStatement) -> str:
    evidence = problem.model_dump(mode="json", exclude_none=True)
    for field, prefix in (("given_data", "D"), ("objective_terms", "O"), ("constraints", "C")):
        evidence[field] = [{"id": f"{prefix}{number}", **item} for number, item in enumerate(evidence[field], 1)]
    evidence["expected_output"] = [
        {"id": f"R{number}", "description": item} for number, item in enumerate(evidence["expected_output"], 1)
    ]
    return _json(evidence)


def _outline(source: str) -> str:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return "No Python definition index available."
    definitions = sorted(
        (node.lineno, node.end_lineno, node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    entries = ", ".join(f"{name}:{start}-{end}" for start, end, name in definitions)
    if len(entries) > MAX_OUTLINE_CHARS:
        entries = entries[:MAX_OUTLINE_CHARS].rsplit(", ", 1)[0] + ", … (additional definitions omitted)"
    return entries or "No functions or classes."


def _source_evidence(workspace: Workspace) -> tuple[str, set[str]]:
    contents = {path: workspace.resolve(path).read_text(encoding="utf-8") for path in source_files(workspace)}
    if sum(map(len, contents.values())) <= FULL_SOURCE_CHARS:
        return "\n\n".join(f"=== {path} (complete) ===\n{source}" for path, source in contents.items()), set()

    budget = min(MAX_EXCERPT_CHARS, INITIAL_SOURCE_CHARS // len(contents))
    partial = set()
    inventory = []
    for path, source in contents.items():
        if len(source) <= budget:
            inventory.append(f"=== {path} (complete) ===\n{source}")
            continue
        partial.add(path)
        excerpt = source[:budget]
        total_lines = source.count("\n") + int(not source.endswith("\n"))
        excerpt_lines = excerpt.count("\n") + int(not excerpt.endswith("\n"))
        inventory.append(
            f"=== {path} (partial, {total_lines} total lines) ===\n"
            f"Definition ranges: {_outline(source)}\n"
            f"Excerpt: lines 1–{excerpt_lines} through the first {len(excerpt)} characters.\n{excerpt}"
        )
    return "\n\n".join(inventory), partial


def _read_ranges(workspace: Workspace, requests: list[SourceRange], partial: set[str], seen: set[tuple]) -> list[dict]:
    excerpts = []
    for request in requests:
        location = (request.path, request.start_line, request.end_line, request.start_column)
        if request.path not in partial or location in seen:
            raise BuildError("Semantic review requested an unlisted, complete or repeated source range.")
        seen.add(location)
        try:
            excerpt = read_file(workspace, *location)
        except FileToolError as error:
            raise BuildError(f"Semantic review requested an invalid source range: {error}") from error
        if excerpt.get("next_line", 0) > request.end_line:
            del excerpt["next_line"]
        excerpts.append(excerpt)
    return excerpts


@model_run
def evaluate_semantics(
    workspace: Workspace, runs: list[RunOutcome], *, model: str | None = None, client=None,
    on_progress: ProgressCallback | None = None,
) -> SemanticReview:
    """Decide whether the implementation and its results solve the stated problem."""
    problem = _problem_evidence(load_problem_statement(workspace))
    listing, partial = _source_evidence(workspace)
    run_evidence = describe_runs(runs)
    model = model_name(DEFAULT_MODEL, model, stage="semantic_review")
    _, system, schema = _review_settings(model)
    excerpts = []
    seen = set()
    guidance = ""
    for attempt in range(MAX_SOURCE_FOLLOWUPS + 1):
        message = load_prompt(
            "test_semantics.user", problem=problem, runs=run_evidence, files=listing,
            excerpts=_json(excerpts) if excerpts else "None yet.",
        )
        report(on_progress, "Review semantic evidence", detail=f"Source review {attempt + 1} of {MAX_SOURCE_FOLLOWUPS + 1}.")
        response = generate(
            client, model, system, message + guidance,
            stage="semantic_review", schema=schema, on_progress=on_progress,
        )
        try:
            judged = _ModelReview.model_validate_json(response.text or "")
        except ValidationError as error:
            raise BuildError("The model returned no usable semantic review.") from error

        if judged.source_requests:
            if attempt == MAX_SOURCE_FOLLOWUPS:
                break
            with progress_step(on_progress, "Read requested solver source"):
                excerpts.extend(_read_ranges(workspace, judged.source_requests, partial, seen))
            guidance = ""
            continue
        if judged.verdict == "needs_source":
            raise BuildError("Semantic review asked for more evidence without naming source ranges.")

        coverage = {item.path: item for item in judged.source_coverage if item.path in partial and item.reason.strip()}
        missing = partial - coverage.keys()
        if missing:
            guidance = "\n\n" + load_prompt("test_semantics.coverage_required", paths=", ".join(sorted(missing)))
            continue
        # The verdict follows from the issues, whatever label the model chose.
        return SemanticReview(
            verdict="falls_short" if judged.issues else "matches",
            summary=judged.summary,
            checks_done=judged.checks_done,
            issues=judged.issues,
            model=model,
            source_coverage=[coverage[path] for path in sorted(coverage)],
        )

    raise BuildError("Semantic review could not reach a verdict within the source review limit.")
