"""The whole Test phase of the agent loop:

  1. Run main.py                                              (backend.runner)
  2. Evaluate the build result. If the run fails, go back to Build with the error.
  3. Evaluate semantics: compare the implementation and its results with the
     problem statement                                        (backend.semantics)
     - If it matches, the workspace is ready for Final Go/No-Go.
     - If not, go back to Build with the semantic issues.

Build receives the failed run or semantic issues and patches the existing files.
The pipeline can reuse runs for the exact same solver inputs.

Reads   <workspace>/main.py and the other solver files, problem.json, plan.json
Writes  <workspace>/results.json  (by main.py; from the run with default controls)
        <workspace>/test.json     (record of the last Test round)

Usage:
    python -m backend.testing <workspace-id>
    python -m backend.testing <workspace-id> --no-rebuild    # test once, report, change nothing
"""

import argparse
import json
import os
import sys
from typing import Literal
from collections.abc import Callable
from fractions import Fraction
from hashlib import sha256

from pydantic import BaseModel, ValidationError

from backend.build import BuildError
from backend.build_phase import BuildNotApproved, run_build_phase
from backend.build_review import solver_fingerprint
from backend.plan import PLAN_JSON, PlanControl, load_plan
from backend.model_provider import ModelError, model_run
from backend.progress import ProgressCallback, progress_step, report
from backend.problem_statement import ENV_FILE
from backend.prompt_loader import PromptError
from backend.runner import CIRCUIT_DIAGRAM, RESULTS_JSON, RunOutcome, run_solver
from backend.semantics import SemanticReview, evaluate_semantics, semantics_fingerprint
from backend.workspace import Workspace, WorkspaceError, fingerprint, open_workspace, write_atomic

TEST_JSON = "test.json"
DEFAULT_MAX_ROUNDS = 4  # the first test plus up to three trips back to Build
MAX_CONTROL_RUNS = 2  # extra runs with a non-default control value


class TestRound(BaseModel):
    """One pass through Test: the runs, and the semantic review if every run passed."""

    runs: list[RunOutcome]
    semantics: SemanticReview | None = None
    runtime_fingerprint: str | None = None
    semantics_fingerprint: str | None = None
    circuit_digest: str | None = None

    @property
    def passed(self) -> bool:
        return bool(self.runs) and all(run.passed for run in self.runs) and self.semantics is not None and self.semantics.matches

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


def _circuit(workspace: Workspace) -> bytes | None:
    try:
        return workspace.resolve(CIRCUIT_DIAGRAM).read_bytes()
    except FileNotFoundError:
        return None


def _circuit_digest(image: bytes | None) -> str | None:
    return sha256(image).hexdigest() if image is not None else None


def _runtime_fingerprint(workspace: Workspace, variants: list[dict]) -> str:
    return fingerprint(1, solver_fingerprint(workspace), variants, sys.executable, sys.version,
                       os.getenv("QR_ACCOUNT"), os.getenv("QR_BACKEND"), os.getenv("QR_KEY"))


def _saved_runs(workspace: Workspace, signature: str, variants: list[dict], validate_results) -> TestRound | None:
    try:
        saved = TestRound.model_validate_json(workspace.resolve(TEST_JSON).read_bytes())
        result = json.loads(workspace.resolve(RESULTS_JSON).read_bytes())
    except (FileNotFoundError, ValueError):
        return None
    expected_controls = [{}, *variants]
    if (
        saved.runtime_fingerprint != signature or len(saved.runs) != len(expected_controls)
        or any(
            not run.passed or run.timed_out or run.exit_code != 0 or not isinstance(run.results, dict)
            or fingerprint(run.controls) != fingerprint(expected)
            for run, expected in zip(saved.runs, expected_controls)
        )
    ):
        return None
    if fingerprint(result) != fingerprint(saved.runs[0].results) or saved.circuit_digest != _circuit_digest(_circuit(workspace)):
        return None
    if validate_results is not None:
        for run in saved.runs:
            try:
                validate_results(run.results, run.controls)
            except ValueError:
                return None
    return saved


