"""Typed planning artifacts shared by the planner, reviewer, and later phases."""

import json
import math
import re
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from backend.workspace import Workspace

PLAN_JSON = "plan.json"
PLAN_MD = "PLAN.md"

ControlValue = StrictBool | StrictInt | StrictFloat | StrictStr
NumericValue = StrictInt | StrictFloat
_CONTROL_TYPES = {"integer": (int,), "number": (int, float), "boolean": (bool,), "string": (str,)}


class PlanControl(BaseModel):
    key: str = Field(description="Stable lowercase key such as van_2_initial_charge_pct.")
    label: str
    type: Literal["integer", "number", "boolean", "string"]
    default: ControlValue
    allowed_values: list[ControlValue] | None = None
    minimum: NumericValue | None = None
    maximum: NumericValue | None = None
    step: NumericValue | None = None
    unit: str | None = None
    group: str | None = None
    rerun_mode: Literal["bind", "rebuild", "resample"]

    @model_validator(mode="after")
    def check_control(self) -> "PlanControl":
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.key):
            raise ValueError("Control key must use lowercase letters, digits, and underscores.")
        if not self.label.strip():
            raise ValueError("Control label is required.")

        bounds = (self.minimum, self.maximum, self.step)
        has_bounds = any(value is not None for value in bounds)
        values = [self.default, *(self.allowed_values or [])]
        values.extend(value for value in bounds if value is not None)
        if any(type(value) not in _CONTROL_TYPES[self.type] for value in values):
            raise ValueError(f"Control values must match the declared {self.type} type.")
        if any(isinstance(value, float) and not math.isfinite(value) for value in values):
            raise ValueError("Control values must be finite.")

        if self.allowed_values is not None:
            if has_bounds or not self.allowed_values or self.default not in self.allowed_values:
                raise ValueError("Specify nonempty allowed values containing the default, or a numeric range.")
            return self

        if self.type == "boolean":
            return self
        if self.type == "string" or self.minimum is None or self.maximum is None:
            raise ValueError("Specify allowed values or both numeric bounds.")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("Control default must lie within its range.")
        if self.step is not None:
            if self.step <= 0:
                raise ValueError("Control step must be positive.")
            offset = Fraction(str(self.default)) - Fraction(str(self.minimum))
            if offset % Fraction(str(self.step)):
                raise ValueError("Control default must align with the step from its minimum.")
        return self


class QubitEstimate(BaseModel):
    decision_qubits: StrictInt = Field(ge=1)
    auxiliary_qubits: StrictInt = Field(ge=0)

    @property
    def total(self) -> int:
        return self.decision_qubits + self.auxiliary_qubits


class SolverExample(BaseModel):
    name: str = Field(description="Unique name for this implementation design.")
    method: str
    classical_steps: list[str]
    quantum_steps: list[str]
    encoding: str
    qubits: QubitEstimate
    estimated_worker_seconds: StrictInt | None = Field(default=None, ge=1)
    verification: list[str]
    reference_method: str
    build_steps: list[str] = Field(description="Human-readable steps for implementing this design.")

    @model_validator(mode="after")
    def check_blueprint(self) -> "SolverExample":
        if not all(value.strip() for value in (self.method, self.encoding, self.reference_method)):
            raise ValueError("A solver design requires a method, encoding, and reference method.")
        for steps in (self.quantum_steps, self.verification, self.build_steps):
            if not steps or any(not step.strip() for step in steps):
                raise ValueError("A solver design requires concrete quantum, verification, and build steps.")
        return self


class Plan(BaseModel):
    summary: str
    objective: str
    constraints: list[str]
    assumptions: list[str]
    controls: list[PlanControl]
    solver_examples: list[SolverExample]
    selected_solver: str = Field(description="Name of the proposed solver example to build.")

    @model_validator(mode="after")
    def check_choices(self) -> "Plan":
        if not self.summary.strip() or not self.objective.strip():
            raise ValueError("A plan requires a summary and an objective.")
        solver_names = [solver.name for solver in self.solver_examples]
        if any(not name.strip() for name in solver_names) or len(solver_names) != len(set(solver_names)):
            raise ValueError("Solver examples need nonempty, unique names.")
        if self.selected_solver not in solver_names:
            raise ValueError("Selected solver must match one of the solver examples.")
        control_keys = [control.key for control in self.controls]
        if len(control_keys) != len(set(control_keys)):
            raise ValueError("Control keys must be unique.")
        return self

    @property
    def selected(self) -> SolverExample:
        return next(solver for solver in self.solver_examples if solver.name == self.selected_solver)


class PlanDraft(BaseModel):
    status: Literal["ready", "needs_clarification", "unsupported"]
    plan: Plan | None = None
    questions: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def check_status(self) -> "PlanDraft":
        if self.status == "ready":
            if self.plan is None or self.questions:
                raise ValueError("A ready draft requires a plan and an empty questions list.")
        elif self.plan is not None:
            raise ValueError("Drafts that require clarification or report unsupported input must omit the plan.")
        if self.status == "needs_clarification" and not any(question.strip() for question in self.questions):
            raise ValueError("Clarification requires a question.")
        if self.status == "unsupported" and not (self.reason or "").strip():
            raise ValueError("Unsupported input requires a reason.")
        return self


