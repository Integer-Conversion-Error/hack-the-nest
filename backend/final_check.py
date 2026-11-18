"""Review problem fidelity and plan fidelity, then record the decision at handoff.

The pipeline runs each review before starting its downstream work. A standalone
Final check evaluates the current artifacts and reuses matching approvals.

Reads   <workspace>/prompt.md, problem.json, plan.json and the solver files
Writes  <workspace>/final.json

Usage:
    python -m backend.final_check <workspace-id>
"""

import argparse
import json
import sys
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.build import DEFAULT_MODEL, BuildError, build_contract
from backend.build_review import BuildReview, compare_with_plan
from backend.model_provider import generate, model_name, model_provider, model_run
from backend.model_types import ModelError
from backend.progress import ProgressCallback, progress_step, report
from backend.plan import PlanningResult, load_plan
from backend.planning import plan_fidelity_fingerprint
from backend.problem_statement import ENV_FILE, load_problem_statement
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, fingerprint, open_workspace, write_atomic

FINAL_JSON = "final.json"

Stage = Literal["init", "plan", "build"]


class FinalIssue(BaseModel):
    requirement: str = Field(description="The part of the source document that is not respected.")
    problem: str = Field(description="What the checked document says or does instead.")
    fix: str = Field(description="What to change.")


class _ModelCheck(BaseModel):
    """The model's response for checks 1 and 2."""

    verdict: Literal["matches", "falls_short"]
    summary: str = Field(description="Two or three sentences on how well the two documents agree.")
    issues: list[FinalIssue]


class FinalCheck(BaseModel):
    name: str
    returns_to: Stage
    verdict: Literal["matches", "falls_short"]
    summary: str
    issues: list[str]
    fingerprint: str | None = None

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
    *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    response = generate(
        client, model, system, message,
        stage="final_review", schema=_ModelCheck.model_json_schema(), on_progress=on_progress,
    )
    try:
        judged = _ModelCheck.model_validate_json(response.text or "")
    except ValidationError as error:
        raise BuildError(f"The model returned no usable result for the check '{name}'.") from error
    return FinalCheck(
        name=name,
        returns_to=returns_to,
        verdict="falls_short" if judged.issues else "matches",  # the verdict follows from the issues
        summary=judged.summary,
        issues=[f"{issue.problem} Requirement: {issue.requirement} Fix: {issue.fix}" for issue in judged.issues],
    )


def _review(
    workspace: Workspace, client, model: str, name: str, stage: Stage, system: str, message: str,
    *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    signature = fingerprint(model_provider(), model, system, message, _ModelCheck.model_json_schema())
    path = workspace.resolve(f".review-{stage}.json")
    try:
        saved = FinalCheck.model_validate_json(path.read_bytes())
    except (FileNotFoundError, ValidationError):
        saved = None
    if saved is not None and saved.passed and saved.returns_to == stage and saved.fingerprint == signature:
        report(on_progress, "Reuse the fidelity review", status="completed", detail=saved.summary)
        return saved
    result = _judge(client, model, name, stage, system, message, on_progress=on_progress)
    if result.passed:
        result.fingerprint = signature
        write_atomic(path, result.model_dump_json().encode("utf-8"))
    return result


def check_problem_against_prompt(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    return _review(
        workspace, client, model, "Problem statement against the original prompt", "init",
        load_prompt("final.problem.system"),
        load_prompt("final.problem.user", prompt=workspace.prompt, problem=load_problem_statement(workspace).model_dump_json()),
        on_progress=on_progress,
    )


def check_plan_against_problem(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    planning = load_plan(workspace)
    if planning.fidelity_fingerprint and planning.fidelity_fingerprint == plan_fidelity_fingerprint(workspace, model=model):
        report(on_progress, "Reuse the approved plan review", status="completed", detail="The plan and fidelity review policy are unchanged.")
        return approved_plan_check(planning)
    return _review(
        workspace, client, model, "Plan against the problem statement", "plan",
        load_prompt("final.plan.system", contract=build_contract(planning, workspace)),
        load_prompt(
            "final.plan.user",
            problem=load_problem_statement(workspace).model_dump_json(),
            plan=planning.plan.implementation_json(),
            answers=json.dumps(planning.answers, ensure_ascii=False),
        ),
        on_progress=on_progress,
    )


def approved_plan_check(planning: PlanningResult) -> FinalCheck:
    return FinalCheck(
        name="Plan against the problem statement", returns_to="plan", verdict="matches",
        summary=planning.review.reason or "Planning verified the selected solver against the problem statement.", issues=[],
    )


def build_check(review: BuildReview) -> FinalCheck:
    return FinalCheck(
        name="Implementation against the plan",
        returns_to="build",
        verdict=review.verdict,
        summary=review.summary,
        issues=[line.removeprefix("- ") for line in review.feedback().splitlines()],
    )


def check_implementation_against_plan(
    workspace: Workspace, client, model: str, *, on_progress: ProgressCallback | None = None,
) -> FinalCheck:
    return build_check(compare_with_plan(workspace, model=model, client=client, on_progress=on_progress))


CHECKS = (
    ("Check the problem statement against the prompt", check_problem_against_prompt),
    ("Check the plan against the problem statement", check_plan_against_problem),
    ("Check the implementation against the plan", check_implementation_against_plan),
)


def save_decision(workspace: Workspace, checks: list[FinalCheck], model: str) -> FinalDecision:
    failed = next((check.returns_to for check in checks if not check.passed), None)
    decision = FinalDecision(decision="NO_GO" if failed else "GO", failed_stage=failed, checks=checks, model=model)
    write_atomic(workspace.resolve(FINAL_JSON), (decision.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return decision


@model_run
def run_final_checks(
    workspace: Workspace, *, model: str | None = None, client=None, on_progress: ProgressCallback | None = None,
) -> FinalDecision:
    """Run the three checks in order, stop at the first failure, and save the decision."""
    build_review_model = model_name(DEFAULT_MODEL, model, stage="build_review")
    model = model_name(DEFAULT_MODEL, model, stage="final_review")

    def run_check(label, check):
        with progress_step(on_progress, label) as step:
            check_model = build_review_model if check is check_implementation_against_plan else model
            result = check(workspace, client, check_model, on_progress=on_progress)
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
    with progress_step(on_progress, "Save the verification decision"):
        return save_decision(workspace, checks, model)


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
    except (OSError, BuildError, ModelError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Final check failed: {error}")

    print(describe(decision))
    print(f"Wrote: {workspace.resolve(FINAL_JSON)}")
    sys.exit(0 if decision.go else 1)


if __name__ == "__main__":
    main()
