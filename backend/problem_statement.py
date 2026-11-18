"""Init step 2 of the agent loop: rewrite the user's natural-language prompt
into a precise, structured problem statement with the configured model.

Prompt text comes from backend/prompts.md (problem_statement.* sections),
re-read on every call.

Reads   <workspace>/prompt.md
Writes  <workspace>/problem.json  - machine-readable, used by Plan / Test / Go-No-Go
        <workspace>/PROBLEM.md    - the same content as readable Markdown

Usage from code:
    problem = parse_problem_statement(workspace)
    problem = parse_problem_statement(workspace, feedback="...")  # repair specific findings

Usage from a terminal (re-parse an existing workspace):
    python -m backend.problem_statement <workspace-id> [--feedback "..."]
"""

import argparse
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from backend.model_provider import generate, model_name, model_run
from backend.model_types import ModelError
from backend.progress import ProgressCallback, progress_step
from backend.prompt_loader import PromptError, load_prompt
from backend.structured_edit import StructuredEdit, apply_edits
from backend.workspace import Workspace, WorkspaceError, open_workspace, write_atomic

PROBLEM_JSON = "problem.json"
PROBLEM_MD = "PROBLEM.md"

DEFAULT_MODEL = "gemini-3.8-flash"  # override with the GEMINI_MODEL environment variable

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ProblemStatementError(Exception):
    """Raised when the model returns nothing usable."""


# --- Schema the model must fill in ---------------------------------------------
# Field descriptions are sent to the model as part of the schema, so they double
# as instructions.

class Parameter(BaseModel):
    name: str = Field(description="What the value is, e.g. 'Battery capacity per van'.")
    value: str = Field(description="The value exactly as given, e.g. '20' or '(0,0), (1,4)'.")
    unit: str | None = Field(default=None, description="Unit such as 'kWh' or '$/km'; null if unitless.")
    source: Literal["stated", "assumed"] = Field(
        description="'stated' if the user gave it, 'assumed' if you filled it in."
    )


class ObjectiveTerm(BaseModel):
    name: str = Field(description="Short name, e.g. 'Driving cost'.")
    weight: str = Field(description="Cost or weight as given, e.g. '$0.30/km'.")
    description: str = Field(description="What this term measures.")


class Constraint(BaseModel):
    description: str = Field(description="The constraint stated precisely, with any numbers.")
    kind: Literal["hard", "soft"] = Field(
        description="'hard' must never be violated; 'soft' is penalized in the objective."
    )


class QuantumRequirements(BaseModel):
    # The allowed methods. Keep this list in step with the one in prompts.md
    # (problem_statement.system), which tells Gemini when to use each.
    method: Literal["QAOA", "VQE", "Grover"] = Field(description="Quantum method to use.")
    method_reason: str = Field(description="One or two sentences on why this method fits the problem.")
    quantum_part: str = Field(description="Which part of the problem is solved on the quantum side.")
    classical_part: str | None = Field(
        default=None, description="Which part is solved classically; null if none."
    )
    # Optional only so problem.json files saved before this field was filled still load.
    qubit_estimate: int | None = Field(
        default=None,
        description="Always fill this in: your estimate of the qubits the method needs, including auxiliary qubits.",
    )
    qubit_budget: int | None = Field(
        default=None, description="Maximum qubits the user allows (including auxiliary qubits); null if not stated."
    )


class ProblemStatement(BaseModel):
    is_optimization_problem: bool = Field(
        description="False only if the problem is too large to run or the text describes no problem to solve."
    )
    rejection_reason: str | None = Field(
        default=None, description="Why the prompt was rejected; null when is_optimization_problem is true."
    )
    title: str = Field(description="Short title for the problem.")
    summary: str = Field(description="Two to four sentences restating the problem in plain language.")
    problem_class: str = Field(
        description="Standard problem family, e.g. 'vehicle routing with charging scheduling'."
    )
    given_data: list[Parameter] = Field(description="Every number, location, count and setting in the prompt.")
    decision_variables: list[str] = Field(description="What the solver chooses, one item per variable group.")
    objective_sense: Literal["minimize", "maximize"]
    objective_terms: list[ObjectiveTerm]
    constraints: list[Constraint]
    quantum: QuantumRequirements
    expected_output: list[str] = Field(description="What the user wants to see at the end, e.g. 'map of routes'.")
    assumptions: list[str] = Field(description="Everything you inferred that the user did not state.")
    open_questions: list[str] = Field(
        description="Ambiguities left for the Plan step, each with the default you would choose."
    )


class _ProblemEdits(BaseModel):
    edits: list[StructuredEdit]


def _revise_problem(
    workspace: Workspace, previous: ProblemStatement, feedback: str, model: str, client,
    *, on_progress: ProgressCallback | None = None,
) -> ProblemStatement:
    response = generate(
        client, model, load_prompt("problem_statement.repair_system"),
        load_prompt("problem_statement.retry", prompt=workspace.prompt, previous=previous.model_dump_json(), feedback=feedback),
        stage="interpret", schema=_ProblemEdits.model_json_schema(), on_progress=on_progress,
    )
    try:
        change = _ProblemEdits.model_validate_json(response.text or "")
        revised = apply_edits(previous, change.edits)
    except ValidationError as error:
        details = error.errors(include_input=False, include_context=False, include_url=False)
        errors = [f"{'.'.join(map(str, item['loc'])) or 'response'}: {item['msg']}" for item in details[:4]]
        raise ProblemStatementError("Invalid problem repair. " + "; ".join(errors)) from error
    except ValueError as error:
        raise ProblemStatementError(f"Invalid problem repair. {error}") from error
    if revised == previous:
        raise ProblemStatementError("The proposed repair did not change the problem statement.")
    return revised


