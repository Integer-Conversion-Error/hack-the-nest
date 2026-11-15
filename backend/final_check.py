"""Final Go/No-Go: three checks, in order, before the result goes to the frontend.

  1. Problem statement against the user's original prompt   -> bad: back to Init (parse)
  2. Plan against the problem statement                     -> bad: back to Plan
  3. Implementation against the plan                        -> bad: back to Build
  Then GO.

Checking stops at the first check that fails, because everything after it
would be redone anyway. Going back is expensive, so a failed check is run a
second time and only counts if it fails again. This module only decides;
backend/pipeline.py acts on the decision.

Reads   <workspace>/prompt.md, problem.json, plan.json and the solver files
Writes  <workspace>/final.json

Usage:
    python -m backend.final_check <workspace-id>
"""

import argparse
import os
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.build import DEFAULT_MODEL, BuildError, build_contract, gemini_client, generate
from backend.build_review import compare_with_plan
from backend.plan import load_plan, render_markdown
from backend.problem_statement import ENV_FILE, load_problem_statement
from backend.progress import ProgressCallback, progress_step
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, open_workspace

FINAL_JSON = "final.json"

Stage = Literal["init", "plan", "build"]


class FinalIssue(BaseModel):
    requirement: str = Field(description="The part of the source document that is not respected.")
    problem: str = Field(description="What the checked document says or does instead.")
    fix: str = Field(description="What to change.")


class _ModelCheck(BaseModel):
    """What Gemini returns for checks 1 and 2."""

    verdict: Literal["matches", "falls_short"]
    summary: str = Field(description="Two or three sentences on how well the two documents agree.")
    issues: list[FinalIssue]


class FinalCheck(BaseModel):
    name: str
    returns_to: Stage  # the stage that has to redo its work if this check fails
    verdict: Literal["matches", "falls_short"]
    summary: str
    issues: list[str]

    @property
    def passed(self) -> bool:
        return self.verdict == "matches"


class FinalDecision(BaseModel):
    decision: Literal["GO", "NO_GO"]
    failed_stage: Stage | None  # where to go back to on NO_GO
    checks: list[FinalCheck]
    model: str

    @property
    def go(self) -> bool:
        return self.decision == "GO"

    def feedback(self) -> str:
        """The failed check's issues, for the stage that has to redo its work."""
        failed = next((check for check in self.checks if not check.passed), None)
        return "\n".join(f"- {issue}" for issue in failed.issues) if failed else ""


def _judge(
    client, model: str, name: str, returns_to: Stage, system: str, message: str,
    on_progress: ProgressCallback | None,
) -> FinalCheck:
    response = generate(
        client, model, system, message,
        on_progress=on_progress,
        response_mime_type="application/json",
        response_json_schema=_ModelCheck.model_json_schema(),
    )
    try:
        judged = _ModelCheck.model_validate_json(response.text or "")
    except ValidationError as error:
        raise BuildError(f"Gemini returned no usable result for the check '{name}'.") from error
    return FinalCheck(
        name=name,
        returns_to=returns_to,
        verdict="falls_short" if judged.issues else "matches",  # the verdict follows from the issues
        summary=judged.summary,
        issues=[f"{issue.problem} Requirement: {issue.requirement} Fix: {issue.fix}" for issue in judged.issues],
    )


def check_problem_against_prompt(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    return _judge(
        client, model, "Problem statement against the original prompt", "init",
        load_prompt("final.problem.system"),
        load_prompt(
            "final.problem.user",
            prompt=workspace.prompt,
            problem=load_problem_statement(workspace).model_dump_json(indent=2),
        ),
        on_progress,
    )


def check_plan_against_problem(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    planning = load_plan(workspace)
    return _judge(
        client, model, "Plan against the problem statement", "plan",
        load_prompt("final.plan.system", contract=build_contract(planning, workspace)),
        load_prompt(
            "final.plan.user",
            problem=load_problem_statement(workspace).model_dump_json(indent=2),
            plan=render_markdown(planning),
        ),
        on_progress,
    )


def check_implementation_against_plan(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    review = compare_with_plan(workspace, model=model, client=client, on_progress=on_progress)
    return FinalCheck(
        name="Implementation against the plan",
        returns_to="build",
        verdict=review.verdict,
        summary=review.summary,
        issues=[line.removeprefix("- ") for line in review.feedback().splitlines()],
    )


CHECKS = (
    ("Check the problem statement against the prompt", check_problem_against_prompt),
    ("Check the plan against the problem statement", check_plan_against_problem),
    ("Check the implementation against the plan", check_implementation_against_plan),
)


def run_final_checks(
    workspace: Workspace, *, model: str | None = None, client=None, on_progress: ProgressCallback | None = None,
) -> FinalDecision:
    """Run the three checks in order, stop at the first failure, and save the decision."""
    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    client = client or gemini_client()

    def run_check(label, check):
        with progress_step(on_progress, label) as step:
            result = check(workspace, client, model, on_progress=on_progress)
            if result.passed:
                step.completed(result.summary)
            else:
                step.failed("\n".join([result.summary, *result.issues]))
            return result

    checks = []
    for label, check in CHECKS:
        result = run_check(label, check)
        if not result.passed:
            # A judge can flip on a borderline point. Send work back only if it fails twice.
            confirmation = run_check(f"{label} (confirmation)", check)
            if confirmation.passed:
                result = confirmation
        checks.append(result)
        if not result.passed:
            break
    failed = None if checks[-1].passed else checks[-1].returns_to
    decision = FinalDecision(decision="NO_GO" if failed else "GO", failed_stage=failed, checks=checks, model=model)
    with progress_step(on_progress, "Save the verification decision"):
        workspace.resolve(FINAL_JSON).write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return decision


def describe(decision: FinalDecision) -> str:
    lines = []
    for check in decision.checks:
        lines.append(f"{'PASS' if check.passed else 'FAIL'}  {check.name}: {check.summary}")
    skipped = len(CHECKS) - len(decision.checks)
    if skipped:
        lines.append(f"({skipped} later check(s) not run)")
    if decision.go:
        lines.append("Decision: GO")
    else:
        lines.append(f"Decision: NO-GO. Go back to: {decision.failed_stage}")
        lines.append(decision.feedback())
    return "\n".join(lines)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Run the three Final Go/No-Go checks on a tested workspace.")
    parser.add_argument("workspace_id")
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        decision = run_final_checks(workspace)
    except (OSError, BuildError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Final check failed: {error}")

    print(describe(decision))
    print(f"Wrote: {workspace.resolve(FINAL_JSON)}")
    sys.exit(0 if decision.go else 1)


if __name__ == "__main__":
    main()
