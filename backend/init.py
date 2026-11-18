"""The full Init phase of the agent loop:
  1. initialize a new working directory   (backend.workspace)
  2. parse the prompt as a problem statement (backend.problem_statement)

Usage:
    python -m backend.init example-user-prompts/ev-fleet-dispatch-optimization.md
"""

import argparse
import sys
from pathlib import Path

from backend.prompt_loader import PromptError
from backend.model_provider import ModelError
from backend.problem_statement import (
    ENV_FILE,
    PROBLEM_MD,
    ProblemStatement,
    ProblemStatementError,
    parse_problem_statement,
)
from backend.workspace import Workspace, WorkspaceError, init_workspace


def run_init(prompt: str, name: str = "", client=None) -> tuple[Workspace, ProblemStatement]:
    workspace = init_workspace(prompt, name=name)
    problem = parse_problem_statement(workspace, client=client)
    return workspace, problem


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)

    parser = argparse.ArgumentParser(description="Run the Init phase on a prompt file.")
    parser.add_argument("prompt_file", type=Path, help="Markdown/text file with the user's prompt")
    args = parser.parse_args()

    try:
        prompt = args.prompt_file.read_text(encoding="utf-8")
        workspace, problem = run_init(prompt, name=args.prompt_file.stem)
    except (OSError, WorkspaceError, PromptError, ProblemStatementError, ModelError) as error:
        sys.exit(f"Init failed: {error}")

    print(f"Workspace id: {workspace.id}")
    print(f"Problem:      {problem.title}")
    print(f"Wrote:        {workspace.resolve(PROBLEM_MD)}")
    if not problem.is_optimization_problem:
        print(f"Rejected:     {problem.rejection_reason}")


if __name__ == "__main__":
    main()