@model_run
def parse_problem_statement(
    workspace: Workspace,
    feedback: str | None = None,
    model: str | None = None,
    client=None,
    *, on_progress: ProgressCallback | None = None,
) -> ProblemStatement:
    """Turn the workspace's prompt.md into problem.json and PROBLEM.md, and return the result.

    feedback: specific issues to repair in the saved problem statement.
    client:   an existing model client; one is opened for the configured provider if omitted.
    """
    model = model_name(DEFAULT_MODEL, model, stage="interpret")
    problem_path = workspace.resolve(PROBLEM_JSON)
    if feedback and problem_path.is_file():
        previous = load_problem_statement(workspace)
        prompt = workspace.prompt
        with progress_step(on_progress, "Revise the problem statement"):
            problem = _revise_problem(workspace, previous, feedback, model, client, on_progress=on_progress)
        if workspace.prompt != prompt or load_problem_statement(workspace) != previous:
            raise ProblemStatementError("The prompt or problem statement changed during repair.")
    else:
        with progress_step(on_progress, "Interpret the original prompt"):
            problem = _parse_problem(workspace, model, client, feedback, on_progress=on_progress)

    with progress_step(on_progress, "Save the problem statement"):
        write_atomic(problem_path, (problem.model_dump_json(indent=2) + "\n").encode("utf-8"))
        write_atomic(workspace.resolve(PROBLEM_MD), render_markdown(problem).encode("utf-8"))
    return problem


def _parse_problem(
    workspace: Workspace, model: str, client, feedback: str | None,
    *, on_progress: ProgressCallback | None = None,
) -> ProblemStatement:
    limits = workspace.meta["limits"]
    system_instruction = load_prompt(
        "problem_statement.system",
        max_qubits=limits["max_qubits"],
        run_timeout_seconds=limits["run_timeout_seconds"],
    )
    prompt = workspace.prompt
    contents = load_prompt("problem_statement.user", prompt=prompt)
    if feedback:
        contents += "\n\n" + load_prompt("problem_statement.notes", feedback=feedback)
    response = generate(
        client, model, system_instruction, contents,
        stage="interpret", schema=ProblemStatement.model_json_schema(), on_progress=on_progress,
    )

    with progress_step(on_progress, "Check the problem statement") as step:
        try:
            problem = ProblemStatement.model_validate_json(response.text)
        except ValidationError as error:
            step.failed("The model did not return a usable problem statement.")
            raise ProblemStatementError(
                f"The model returned no valid problem statement (finish reason: {response.finish_reason})."
            ) from error
        if problem.is_optimization_problem:
            step.completed(f"{problem.title} · Proposed method: {problem.quantum.method}")
        else:
            step.failed(problem.rejection_reason or "The prompt could not be used for this pipeline.")

    if workspace.prompt != prompt or workspace.meta["limits"] != limits:
        raise ProblemStatementError("The prompt or run limits changed during parsing.")
    return problem


def load_problem_statement(workspace: Workspace) -> ProblemStatement:
    """Read a previously parsed problem.json back into a ProblemStatement."""
    return ProblemStatement.model_validate_json(workspace.resolve(PROBLEM_JSON).read_text(encoding="utf-8"))


def render_markdown(problem: ProblemStatement) -> str:
    lines = [f"# {problem.title}", ""]
    if not problem.is_optimization_problem:
        lines += [f"> **Rejected:** {problem.rejection_reason}", ""]
    lines += [problem.summary, "", f"**Problem class:** {problem.problem_class}", ""]

    lines += ["## Given data", "", "| Parameter | Value | Unit | Source |", "| --- | --- | --- | --- |"]
    for p in problem.given_data:
        lines.append(f"| {p.name} | {p.value} | {p.unit or ''} | {p.source} |")

    lines += ["", "## Decision variables", ""] + [f"- {v}" for v in problem.decision_variables]

    lines += ["", f"## Objective: {problem.objective_sense}", ""]
    lines += [f"- **{t.name}** ({t.weight}): {t.description}" for t in problem.objective_terms]

    lines += ["", "## Constraints", ""] + [f"- *{c.kind}*: {c.description}" for c in problem.constraints]

    q = problem.quantum
    lines += ["", "## Quantum requirements", ""]
    for label, value in (
        ("Method", q.method),
        ("Why", q.method_reason),
        ("Quantum part", q.quantum_part),
        ("Classical part", q.classical_part),
        ("Qubit estimate", q.qubit_estimate),
        ("Qubit budget", q.qubit_budget),
    ):
        lines.append(f"- **{label}:** {value if value is not None else 'not specified'}")

    for heading, items in (
        ("Expected output", problem.expected_output),
        ("Assumptions", problem.assumptions),
        ("Open questions", problem.open_questions),
    ):
        lines += ["", f"## {heading}", ""] + ([f"- {i}" for i in items] or ["- None"])
    return "\n".join(lines) + "\n"


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)

    parser = argparse.ArgumentParser(description="Parse a workspace's prompt into a problem statement.")
    parser.add_argument("workspace_id")
    parser.add_argument("--feedback", help="Reviewer issues to fix (for a retry)")
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        problem = parse_problem_statement(workspace, feedback=args.feedback)
    except (WorkspaceError, PromptError, ProblemStatementError, ModelError) as error:
        sys.exit(f"Could not parse problem statement: {error}")

    print(f"Wrote {workspace.resolve(PROBLEM_MD)}")
    if not problem.is_optimization_problem:
        print(f"Rejected: {problem.rejection_reason}")


if __name__ == "__main__":
    main()
