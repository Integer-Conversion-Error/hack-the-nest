"""Build step 2 of the agent loop: compare the implementation with the plan.

Runs when the Build agent has called `finish`. Automatic checks look at the
files first (entry point, syntax, control flags, imports). Then Gemini, in a
fresh conversation that has not seen the builder's reasoning, reads the plan
and the source files and decides whether they match.

Reads   <workspace>/plan.json, problem.json, build.json and the agent's .py files
Writes  <workspace>/build_review.json

Usage:
    python -m backend.build_review <workspace-id>
"""

import argparse
import ast
import os
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.build import (
    BUILD_JSON,
    DEFAULT_MODEL,
    ENTRY_POINT,
    BuildError,
    BuildResult,
    build_contract,
    gemini_client,
    generate,
    source_files,
    unfinished_issues,
)
from backend.plan import PLAN_JSON, PlanningResult, load_plan, render_markdown
from backend.problem_statement import ENV_FILE, load_problem_statement
from backend.progress import ProgressCallback, progress_step
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, open_workspace

BUILD_REVIEW_JSON = "build_review.json"

# Modules the solver may import besides the standard library and its own files.
ALLOWED_PACKAGES = frozenset({"numpy", "scipy", "QuantumRingsLib", "qr_runtime"})


class PlanIssue(BaseModel):
    file: str = Field(description="The file the issue is in, or 'missing' if something was not built.")
    plan_requirement: str = Field(description="The part of the plan that is not met.")
    problem: str = Field(description="What the code does instead.")
    fix: str = Field(description="What to change.")


class _ModelReview(BaseModel):
    """What Gemini returns."""

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
    """Problems found by inspecting the files, without Gemini and without running anything."""
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


def compare_with_plan(
    workspace: Workspace, *, model: str | None = None, client=None, on_progress: ProgressCallback | None = None,
) -> BuildReview:
    """Decide whether the workspace's implementation matches its approved plan, and save the result."""
    with progress_step(on_progress, "Load the plan and solver files for review") as step:
        if not workspace.resolve(PLAN_JSON).is_file():
            raise BuildError(f"Run Plan first to create {PLAN_JSON} for workspace {workspace.id}.")
        planning = load_plan(workspace)
        problem = load_problem_statement(workspace)
        sources = source_files(workspace)
        if not sources:
            raise BuildError(f"Run Build first. Workspace {workspace.id} has no solver files to review.")

        build_record = workspace.resolve(BUILD_JSON)
        summary = "(no build record)"
        if build_record.is_file():
            summary = BuildResult.model_validate_json(build_record.read_text(encoding="utf-8")).summary
        step.completed(f"Solver files to review: {len(sources)}.")

    found = automatic_issues(workspace, planning, on_progress=on_progress)
    listing = "\n\n".join(
        f"=== {path} ===\n{workspace.resolve(path).read_text(encoding='utf-8')}" for path in sources
    )

    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    with progress_step(on_progress, "Review the solver files against the approved plan") as step:
        response = generate(
            client or gemini_client(),
            model,
            # Prompts are read from prompts.md now, before Gemini is called.
            load_prompt("build_review.system", contract=build_contract(planning, workspace)),
            load_prompt(
                "build_review.user",
                problem=problem.model_dump_json(indent=2),
                plan=render_markdown(planning),  # the same text Plan saves as PLAN.md
                summary=summary,
                checks="\n".join(f"- FAILED: {issue}" for issue in found) or "All passed.",
                files=listing,
            ),
            on_progress=on_progress,
            response_mime_type="application/json",
            response_json_schema=_ModelReview.model_json_schema(),
        )
        try:
            judged = _ModelReview.model_validate_json(response.text or "")
        except ValidationError as error:
            raise BuildError("Gemini returned no usable review.") from error

        if found or judged.issues:
            notes = [judged.summary] + [
                f"{issue.file}: {issue.problem} Fix: {issue.fix}" for issue in judged.issues
            ]
            if found:
                notes.insert(0, f"Automatic checks also found {len(found)} issues.")
            step.failed("\n".join(notes))
        else:
            step.completed(judged.summary)

    # The verdict follows from the issues, whatever label Gemini chose.
    review = BuildReview(
        verdict="falls_short" if found or judged.issues else "matches",
        summary=judged.summary,
        automatic_issues=found,
        issues=judged.issues,
        accepted_deviations=judged.accepted_deviations,
        files_reviewed=sources,
        model=model,
    )
    with progress_step(on_progress, "Save the build review"):
        workspace.resolve(BUILD_REVIEW_JSON).write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return review


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Compare a workspace's implementation with its plan.")
    parser.add_argument("workspace_id")
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        review = compare_with_plan(workspace)
    except (OSError, BuildError, PromptError, ValidationError, WorkspaceError) as error:
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
