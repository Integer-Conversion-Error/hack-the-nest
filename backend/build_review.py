"""Build step 2 of the agent loop: compare the implementation with the plan.

Runs when the Build agent has called `finish`. Automatic checks look at the
files first (entry point, syntax, control flags, imports). If they pass, the
model reviews the source against the selected design. Passing reviews can be
reused while the implementation and review inputs stay the same.

Reads   the workspace inputs and review prompts
Writes  <workspace>/build_review.json

Usage:
    python -m backend.build_review <workspace-id>
"""

import argparse
import ast
import sys
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.build import (
    DEFAULT_MODEL,
    ENTRY_POINT,
    BuildError,
    build_contract,
    source_files,
    unfinished_issues,
)
from backend.model_provider import generate, model_name, model_provider, model_run
from backend.model_types import ModelError
from backend.progress import ProgressCallback, progress_step, report
from backend.plan import PLAN_JSON, PlanningResult, load_plan
from backend.problem_statement import ENV_FILE, ProblemStatement, load_problem_statement
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, fingerprint, open_workspace, write_atomic

BUILD_REVIEW_JSON = "build_review.json"
_OUTPUT_FILES = frozenset({
    BUILD_REVIEW_JSON, "results.json", "circuit.png", "test.json", "final.json", "handoff.json", "model_usage.json", "model_usage.jsonl",
    "dispatch_animation.html", ".review-init.json", ".review-plan.json",
})
_FINGERPRINT_VERSION = 1

# Modules the solver may import besides the standard library and its own files.
ALLOWED_PACKAGES = frozenset({"numpy", "scipy", "QuantumRingsLib", "qr_runtime"})


class PlanIssue(BaseModel):
    file: str = Field(description="The file the issue is in, or 'missing' if something was not built.")
    plan_requirement: str = Field(description="The part of the plan that is not met.")
    problem: str = Field(description="What the code does instead.")
    fix: str = Field(description="What to change.")


class _ModelReview(BaseModel):
    """The model's review response."""

    verdict: Literal["matches", "falls_short"]
    summary: str = Field(description="Two or three sentences on how well the implementation matches the plan.")
    issues: list[PlanIssue]
    accepted_deviations: list[str] = Field(description="Differences from the plan that are acceptable, and why.")


class BuildReview(BaseModel):
    """The saved result of the comparison."""

    verdict: Literal["matches", "falls_short"]
    summary: str
    automatic_issues: list[str]
    issues: list[PlanIssue]
    accepted_deviations: list[str]
    files_reviewed: list[str]
    model: str
    fingerprint: str | None = None

    @property
    def matches(self) -> bool:
        return self.verdict == "matches"

    def feedback(self) -> str:
        """The issues as text for run_build(feedback=...), when Build has to be redone."""
        lines = [f"- {issue}" for issue in self.automatic_issues]
        lines += [
            f"- {issue.file}: {issue.problem} Plan requirement: {issue.plan_requirement} Fix: {issue.fix}"
            for issue in self.issues
        ]
        return "\n".join(lines)


def automatic_issues(
    workspace: Workspace, planning: PlanningResult, *, on_progress: ProgressCallback | None = None,
) -> list[str]:
    """Problems found by inspecting the files, without a model call and without running anything."""
    with progress_step(on_progress, "Check the entry point and Python syntax") as step:
        issues = unfinished_issues(workspace)
        if issues:
            step.failed("\n".join(issues))
    sources = source_files(workspace)
    local_modules = {path.removesuffix(".py").split("/")[0] for path in sources}

    entry = workspace.resolve(ENTRY_POINT)
    if entry.is_file():
        with progress_step(on_progress, "Check solver control flags") as step:
            all_source = "\n".join(workspace.resolve(path).read_text(encoding="utf-8") for path in sources)
            missing_controls = [
                f"No --{control.key} flag found for the plan control '{control.label}'."
                for control in planning.plan.controls if f"--{control.key}" not in all_source
            ]
            if missing_controls:
                step.failed("\n".join(missing_controls))
                issues.extend(missing_controls)

    with progress_step(on_progress, "Check Python imports") as step:
        import_issues = []
        for path in sources:
            try:
                tree = ast.parse(workspace.resolve(path).read_text(encoding="utf-8"))
            except SyntaxError:
                continue  # already reported above
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names and top not in ALLOWED_PACKAGES and top not in local_modules:
                        import_issues.append(f"{path} imports '{top}', which is not installed for solvers.")
        if import_issues:
            step.failed("\n".join(import_issues))
            issues.extend(import_issues)
    return issues


def solver_fingerprint(workspace: Workspace) -> str:
    """Hash workspace inputs, including non-Python files and the read-only runtime helper."""
    inputs = []
    for path in sorted(workspace.path.rglob("*")):
        relative = path.relative_to(workspace.path)
        name = relative.as_posix()
        if name in _OUTPUT_FILES or not path.is_file():
            continue
        if any(part == "__pycache__" or part.startswith(".publish-") for part in relative.parts):
            continue
        resolved = workspace.resolve(name)
        digest = sha256()
        with resolved.open("rb") as source:
            for block in iter(lambda: source.read(65536), b""):
                digest.update(block)
        inputs.append((name, resolved.relative_to(workspace.path).as_posix(), digest.hexdigest()))
    return fingerprint(inputs)


