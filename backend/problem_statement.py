"""Init step 2 of the agent loop: rewrite the user's natural-language prompt
into a precise, structured problem statement with Gemini.

Prompt text comes from backend/prompts.md (problem_statement.* sections),
re-read on every call.

Reads   <workspace>/prompt.md
Writes  <workspace>/problem.json  - machine-readable, used by Plan / Test / Go-No-Go
        <workspace>/PROBLEM.md    - the same content as readable Markdown

Usage from code:
    problem = parse_problem_statement(workspace)
    problem = parse_problem_statement(workspace, feedback="...")  # retry from Final Go/No-Go

Usage from a terminal (re-parse an existing workspace):
    python -m backend.problem_statement <workspace-id> [--feedback "..."]
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, open_workspace

PROBLEM_JSON = "problem.json"
PROBLEM_MD = "PROBLEM.md"

DEFAULT_MODEL = "gemini-3.8-flash"  # override with the GEMINI_MODEL environment variable

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ProblemStatementError(Exception):
    """Raised when Gemini returns nothing usable."""


# --- Schema Gemini must fill in ------------------------------------------------
# Field descriptions are sent to Gemini as part of the schema, so they double
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
    method: str = Field(description="Gate-based quantum method to use, e.g. 'QAOA'.")
    method_source: Literal["stated", "chosen"] = Field(
        description="'stated' if the user named this method, 'chosen' if you picked it."
    )
    method_reason: str = Field(description="One or two sentences on why this method fits the problem.")
    quantum_part: str = Field(description="Which part of the problem is solved on the quantum side.")
    classical_part: str | None = Field(
        default=None, description="Which part is solved classically; null if none."
    )
    qubit_estimate: int | None = Field(
        default=None, description="Qubit count the user stated for the basic formulation; null if not stated."
    )
    qubit_budget: int | None = Field(
        default=None, description="Maximum qubits the user allows (including auxiliary qubits); null if not stated."
    )


class ProblemStatement(BaseModel):
    is_optimization_problem: bool = Field(
        description="False if the prompt is not an optimization problem a quantum approach could address."
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


def parse_problem_statement(
    workspace: Workspace,
    feedback: str | None = None,
    model: str | None = None,
    client=None,
) -> ProblemStatement:
    """Turn the workspace's prompt.md into problem.json and PROBLEM.md, and return the result.

    feedback: issues found by Final Go/No-Go when this step is being redone.
    client:   an existing google.genai.Client; one is created from GEMINI_API_KEY if omitted.
    """
    from google import genai
    from google.genai import types

    client = client or genai.Client()
    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

    # Prompts are read from prompts.md now, before Gemini is called.
    system_instruction = load_prompt(
        "problem_statement.system", max_qubits=workspace.meta["limits"]["max_qubits"]
    )
    contents = load_prompt("problem_statement.user", prompt=workspace.prompt)
    if feedback:
        previous_path = workspace.resolve(PROBLEM_JSON)
        previous = previous_path.read_text(encoding="utf-8") if previous_path.is_file() else "(none saved)"
        contents += "\n\n" + load_prompt("problem_statement.retry", previous=previous, feedback=feedback)

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=ProblemStatement,
        ),
    )

    problem = response.parsed
    if not isinstance(problem, ProblemStatement):
        reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
        raise ProblemStatementError(f"Gemini returned no valid problem statement (finish reason: {reason}).")

    workspace.resolve(PROBLEM_JSON).write_text(problem.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.resolve(PROBLEM_MD).write_text(render_markdown(problem), encoding="utf-8")
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
        ("Method", f"{q.method} ({q.method_source})"),
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
    except (WorkspaceError, PromptError, ProblemStatementError) as error:
        sys.exit(f"Could not parse problem statement: {error}")

    print(f"Wrote {workspace.resolve(PROBLEM_MD)}")
    if not problem.is_optimization_problem:
        print(f"Rejected: {problem.rejection_reason}")


if __name__ == "__main__":
    main()