@model_run
def test_once(
    workspace: Workspace,
    *,
    model: str | None = None,
    client=None,
    on_event: Callable[[str], None] | None = None,
    on_progress: ProgressCallback | None = None,
    validate_results: Callable[[dict, dict], None] | None = None,
    reuse: bool = False,
) -> TestRound:
    """Steps 1 to 3 without going back to Build. Saves test.json and returns the round."""
    say = on_event or (lambda message: None)

    def run(controls: dict | None = None) -> RunOutcome:
        outcome = run_solver(workspace, controls, validate_results=validate_results, on_progress=on_progress)
        verdict = "passed" if outcome.passed else "FAILED: " + " ".join(outcome.issues)
        say(f"    {outcome.command} ({outcome.seconds} s): {verdict}")
        return outcome

    with progress_step(on_progress, "Select alternate settings to test") as step:
        variants = control_variants(workspace)
        step.completed(f"Selected {len(variants)} alternate settings to test.")
    signature = _runtime_fingerprint(workspace, variants)
    round_ = _saved_runs(workspace, signature, variants, validate_results) if reuse else None
    if round_ is not None:
        say("--- Test: reusing verified runs for unchanged solver inputs")
        report(on_progress, "Reuse verified solver runs", status="completed", detail="The solver inputs and run policy are unchanged.")
        after_runs = signature
    else:
        say("--- Test: running main.py")
        runs = [run()]
        default_diagram = _circuit(workspace)
        if runs[0].passed and variants:
            try:
                runs.extend(run(variant) for variant in variants)
            finally:
                # The handoff uses the default results and their own circuit.
                output = json.dumps(runs[0].results, indent=2, ensure_ascii=False) + "\n"
                write_atomic(workspace.resolve(RESULTS_JSON), output.encode("utf-8"))
                circuit = workspace.resolve(CIRCUIT_DIAGRAM)
                if default_diagram is not None:
                    write_atomic(circuit, default_diagram)
                else:
                    circuit.unlink(missing_ok=True)
        round_ = TestRound(runs=runs, circuit_digest=_circuit_digest(default_diagram))
        after_runs = _runtime_fingerprint(workspace, variants)
        if signature != after_runs:
            raise BuildError("Solver inputs changed during execution. Resume to verify the current implementation.")
        if all(item.passed for item in runs):
            round_.runtime_fingerprint = signature

    if all(item.passed for item in round_.runs):
        policy = fingerprint(semantics_fingerprint(model=model), [item.model_dump(mode="json") for item in round_.runs])
        if round_.semantics is None or not round_.semantics.matches or round_.semantics_fingerprint != policy:
            # A review request can fail independently of the completed solver runs.
            round_.semantics = None
            round_.semantics_fingerprint = None
            if round_.runtime_fingerprint:
                write_atomic(workspace.resolve(TEST_JSON), round_.model_dump_json().encode("utf-8"))
            say("--- Test: evaluating semantics against the problem statement")
            with progress_step(on_progress, "Check the solution against the problem") as step:
                round_.semantics = evaluate_semantics(workspace, round_.runs, model=model, client=client, on_progress=on_progress)
                if round_.semantics.matches:
                    step.completed(round_.semantics.summary)
                else:
                    step.failed(round_.semantics.summary)
            for check in round_.semantics.checks_done:
                report(on_progress, "Semantic check", detail=check)
            round_.semantics_fingerprint = policy
            say(f"    {round_.semantics.verdict}: {round_.semantics.summary}")
        else:
            say("--- Test: reusing the matching semantic review")
            report(on_progress, "Reuse the semantic review", status="completed", detail=round_.semantics.summary)
        if _runtime_fingerprint(workspace, variants) != after_runs:
            raise BuildError("Solver inputs changed during Test. Resume to verify the current implementation.")

    with progress_step(on_progress, "Save the test report"):
        write_atomic(workspace.resolve(TEST_JSON), (round_.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return round_


@model_run
def run_test_phase(
    workspace: Workspace,
    *,
    model: str | None = None,
    client=None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    on_event: Callable[[str], None] | None = None,
    on_stage: Callable[[Literal["build", "test"]], None] | None = None,
    on_progress: ProgressCallback | None = None,
    validate_results: Callable[[dict, dict], None] | None = None,
    reuse: bool = False,
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
        if on_stage is not None:
            on_stage("test")
        say(f"=== Test round {round_number} of {max_rounds}")
        round_ = test_once(
            workspace, model=model, client=client, on_event=on_event,
            on_progress=on_progress, validate_results=validate_results, reuse=reuse,
        )
        if round_.passed:
            say("=== Test passed. Ready for Final Go/No-Go.")
            report(on_progress, "Approve the solver tests", status="completed")
            return TestPhaseResult(workspace_id=workspace.id, rounds=round_number, last_round=round_)

        feedback = round_.feedback()
        if feedback == previous_feedback:
            report(
                on_progress, "Testing stopped", status="failed",
                detail="The latest rebuild left the same issues.",
            )
            raise TestNotPassed("A trip back to Build left the same issues, so Test stopped.", round_)
        previous_feedback = feedback
        if round_number < max_rounds:
            failed_run = next((run for run in round_.runs if not run.passed), None)
            issues = "\n".join(failed_run.issues) if failed_run else feedback
            report(on_progress, "Return to Build", detail=issues)
            if on_stage is not None:
                on_stage("build")
            say(f"=== Test failed. Going back to Build with:\n{feedback}")
            run_build_phase(
                workspace, feedback, model=model, client=client, on_event=on_event, on_progress=on_progress,
            )

    report(on_progress, "Testing stopped", status="failed", detail=f"No approval after {max_rounds} test rounds.")
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
    except (OSError, BuildError, ModelError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Test phase failed: {error}")

    print(f"\nReady for Final Go/No-Go after {rounds} test round(s).")
    print(f"Wrote: {workspace.resolve(TEST_JSON)}")


if __name__ == "__main__":
    main()