def _review_inputs(
    workspace: Workspace, planning: PlanningResult, problem: ProblemStatement, model: str,
) -> tuple[str, str, dict]:
    system = load_prompt("build_review.system", contract=build_contract(planning, workspace))
    user_prompt = load_prompt("build_review.user", problem="{problem}", plan="{plan}", files="{files}")
    schema = _ModelReview.model_json_schema()
    signature = fingerprint({
        "version": _FINGERPRINT_VERSION,
        "workspace": solver_fingerprint(workspace),
        "provider": model_provider(),
        "model": model,
        "system": system,
        "user": user_prompt,
        "plan": planning.plan.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
        "schema": schema,
        "allowed_packages": sorted(ALLOWED_PACKAGES),
        "python": sys.version_info[:3],
    })
    return signature, system, schema


def build_fingerprint(workspace: Workspace, *, model: str | None = None) -> str:
    """The identity of the workspace inputs and the model's review contract."""
    signature, _, _ = _review_inputs(
        workspace, load_plan(workspace), load_problem_statement(workspace), model_name(DEFAULT_MODEL, model, stage="build_review"),
    )
    return signature


def _saved_approval(workspace: Workspace) -> BuildReview | None:
    try:
        review = BuildReview.model_validate_json(workspace.resolve(BUILD_REVIEW_JSON).read_bytes())
    except (FileNotFoundError, ValidationError):
        return None
    if review.matches and review.fingerprint and not (review.automatic_issues or review.issues):
        return review
    return None


def cached_build_review(workspace: Workspace, *, model: str | None = None) -> BuildReview | None:
    """Find a passing Build review of these exact inputs, if one was saved."""
    review = _saved_approval(workspace)
    if review is None:
        return None
    try:
        return review if review.fingerprint == build_fingerprint(workspace, model=model) else None
    except (FileNotFoundError, ValidationError):
        return None


def _save_review(workspace: Workspace, review: BuildReview) -> BuildReview:
    write_atomic(workspace.resolve(BUILD_REVIEW_JSON), (review.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return review


@model_run
def compare_with_plan(
    workspace: Workspace, *, model: str | None = None, client=None, on_progress: ProgressCallback | None = None,
) -> BuildReview:
    """Decide whether the workspace's implementation matches its approved plan, and save the result."""
    if not workspace.resolve(PLAN_JSON).is_file():
        raise BuildError(f"Run Plan first to create {PLAN_JSON} for workspace {workspace.id}.")
    planning = load_plan(workspace)
    found = automatic_issues(workspace, planning, on_progress=on_progress)
    sources = source_files(workspace)
    if found:
        return _save_review(workspace, BuildReview(
            verdict="falls_short",
            summary="The implementation failed the automatic checks.",
            automatic_issues=found,
            issues=[],
            accepted_deviations=[],
            files_reviewed=sources,
            model="automatic checks",
        ))

    model = model_name(DEFAULT_MODEL, model, stage="build_review")
    problem = load_problem_statement(workspace)
    signature, system, schema = _review_inputs(workspace, planning, problem, model)
    saved = _saved_approval(workspace)
    if saved and saved.fingerprint == signature:
        report(on_progress, "Reuse the build review", status="completed", detail=saved.summary)
        return saved

    listing = "\n\n".join(
        f"=== {path} ===\n{workspace.resolve(path).read_text(encoding='utf-8')}" for path in sources
    )

    with progress_step(on_progress, "Review the solver files against the approved plan") as step:
        response = generate(
            client,
            model,
            system,
            load_prompt(
                "build_review.user",
                problem=problem.model_dump_json(exclude_none=True),
                plan=planning.plan.implementation_json(),
                files=listing,
            ),
            stage="build_review", schema=schema, on_progress=on_progress,
        )
        try:
            judged = _ModelReview.model_validate_json(response.text or "")
        except ValidationError as error:
            raise BuildError("The model returned no usable review.") from error

        if judged.issues:
            step.failed(judged.summary)
        else:
            step.completed(judged.summary)

    # The verdict follows from the issues, whatever label the model chose.
    review = BuildReview(
        verdict="falls_short" if judged.issues else "matches",
        summary=judged.summary,
        automatic_issues=found,
        issues=judged.issues,
        accepted_deviations=judged.accepted_deviations,
        files_reviewed=sources,
        model=model,
        fingerprint=signature,
    )
    if build_fingerprint(workspace, model=model) != signature:
        raise BuildError("Build inputs changed during the review. Review the current implementation again.")
    with progress_step(on_progress, "Save the build review"):
        return _save_review(workspace, review)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Compare a workspace's implementation with its plan.")
    parser.add_argument("workspace_id")
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        review = compare_with_plan(workspace)
    except (OSError, BuildError, ModelError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Review failed: {error}")

    print(f"Verdict: {review.verdict.upper()}")
    print(f"Summary: {review.summary}")
    if review.accepted_deviations:
        print("Accepted deviations:")
        for deviation in review.accepted_deviations:
            print(f"- {deviation}")
    if not review.matches:
        print("Issues to send back to Build:")
        print(review.feedback())
    print(f"Wrote: {workspace.resolve(BUILD_REVIEW_JSON)}")
    sys.exit(0 if review.matches else 1)


if __name__ == "__main__":
    main()
