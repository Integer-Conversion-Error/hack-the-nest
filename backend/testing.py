"""The whole Test phase of the agent loop:

  1. Run main.py                                              (backend.runner)
  2. Evaluate the build result. If the run fails, go back to Build with the error.
  3. Evaluate semantics: compare the implementation and its results with the
     problem statement                                        (backend.semantics)
     - If it matches, the workspace is ready for Final Go/No-Go.
     - If not, go back to Build with the semantic issues.

"Back to Build" runs the whole Build phase again (fix the files, compare with
the plan), then Test starts over.

Reads   <workspace>/main.py and the other solver files, problem.json, plan.json
Writes  <workspace>/results.json  (by main.py; from the run with default controls)
        <workspace>/test.json     (record of the last Test round)

Usage:
    python -m backend.testing <workspace-id>
    python -m backend.testing <workspace-id> --no-rebuild    # test once, report, change nothing
"""

import argparse
import json
import sys
from collections.abc import Callable
from fractions import Fraction

from pydantic import BaseModel, ValidationError

from backend.build import BuildError
from backend.build_phase import BuildNotApproved, run_build_phase
from backend.plan import PLAN_JSON, PlanControl, load_plan
from backend.problem_statement import ENV_FILE
from backend.prompt_loader import PromptError
from backend.runner import RESULTS_JSON, RunOutcome, run_solver
from backend.semantics import SemanticReview, evaluate_semantics
from backend.workspace import Workspace, WorkspaceError, open_workspace

TEST_JSON = "test.json"
DEFAULT_MAX_ROUNDS = 4  # the first test plus up to three trips back to Build
MAX_CONTROL_RUNS = 2  # extra runs with a non-default control value


class TestRound(BaseModel):
    """One pass through Test: the runs, and the semantic review if every run passed."""

    runs: list[RunOutcome]
    semantics: SemanticReview | None = None

    @property
    def passed(self) -> bool:
        return all(run.passed for run in self.runs) and self.semantics is not None and self.semantics.matches

    def feedback(self) -> str:
        """What to send back to Build."""
        failed = next((run for run in self.runs if not run.passed), None)
        if failed:
            return failed.feedback()
        return self.semantics.feedback() if self.semantics else ""


class TestPhaseResult(BaseModel):
    """Returned when the solver runs and solves the stated problem: ready for Final Go/No-Go."""

    workspace_id: str
    rounds: int
    last_round: TestRound


class TestNotPassed(BuildError):
    """The solver still fails Test after the allowed rounds."""

    def __init__(self, message: str, last_round: TestRound) -> None:
        super().__init__(message)
        self.last_round = last_round


