"""The whole Build phase of the agent loop:

  1. The agent creates, reads, updates and deletes Python files  (backend.build)
  2. When it thinks it is done, compare the result with the plan (backend.build_review)
  3. If it matches, the workspace is ready for the Test phase
  4. If it falls short, go back to step 1 with the issues

Test sends work back here the same way: call run_build_phase with the run
error or semantic issues as `feedback`.

Usage:
    python -m backend.build_phase <workspace-id>
    python -m backend.build_phase <workspace-id> --feedback "main.py crashes with ..."
"""

import argparse
import sys
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from backend.build import ENTRY_POINT, BuildError, run_build
from backend.build_review import BuildReview, compare_with_plan
from backend.problem_statement import ENV_FILE
from backend.progress import ProgressCallback, report
from backend.prompt_loader import PromptError
from backend.workspace import Workspace, WorkspaceError, open_workspace

DEFAULT_MAX_ROUNDS = 4  # the first build plus up to three rounds of fixes


class BuildNotApproved(BuildError):
    """The implementation still falls short of the plan after the allowed rounds."""

    def __init__(self, message: str, review: BuildReview) -> None:
        super().__init__(message)
        self.review = review


class BuildPhaseResult(BaseModel):
    """Returned when the implementation matches the plan and Test can start."""

    workspace_id: str
    entry_point: str
    rounds: int
    files: list[str]
    review: BuildReview


def run_build_phase(
    workspace: Workspace,
    feedback: str | None = None,
    *,
    model: str | None = None,
    client=None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    on_event: Callable[[str], None] | None = None,
    on_progress: ProgressCallback | None = None,
) -> BuildPhaseResult:
    """Build, compare with the plan, and repeat until they match.

    feedback: issues from the Test phase when it sends the work back to Build.
    Raises BuildNotApproved if the plan is still not met after max_rounds, or if a
    round of fixes leaves exactly the same issues.
    """
    if max_rounds < 1:
        raise BuildError("Allow at least one build round.")
    say = on_event or (lambda message: None)

    previous_feedback = None
    for round_number in range(1, max_rounds + 1):
        say(f"--- Build round {round_number} of {max_rounds}: writing files")
        report(on_progress, f"Build round {round_number} of {max_rounds}")
        built = run_build(
            workspace, feedback, model=model, client=client, on_event=on_event, on_progress=on_progress,
        )

        say(f"--- Build round {round_number}: comparing with the plan")
        review = compare_with_plan(workspace, model=model, client=client, on_progress=on_progress)
        if review.matches:
            report(on_progress, "Approve the solver for testing", status="completed")
            say("--- Matches the plan. Ready for the Test phase.")
            return BuildPhaseResult(
                workspace_id=workspace.id,
                entry_point=ENTRY_POINT,
                rounds=round_number,
                files=built.files,
                review=review,
            )

        feedback = review.feedback()
        say(f"--- Falls short of the plan. Sending back to Build:\n{feedback}")
        if feedback == previous_feedback:
            report(on_progress, "Stop build revisions", status="failed", detail="The same issues remained after a revision.")
            raise BuildNotApproved("A round of fixes left the same issues, so Build stopped.", review)
        previous_feedback = feedback
        if round_number < max_rounds:
            report(
                on_progress, "Revise the solver",
                detail=f"The build review found issues. Returning to file editing for round {round_number + 1}.",
            )

    report(
        on_progress, "Stop build revisions", status="failed",
        detail=f"The solver still has issues after {max_rounds} build rounds.",
    )
    raise BuildNotApproved(f"The implementation still falls short of the plan after {max_rounds} rounds.", review)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Run the Build phase: write the solver, then check it against the plan.")
    parser.add_argument("workspace_id")
    parser.add_argument("--feedback", help="Issues from the Test phase to fix")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        result = run_build_phase(workspace, args.feedback, max_rounds=args.max_rounds, on_event=print)
    except BuildNotApproved as error:
        sys.exit(f"Build phase stopped: {error}\nRemaining issues:\n{error.review.feedback()}")
    except (OSError, BuildError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Build phase failed: {error}")

    print(f"\nReady for Test after {result.rounds} round(s).")
    print(f"Files: {', '.join(result.files)}")
    print(f"Next: run {result.entry_point} in {workspace.path}")


if __name__ == "__main__":
    main()
