"""The whole agent loop, from a user's prompt to a GO handoff for the frontend:

  Init   Parse and check the problem statement against the original prompt.
  Plan   Review the proposed solver for fidelity and feasibility before Build.
  Build  Review existing files or write a new solver. Patch specific findings.
  Test   Verify solver runs and record GO once the current artifacts pass.

Resume reuses approvals for matching inputs and repairs existing artifacts only
when a check identifies an issue.

Usage:
    python -m backend.pipeline example-user-prompts/ev-fleet-dispatch-optimization.md
    python -m backend.pipeline --workspace <workspace-id>          # resume
    python -m backend.pipeline --workspace <workspace-id> --answer "..."   # answer a planning question
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from backend.build import DEFAULT_MODEL, ENTRY_POINT, BuildError
from backend.build_phase import BuildNotApproved, run_build_phase
from backend.build_review import solver_fingerprint
from backend.final_check import (
    FINAL_JSON,
    FinalCheck,
    FinalDecision,
    check_implementation_against_plan,
    check_plan_against_problem,
    check_problem_against_prompt,
    describe,
    save_decision,
)
from backend.model_provider import ModelError, model_name, model_run, model_session
from backend.progress import ProgressCallback, progress_step, report
from backend.plan import PLAN_JSON, load_plan
from backend.planning import (
    ClarificationRequired,
    PlanningError,
    ReinterpretationRequired,
    planning_answers,
    run_planning,
)
from backend.problem_statement import (
    ENV_FILE,
    PROBLEM_JSON,
    ProblemStatementError,
    load_problem_statement,
    parse_problem_statement,
)
from backend.prompt_loader import PromptError
from backend.runner import CIRCUIT_DIAGRAM, RESULTS_JSON
from backend.testing import TestNotPassed, run_test_phase
from backend.workspace import Workspace, WorkspaceError, fingerprint, init_workspace, open_workspace, write_atomic

HANDOFF_JSON = "handoff.json"
DEFAULT_MAX_FINAL_ROUNDS = 3  # one review and up to two scoped repairs per gate
PipelineStage = Literal["init", "plan", "build", "test", "verify"]


class PipelineError(Exception):
    """The pipeline stopped without a GO."""


class NoGo(PipelineError):
    """A review still fails after the allowed repair rounds."""

    def __init__(self, message: str, decision: FinalDecision) -> None:
        super().__init__(message)
        self.decision = decision


def _requirements_id(workspace: Workspace) -> str:
    return fingerprint(workspace.prompt, workspace.meta, load_problem_statement(workspace).model_dump(mode="json"))


def _prepare_requirements(
    workspace: Workspace, answers: Sequence[str], client, model: str, max_rounds: int, say: Callable[[str], None],
    *, on_stage: Callable[[PipelineStage], None] | None = None, on_progress: ProgressCallback | None = None,
) -> tuple[FinalCheck, FinalCheck, str]:
    if on_stage is not None:
        on_stage("init")
    if not workspace.resolve(PROBLEM_JSON).is_file():
        say("##### Init: parsing the prompt into a problem statement")
        parse_problem_statement(workspace, client=client, on_progress=on_progress)

    init_repairs = 0
    plan_repairs = 0
    plan_feedback = None
    previous_draft = None

    def repair_problem(check: FinalCheck) -> None:
        nonlocal init_repairs
        feedback = "\n".join(check.issues)
        while init_repairs < max_rounds - 1:
            init_repairs += 1
            if on_stage is not None:
                on_stage("init")
            report(on_progress, "Return to prompt parsing", detail=feedback)
            say(f"##### Init: repairing the problem statement\n{feedback}")
            try:
                parse_problem_statement(workspace, feedback=feedback, client=client, on_progress=on_progress)
                return
            except ProblemStatementError as error:
                feedback = "\n".join([*check.issues, str(error)])
        decision = save_decision(workspace, [check], model)
        raise NoGo(f"The problem statement could not be repaired. {feedback}", decision)

    while True:
        problem = load_problem_statement(workspace)
        if not problem.is_optimization_problem:
            raise PipelineError(f"The prompt was rejected: {problem.rejection_reason}")
        before = _requirements_id(workspace)
        with progress_step(on_progress, "Check the problem statement against the prompt") as step:
            problem_check = check_problem_against_prompt(workspace, client, model, on_progress=on_progress)
            if problem_check.passed:
                step.completed(problem_check.summary)
            else:
                step.failed("\n".join([problem_check.summary, *problem_check.issues]))
        if before != _requirements_id(workspace):
            raise PipelineError("The prompt or problem changed during its review. Resume to check the current inputs.")
        if not problem_check.passed:
            repair_problem(problem_check)
            continue

        if on_stage is not None:
            on_stage("plan")
        planning = load_plan(workspace) if workspace.resolve(PLAN_JSON).is_file() else None
        if planning is not None and not plan_feedback:
            limits = workspace.meta["limits"]
            qubits = limits["max_qubits"]
            if problem.quantum.qubit_budget is not None:
                qubits = min(qubits, problem.quantum.qubit_budget)
            if planning.max_qubits > qubits or planning.worker_seconds > limits["run_timeout_seconds"]:
                plan_feedback = "Check the existing plan against the tighter qubit and worker-time limits. Update only what is affected."
            elif planning_answers(planning.answers, answers) != planning.answers:
                plan_feedback = "Apply the new user clarifications to the existing plan. Preserve everything still valid."

        if planning is None or plan_feedback:
            if plan_feedback:
                report(on_progress, "Return to planning", detail=plan_feedback)
            say("##### Plan: repairing the saved plan" if planning or previous_draft else "##### Plan")
            try:
                planning = run_planning(
                    workspace, plan_feedback, answers=answers, client=client, previous_draft=previous_draft,
                    on_progress=on_progress,
                )
            except ReinterpretationRequired as error:
                previous_draft = error.checkpoint
                plan_feedback = "The problem statement was corrected. Update only the parts of the plan affected by the correction."
                repair_problem(FinalCheck(
                    name="Problem statement against the original prompt", returns_to="init",
                    verdict="falls_short", summary=str(error), issues=[str(error)],
                ))
                continue
            say(f"Selected solver: {planning.plan.selected.name} (approved after {planning.attempts} attempt(s))")

        plan_id = fingerprint(before, planning.model_dump(mode="json"))
        with progress_step(on_progress, "Check the plan against the problem statement") as step:
            plan_check = check_plan_against_problem(workspace, client, model, on_progress=on_progress)
            if plan_check.passed:
                step.completed(plan_check.summary)
            else:
                step.failed("\n".join([plan_check.summary, *plan_check.issues]))
        if plan_id != fingerprint(_requirements_id(workspace), load_plan(workspace).model_dump(mode="json")):
            raise PipelineError("The problem or plan changed during its review. Resume to check the current inputs.")
        if plan_check.passed:
            return problem_check, plan_check, plan_id
        if plan_repairs == max_rounds - 1:
            decision = save_decision(workspace, [problem_check, plan_check], model)
            raise NoGo("The plan still fails its fidelity review.", decision)
        plan_repairs += 1
        plan_feedback = "\n".join(plan_check.issues)


def write_handoff(workspace: Workspace, decision: FinalDecision) -> dict:
    """Collect what the frontend needs after GO: the problem, the method, the controls and the result."""
    problem = load_problem_statement(workspace)
    planning = load_plan(workspace)
    solver = planning.plan.selected
    handoff = {
        "workspace_id": workspace.id,
        "decision": decision.decision,
        "problem": {
            "title": problem.title,
            "summary": problem.summary,
            "objective_sense": problem.objective_sense,
            "expected_output": problem.expected_output,
        },
        "method": {
            "name": solver.name,
            "method": solver.method,
            "encoding": solver.encoding,
            "classical_steps": solver.classical_steps,
            "quantum_steps": solver.quantum_steps,
            "qubits": solver.qubits.total,
        },
        "controls": [control.model_dump(mode="json") for control in planning.plan.controls],
        "rerun_command": f"python {ENTRY_POINT} --<control key> <value>",
        "circuit_diagram": CIRCUIT_DIAGRAM,  # picture of the final circuit, in the workspace folder
        "results": json.loads(workspace.resolve(RESULTS_JSON).read_text(encoding="utf-8")),
        "final_checks": [{"name": check.name, "summary": check.summary} for check in decision.checks],
    }
    output = json.dumps(handoff, indent=2, ensure_ascii=False) + "\n"
    write_atomic(workspace.resolve(HANDOFF_JSON), output.encode("utf-8"))
    return handoff


@model_run
def run_pipeline(
    workspace: Workspace,
    *,
    answers: Sequence[str] = (),
    client=None,
    max_final_rounds: int = DEFAULT_MAX_FINAL_ROUNDS,
    on_event: Callable[[str], None] | None = None,
    on_stage: Callable[[PipelineStage], None] | None = None,
    on_progress: ProgressCallback | None = None,
    validate_results: Callable[[dict, dict], None] | None = None,
) -> dict:
    """Take a workspace from wherever it is to a GO handoff. Returns the handoff.

    answers: the user's replies to planning's clarifying questions, if any.
    validate_results: an optional check of the result and requested overrides, raising ValueError on failure.
    Raises NoGo, PipelineError, or the stage's own error when a stage cannot finish.
    """
    if max_final_rounds < 1:
        raise PipelineError("Allow at least one review per gate.")
    say = on_event or (lambda message: None)
    model = model_name(DEFAULT_MODEL, stage="final_review")
    build_review_model = model_name(DEFAULT_MODEL, stage="build_review")
    workspace.resolve(HANDOFF_JSON).unlink(missing_ok=True)
    workspace.resolve(FINAL_JSON).unlink(missing_ok=True)
    problem_check, plan_check, plan_id = _prepare_requirements(
        workspace, answers, client, model, max_final_rounds, say, on_stage=on_stage, on_progress=on_progress,
    )

    feedback = None
    for _ in range(max_final_rounds):
        if on_stage is not None:
            on_stage("build")
        say("##### Build")
        run_build_phase(workspace, feedback, client=client, on_event=on_event, on_progress=on_progress, review_existing=True)
        say("##### Test")
        run_test_phase(workspace, client=client, on_event=on_event, on_stage=on_stage, on_progress=on_progress,
                       validate_results=validate_results, reuse=True)

        if plan_id != fingerprint(_requirements_id(workspace), load_plan(workspace).model_dump(mode="json")):
            raise PipelineError("The problem or plan changed during Build or Test. Resume to check the current inputs.")
        tested_inputs = solver_fingerprint(workspace)
        if on_stage is not None:
            on_stage("verify")
        say("##### Final Go/No-Go")
        with progress_step(on_progress, "Check the implementation against the plan") as step:
            implementation_check = check_implementation_against_plan(workspace, client, build_review_model, on_progress=on_progress)
            if implementation_check.passed:
                step.completed(implementation_check.summary)
            else:
                step.failed("\n".join([implementation_check.summary, *implementation_check.issues]))
        if solver_fingerprint(workspace) != tested_inputs:
            raise PipelineError("Solver inputs changed during Final review. Resume to verify the current implementation.")
        decision = save_decision(workspace, [problem_check, plan_check, implementation_check], model)
        say(describe(decision))
        if decision.go:
            report(on_progress, "Approve the result", status="completed")
            with progress_step(on_progress, "Save the verified handoff"):
                handoff = write_handoff(workspace, decision)
            say(f"##### GO. Wrote {workspace.resolve(HANDOFF_JSON)}")
            return handoff
        feedback = decision.feedback()
        report(on_progress, "Return to Build", detail=feedback)

    report(on_progress, "Verification stopped", status="failed", detail=f"No approval after {max_final_rounds} verification rounds.")
    raise NoGo(f"Final Go/No-Go still fails after {max_final_rounds} rounds.", decision)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Run the whole agent loop on a prompt.")
    parser.add_argument("prompt_file", nargs="?", type=Path, help="Markdown/text file with the user's prompt")
    parser.add_argument("--workspace", help="Resume an existing workspace instead of starting from a prompt file")
    parser.add_argument("--answer", action="append", default=[], help="Answer to a planning question, repeat as needed")
    parser.add_argument("--max-final-rounds", type=int, default=DEFAULT_MAX_FINAL_ROUNDS,
                        help="Maximum reviews and scoped repairs per gate")
    args = parser.parse_args()
    if bool(args.prompt_file) == bool(args.workspace):
        parser.error("Give either a prompt file or --workspace.")

    try:
        if args.workspace:
            workspace = open_workspace(args.workspace)
        else:
            workspace = init_workspace(args.prompt_file.read_text(encoding="utf-8"), name=args.prompt_file.stem)
        print(f"Workspace: {workspace.id}")
        with model_session() as client:
            handoff = run_pipeline(
                workspace, answers=args.answer, client=client, max_final_rounds=args.max_final_rounds, on_event=print,
            )
    except ClarificationRequired as error:
        sys.exit(f"{error}\nRerun with --workspace {workspace.id} and --answer for each question.")
    except NoGo as error:
        sys.exit(f"NO-GO: {error}\n{describe(error.decision)}")
    except TestNotPassed as error:
        sys.exit(f"Stopped in Test: {error}\nRemaining issues:\n{error.last_round.feedback()}")
    except BuildNotApproved as error:
        sys.exit(f"Stopped in Build: {error}\nRemaining issues:\n{error.review.feedback()}")
    except (
        OSError, BuildError, ModelError, PipelineError, PlanningError, ProblemStatementError,
        PromptError, ValidationError, WorkspaceError,
    ) as error:
        sys.exit(f"Pipeline failed: {error}")

    objective = handoff["results"].get("objective", {})
    print(f"\nGO: {handoff['problem']['title']}")
    print(f"Method: {handoff['method']['name']} on {handoff['method']['qubits']} qubits")
    print(f"Result: {objective.get('value')} {objective.get('unit')} ({objective.get('direction')})")
    print(f"Handoff for the frontend: {workspace.resolve(HANDOFF_JSON)}")


if __name__ == "__main__":
    main()