def _alternate_value(control: PlanControl):
    if control.allowed_values is not None:
        return next((value for value in control.allowed_values if value != control.default), None)
    if control.type == "boolean":
        return not control.default

    minimum, maximum = control.minimum, control.maximum
    lower, upper = Fraction(str(minimum)), Fraction(str(maximum))
    midpoint = (lower + upper) / 2
    step = Fraction(str(control.step)) if control.step is not None else None
    if step is None and control.type == "integer":
        step = Fraction(1)

    if step is None:
        samples = (midpoint, (lower + midpoint) / 2, (midpoint + upper) / 2)
    else:
        center = lower + ((midpoint - lower) // step) * step
        samples = (center, center + step, center - step)

    for sample in samples:
        if not lower < sample < upper:
            continue
        try:
            candidate = int(sample) if sample.denominator == 1 else float(sample)
        except OverflowError:
            continue
        if (
            minimum < candidate < maximum and candidate != control.default
            and (step is None or (Fraction(str(candidate)) - lower) % step == 0)
        ):
            return candidate

    if minimum != control.default:
        return minimum
    if maximum != control.default and (step is None or (upper - lower) % step == 0):
        return maximum
    return None


def control_variants(workspace: Workspace) -> list[dict]:
    """Test up to MAX_CONTROL_RUNS alternate values, preferring interior points for ranges."""
    if not workspace.resolve(PLAN_JSON).is_file():
        return []
    variants = []
    for control in load_plan(workspace).plan.controls:
        alternate = _alternate_value(control)
        if alternate is not None:
            variants.append({control.key: alternate})
            if len(variants) == MAX_CONTROL_RUNS:
                break
    return variants


def test_once(
    workspace: Workspace,
    *,
    model: str | None = None,
    client=None,
    on_event: Callable[[str], None] | None = None,
    validate_results: Callable[[dict, dict], None] | None = None,
) -> TestRound:
    """Steps 1 to 3 without going back to Build. Saves test.json and returns the round."""
    say = on_event or (lambda message: None)

    def run(controls: dict | None = None) -> RunOutcome:
        outcome = run_solver(workspace, controls, validate_results=validate_results)
        verdict = "passed" if outcome.passed else "FAILED: " + " ".join(outcome.issues)
        say(f"    {outcome.command} ({outcome.seconds} s): {verdict}")
        return outcome

    say("--- Test: running main.py")
    runs = [run()]
    if runs[0].passed:
        runs += [run(variant) for variant in control_variants(workspace)]
        if len(runs) > 1 and all(r.passed for r in runs):
            # Keep the same default result that Test checked, without running the circuit again.
            workspace.resolve(RESULTS_JSON).write_text(
                json.dumps(runs[0].results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
            )
    round_ = TestRound(runs=runs)

    if all(r.passed for r in runs):
        say("--- Test: evaluating semantics against the problem statement")
        round_.semantics = evaluate_semantics(workspace, runs, model=model, client=client)
        say(f"    {round_.semantics.verdict}: {round_.semantics.summary}")

    workspace.resolve(TEST_JSON).write_text(round_.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return round_


def run_test_phase(
    workspace: Workspace,
    *,
    model: str | None = None,
    client=None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    on_event: Callable[[str], None] | None = None,
    validate_results: Callable[[dict, dict], None] | None = None,
) -> TestPhaseResult:
    """Test the built solver, sending it back to Build with the issues until it passes.

    Raises TestNotPassed after max_rounds, or if a trip back to Build leaves the same issues.
    Raises BuildNotApproved if Build cannot get its fixes to match the plan.
    """
    if max_rounds < 1:
        raise BuildError("Allow at least one test round.")
    say = on_event or (lambda message: None)

    previous_feedback = None
    for round_number in range(1, max_rounds + 1):
        say(f"=== Test round {round_number} of {max_rounds}")
        round_ = test_once(
            workspace, model=model, client=client, on_event=on_event, validate_results=validate_results,
        )
        if round_.passed:
            say("=== Test passed. Ready for Final Go/No-Go.")
            return TestPhaseResult(workspace_id=workspace.id, rounds=round_number, last_round=round_)

        feedback = round_.feedback()
        if feedback == previous_feedback:
            raise TestNotPassed("A trip back to Build left the same issues, so Test stopped.", round_)
        previous_feedback = feedback
        if round_number < max_rounds:
            say(f"=== Test failed. Going back to Build with:\n{feedback}")
            run_build_phase(workspace, feedback, model=model, client=client, on_event=on_event)

    raise TestNotPassed(f"The solver still fails Test after {max_rounds} rounds.", round_)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Run the Test phase on a built workspace.")
    parser.add_argument("workspace_id")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    parser.add_argument("--no-rebuild", action="store_true", help="Test once and report without going back to Build")
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        if args.no_rebuild:
            round_ = test_once(workspace, on_event=print)
            if not round_.passed:
                sys.exit(f"\nTest failed. This would go back to Build:\n{round_.feedback()}")
            rounds = 1
        else:
            rounds = run_test_phase(workspace, max_rounds=args.max_rounds, on_event=print).rounds
    except TestNotPassed as error:
        sys.exit(f"Test phase stopped: {error}\nRemaining issues:\n{error.last_round.feedback()}")
    except BuildNotApproved as error:
        sys.exit(f"Test phase stopped in Build: {error}\nRemaining issues:\n{error.review.feedback()}")
    except (OSError, BuildError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Test phase failed: {error}")

    print(f"\nReady for Final Go/No-Go after {rounds} test round(s).")
    print(f"Wrote: {workspace.resolve(TEST_JSON)}")


if __name__ == "__main__":
    main()