class PlanReview(BaseModel):
    verdict: Literal["approve", "revise", "needs_clarification", "unsupported", "needs_reinterpretation"]
    issues: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def check_verdict(self) -> "PlanReview":
        if self.verdict == "approve" and (self.issues or self.questions):
            raise ValueError("Approval requires empty issues and questions lists.")
        if self.verdict == "revise" and not any(issue.strip() for issue in self.issues):
            raise ValueError("A revision requires an issue to fix.")
        if self.verdict == "needs_clarification" and not any(question.strip() for question in self.questions):
            raise ValueError("Clarification requires a question.")
        if self.verdict in {"unsupported", "needs_reinterpretation"}:
            if not (self.reason or "").strip() and not any(issue.strip() for issue in self.issues):
                raise ValueError("This verdict requires a reason or an issue to fix.")
        return self


class Source(BaseModel):
    title: str | None = None
    url: str


class Research(BaseModel):
    status: Literal["grounded", "unavailable"]
    summary: str
    sources: list[Source] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_sources(self) -> "Research":
        if (self.status == "grounded") != bool(self.sources):
            raise ValueError("Grounded research requires source records.")
        return self


class PlanningResult(BaseModel):
    schema_version: Literal[1] = 1
    problem_title: str
    model: str
    plan: Plan
    research: Research
    attempts: StrictInt = Field(ge=1)
    max_qubits: StrictInt = Field(ge=1)
    worker_seconds: StrictInt = Field(ge=1)
    review: PlanReview

    @model_validator(mode="after")
    def check_approval(self) -> "PlanningResult":
        if self.review.verdict != "approve":
            raise ValueError("A saved plan requires an approving review.")
        solver = self.plan.selected
        if solver.qubits.total > self.max_qubits:
            raise ValueError("Selected solver exceeds the qubit budget.")
        if solver.estimated_worker_seconds is not None and solver.estimated_worker_seconds > self.worker_seconds:
            raise ValueError("Selected solver exceeds the worker time budget.")
        return self


def _list_section(lines: list[str], heading: str, items: list[str]) -> None:
    if items:
        lines.extend([heading, "", *(f"- {item}" for item in items), ""])


def render_markdown(result: PlanningResult) -> str:
    plan = result.plan
    lines = [
        f"# Plan for {result.problem_title}", "", plan.summary, "",
        f"**Objective** {plan.objective}", "",
        f"**Selected solver** {plan.selected_solver}", "",
        f"**Budget** {result.max_qubits} qubits and {result.worker_seconds} seconds per worker", "",
        f"**Planning model** `{result.model}` ({result.attempts} attempts)", "",
    ]
    _list_section(lines, "## Constraints", plan.constraints)
    _list_section(lines, "## Assumptions", plan.assumptions)
    lines.extend(["## Solver examples", ""])
    for solver in plan.solver_examples:
        qubits = solver.qubits
        lines.extend([
            f"### {solver.name}", "",
            f"**Method** {solver.method}", "",
            f"**Encoding** {solver.encoding}", "",
            f"**Qubits** {qubits.total} total ({qubits.decision_qubits} decision, {qubits.auxiliary_qubits} auxiliary)",
            "",
        ])
        if solver.estimated_worker_seconds is not None:
            lines.extend([f"**Estimated worker time** {solver.estimated_worker_seconds} seconds", ""])
        _list_section(lines, "#### Classical steps", solver.classical_steps)
        _list_section(lines, "#### Quantum steps", solver.quantum_steps)
        _list_section(lines, "#### Build steps", solver.build_steps)
        _list_section(lines, "#### Verification", solver.verification)
        lines.extend([f"**Reference method** {solver.reference_method}", ""])

    lines.extend(["## User controls", ""])
    for control in plan.controls:
        if control.allowed_values is not None:
            choices = ", ".join(json.dumps(value, ensure_ascii=False) for value in control.allowed_values)
        elif control.type == "boolean":
            choices = "true or false"
        else:
            choices = f"{control.minimum} to {control.maximum}"
            if control.step is not None:
                choices += f" in steps of {control.step}"
        default = json.dumps(control.default, ensure_ascii=False)
        unit = f" ({control.unit})" if control.unit else ""
        group = f" · {control.group}" if control.group else ""
        lines.append(
            f"- **{control.label}**{unit}{group} (`{control.key}`) {choices}, default {default}, "
            f"`{control.rerun_mode}` on change."
        )
    if not plan.controls:
        lines.append("This plan has a fixed set of inputs.")
    lines.extend(["", "## Research", "", f"**Status** {result.research.status}", "", result.research.summary, ""])
    for source in result.research.sources:
        lines.append(f"- [{source.title or source.url}]({source.url})")
    if result.review.reason:
        lines.extend(["", "## Review", "", result.review.reason])
    return "\n".join(lines).rstrip() + "\n"


def save_plan(workspace: Workspace, result: PlanningResult) -> None:
    """Publish the readable and machine-readable forms of an approved plan."""
    json_path = workspace.resolve(PLAN_JSON)
    markdown_path = workspace.resolve(PLAN_MD)
    previous_markdown = markdown_path.read_bytes() if markdown_path.exists() else None
    with TemporaryDirectory(dir=workspace.path) as temporary_dir:
        staging = Path(temporary_dir)
        staged_json = staging / PLAN_JSON
        staged_md = staging / PLAN_MD
        staged_json.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        staged_md.write_text(render_markdown(result), encoding="utf-8")
        staged_md.replace(markdown_path)
        try:
            staged_json.replace(json_path)
        except OSError:
            if previous_markdown is None:
                markdown_path.unlink(missing_ok=True)
            else:
                staged_md.write_bytes(previous_markdown)
                staged_md.replace(markdown_path)
            raise


def load_plan(workspace: Workspace) -> PlanningResult:
    """Read the approved plan for downstream phases."""
    return PlanningResult.model_validate_json(workspace.resolve(PLAN_JSON).read_text(encoding="utf-8"))
