"""The whole agent loop, from a user's prompt to a GO handoff for the frontend:

  Init            new workspace, parse the prompt into a problem statement
  Plan            research, draft and review a solver plan
  Build           write the solver files, compare them with the plan
  Test            run main.py, evaluate the run, evaluate semantics
  Final Go/No-Go  three checks; a failed check sends the work back:
                    problem statement vs prompt  -> Init (parse), then Plan, Build, Test
                    plan vs problem statement    -> Plan, then Build, Test
                    implementation vs plan       -> Build, then Test
  GO              write handoff.json for the frontend

A workspace can be resumed: stages whose output already exists are skipped.

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

from pydantic import ValidationError

from backend.build import ENTRY_POINT, BuildError
from backend.build_phase import BuildNotApproved, run_build_phase
from backend.build_review import BUILD_REVIEW_JSON, BuildReview
from backend.final_check import FinalDecision, describe, run_final_checks
from backend.plan import PLAN_JSON, load_plan
from backend.planning import ClarificationRequired, PlanningError, run_planning
from backend.problem_statement import (
    ENV_FILE,
    PROBLEM_JSON,
    ProblemStatementError,
    load_problem_statement,
    parse_problem_statement,
)
from backend.prompt_loader import PromptError, load_prompt
from backend.runner import RESULTS_JSON
from backend.testing import TestNotPassed, run_test_phase
from backend.workspace import Workspace, WorkspaceError, init_workspace, open_workspace

HANDOFF_JSON = "handoff.json"
DEFAULT_MAX_FINAL_ROUNDS = 3  # the first decision plus up to two trips back


class PipelineError(Exception):
    """The pipeline stopped without a GO."""


class NoGo(PipelineError):
    """Final Go/No-Go still fails after the allowed rounds."""

    def __init__(self, message: str, decision: FinalDecision) -> None:
        super().__init__(message)
        self.decision = decision


def _build_is_approved(workspace: Workspace) -> bool:
    review = workspace.resolve(BUILD_REVIEW_JSON)
    if not review.is_file() or not workspace.resolve(ENTRY_POINT).is_file():
        return False
    return BuildReview.model_validate_json(review.read_text(encoding="utf-8")).matches


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
        "results": json.loads(workspace.resolve(RESULTS_JSON).read_text(encoding="utf-8")),
        "final_checks": [{"name": check.name, "summary": check.summary} for check in decision.checks],
    }
    workspace.resolve(HANDOFF_JSON).write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return handoff


def run_pipeline(
    workspace: Workspace,
    *,
    answers: Sequence[str] = (),
    client=None,
    max_final_rounds: int = DEFAULT_MAX_FINAL_ROUNDS,
    on_event: Callable[[str], None] | None = None,
) -> dict:
    """Take a workspace from wherever it is to a GO handoff. Returns the handoff.

    answers: the user's replies to planning's clarifying questions, if any.
    Raises NoGo, PipelineError, or the stage's own error when a stage cannot finish.
    """
    say = on_event or (lambda message: None)

    def plan(feedback: str | None = None) -> None:
        say("##### Plan")
        result = run_planning(workspace, feedback, answers=answers, client=client)
        say(f"Selected solver: {result.plan.selected.name} (approved after {result.attempts} attempt(s))")

    def build(feedback: str | None = None) -> None:
        say("##### Build")
        run_build_phase(workspace, feedback, client=client, on_event=on_event)

    def test() -> None:
        say("##### Test")
        run_test_phase(workspace, client=client, on_event=on_event)

    # --- Forward pass, skipping stages whose output already exists
    if not workspace.resolve(PROBLEM_JSON).is_file():
        say("##### Init: parsing the prompt into a problem statement")
        parse_problem_statement(workspace, client=client)
    problem = load_problem_statement(workspace)
    if not problem.is_optimization_problem:
        raise PipelineError(f"The prompt was rejected: {problem.rejection_reason}")
    say(f"Problem: {problem.title} ({problem.quantum.method})")

    if not workspace.resolve(PLAN_JSON).is_file():
        plan()
    if not _build_is_approved(workspace):
        build()
    test()

    # --- Final Go/No-Go, going back to the stage a failed check points at
    for round_number in range(1, max_final_rounds + 1):
        say(f"##### Final Go/No-Go (round {round_number} of {max_final_rounds})")
        decision = run_final_checks(workspace, client=client)
        say(describe(decision))
        if decision.go:
            handoff = write_handoff(workspace, decision)
            say(f"##### GO. Wrote {workspace.resolve(HANDOFF_JSON)}")
            return handoff
        if round_number == max_final_rounds:
            break

        issues = decision.feedback()
        if decision.failed_stage == "init":
            say("##### Init: revising the problem statement")
            parse_problem_statement(workspace, feedback=issues, client=client)
            if not load_problem_statement(workspace).is_optimization_problem:
                raise PipelineError("The revised problem statement was rejected.")
            plan(load_prompt("final.problem_changed", issues=issues))
            build(load_prompt("final.plan_changed", issues=issues))
        elif decision.failed_stage == "plan":
            plan(issues)
            build(load_prompt("final.plan_changed", issues=issues))
        else:
            build(issues)
        test()

    raise NoGo(f"Final Go/No-Go still fails after {max_final_rounds} rounds.", decision)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Run the whole agent loop on a prompt.")
    parser.add_argument("prompt_file", nargs="?", type=Path, help="Markdown/text file with the user's prompt")
    parser.add_argument("--workspace", help="Resume an existing workspace instead of starting from a prompt file")
    parser.add_argument("--answer", action="append", default=[], help="Answer to a planning question, repeat as needed")
    parser.add_argument("--max-final-rounds", type=int, default=DEFAULT_MAX_FINAL_ROUNDS)
    args = parser.parse_args()
    if bool(args.prompt_file) == bool(args.workspace):
        parser.error("Give either a prompt file or --workspace.")

    try:
        if args.workspace:
            workspace = open_workspace(args.workspace)
        else:
            workspace = init_workspace(args.prompt_file.read_text(encoding="utf-8"), name=args.prompt_file.stem)
        print(f"Workspace: {workspace.id}")
        handoff = run_pipeline(workspace, answers=args.answer, max_final_rounds=args.max_final_rounds, on_event=print)
    except ClarificationRequired as error:
        sys.exit(f"{error}\nRerun with --workspace {workspace.id} and --answer for each question.")
    except NoGo as error:
        sys.exit(f"NO-GO: {error}\n{describe(error.decision)}")
    except TestNotPassed as error:
        sys.exit(f"Stopped in Test: {error}\nRemaining issues:\n{error.last_round.feedback()}")
    except BuildNotApproved as error:
        sys.exit(f"Stopped in Build: {error}\nRemaining issues:\n{error.review.feedback()}")
    except (
        OSError, BuildError, PipelineError, PlanningError, ProblemStatementError,
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
