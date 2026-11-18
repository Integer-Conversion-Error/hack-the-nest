"""Research, draft, and review a solver plan for a workspace created by Init.

Usage:
    python -m backend.planning <workspace-id>
    python -m backend.planning <workspace-id> --answer "..." --feedback "..."
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.model_provider import generate, model_name, model_provider, model_run
from backend.model_types import ModelError, Stage
from backend.progress import ProgressCallback, progress_step, report
from backend.plan import (
    PLAN_JSON,
    PLAN_MD,
    Plan,
    PlanDraft,
    PlanningResult,
    PlanReview,
    PlanRevision,
    Research,
    Source,
    load_plan,
    save_plan,
)
from backend.problem_statement import ENV_FILE, PROBLEM_JSON, ProblemStatement, load_problem_statement
from backend.prompt_loader import PromptError, load_prompt
from backend.structured_edit import apply_edits
from backend.workspace import Workspace, WorkspaceError, open_workspace

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_MAX_RETRIES = 10
_MAX_FINDINGS_CHARS = 2400
_MAX_SOURCE_TITLES = 8

_Response = TypeVar("_Response", bound=BaseModel)


class PlanningError(Exception):
    """Planning stopped before a solver plan could be approved."""


class ClarificationRequired(PlanningError):
    def __init__(self, questions: Sequence[str]) -> None:
        self.questions = tuple(questions)
        super().__init__("The user needs to clarify the problem.\n" + "\n".join(f"- {q}" for q in questions))


@dataclass(frozen=True)
class PlanningCheckpoint:
    plan: Plan
    research: Research
    research_scope: str


class ReinterpretationRequired(PlanningError):
    """The problem statement needs revision before another plan can be made."""

    def __init__(self, message: str, *, checkpoint: PlanningCheckpoint | None = None):
        super().__init__(message)
        self.checkpoint = checkpoint


class _InvalidRevision(Exception):
    """An edit could not be applied to the current plan."""


def _research(
    client, model: str, problem_json: str, context: str, *, on_progress: ProgressCallback | None = None,
) -> Research:
    response = generate(
        client, model, load_prompt("planning.research.system"),
        load_prompt("planning.research.user", problem=problem_json, context=context),
        stage="research", search=True, on_progress=on_progress,
    )
    sources = {
        citation.url: Source(title=citation.title or citation.url, url=citation.url)
        for citation in response.citations if citation.url.startswith(("https://", "http://"))
    }
    if response.text and sources:
        return Research(status="grounded", summary=response.text, sources=list(sources.values()))
    return Research(status="unavailable", summary="The model returned no research with web sources.", sources=[])


def _structured(
    client,
    model: str,
    schema: type[_Response],
    system: str,
    contents: str,
    *,
    stage: Stage = "plan",
    on_progress: ProgressCallback | None = None,
) -> _Response:
    response = generate(
        client,
        model,
        system,
        contents,
        stage=stage, schema=schema.model_json_schema(), on_progress=on_progress,
    )
    return schema.model_validate_json(response.text or "")


def _limit_issues(plan: Plan, max_qubits: int, worker_seconds: int) -> list[str]:
    issues = []
    for solver in plan.solver_examples:
        if solver.qubits.total > max_qubits:
            issues.append(f"{solver.name} needs {solver.qubits.total} qubits, above the {max_qubits}-qubit limit.")
        estimated_seconds = solver.estimated_worker_seconds
        if estimated_seconds is not None and estimated_seconds > worker_seconds:
            issues.append(f"{solver.name} estimates {estimated_seconds} seconds, above the {worker_seconds}-second worker limit.")
    return issues


def _compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value) -> str:
    return sha256(_compact(value).encode("utf-8")).hexdigest()


def _planning_limits(workspace: Workspace, problem: ProblemStatement) -> tuple[int, int]:
    limits = workspace.meta["limits"]
    max_qubits = limits["max_qubits"]
    if problem.quantum.qubit_budget is not None:
        max_qubits = min(max_qubits, problem.quantum.qubit_budget)
    return max_qubits, limits["run_timeout_seconds"]


def planning_answers(previous: Sequence[str], supplied: Sequence[str]) -> list[str]:
    """Keep saved clarifications when a caller resumes or adds new ones."""
    answers = list(supplied)
    saved = list(previous)
    return answers if answers[:len(saved)] == saved else saved + answers


def _review_policy(workspace: Workspace, limits: tuple[int, int]) -> tuple[str, dict]:
    max_qubits, worker_seconds = limits
    system = load_prompt("planning.review.system", max_qubits=max_qubits, worker_seconds=worker_seconds)
    context = {}
    if workspace.meta.get("scene_type") == "ev_fleet":
        context["dashboard_requirements"] = load_prompt("planning.ev_fleet")
    if workspace.meta.get("scenario_digest"):
        context["scenario_digest"] = workspace.meta["scenario_digest"]
        context["scenario"] = json.loads(workspace.resolve("scenario.json").read_text(encoding="utf-8"))
    return system, context


def plan_fidelity_fingerprint(
    workspace: Workspace,
    *,
    model: str | None = None,
    answers: Sequence[str] = (),
    plan: Plan | None = None,
    research: Research | None = None,
) -> str:
    """Identify the exact problem, plan, and policy covered by a planning approval."""
    if plan is None:
        saved = load_plan(workspace)
        plan, research = saved.plan, saved.research
        answers = planning_answers(saved.answers, answers)
    problem = load_problem_statement(workspace)
    limits = max_qubits, worker_seconds = _planning_limits(workspace, problem)
    system, context = _review_policy(workspace, limits)
    user = load_prompt("planning.review.user", problem="", evidence="", context="", draft="")
    contract = load_prompt("build.contract", max_qubits=max_qubits, worker_seconds=worker_seconds)
    if context:
        contract += "\n\n" + load_prompt("build.ev_fleet")
    return _digest({
        "provider": model_provider(), "model": model_name(DEFAULT_MODEL, model, stage="plan_review"), "prompt": workspace.prompt,
        "problem": problem.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
        "context": context, "answers": list(answers), "schema": PlanReview.model_json_schema(),
        "system": system, "user": user, "contract": contract,
        "evidence": _evidence_brief(research) if research else None,
    })


def _research_scope(problem: ProblemStatement, limits: tuple[int, int], context: dict) -> str:
    fields = problem.model_dump(mode="json", include={
        "problem_class", "given_data", "decision_variables", "objective_sense", "objective_terms", "constraints", "quantum",
    })
    fields["quantum"].pop("method_reason", None)
    return _digest({"problem": fields, "limits": limits, "context": context})


def _evidence_brief(research: Research) -> str:
    findings = research.summary
    sources = []
    for index, source in enumerate(research.sources[:_MAX_SOURCE_TITLES], 1):
        source_id = f"S{index}"
        findings = findings.replace(source.url, source_id)
        title = source.title or ""
        sources.append(f"{source_id} {title[:120]}".rstrip() if "://" not in title else source_id)
    findings = re.sub(r"https?://[^\s)]+", "", findings)
    if len(findings) > _MAX_FINDINGS_CHARS:
        findings = findings[:_MAX_FINDINGS_CHARS].rsplit(None, 1)[0] + " …"
    return _compact({"status": research.status, "findings": findings, "sources": sources})


def _propose_plan(
    client, model: str, limits: tuple[int, int], prompt_values: dict, previous: Plan | None,
    *, on_progress: ProgressCallback | None = None,
) -> Plan:
    max_qubits, worker_seconds = limits
    if previous is None:
        draft = _structured(
            client, model, PlanDraft,
            load_prompt("planning.draft.system", max_qubits=max_qubits, worker_seconds=worker_seconds),
            load_prompt("planning.draft.user", **prompt_values), on_progress=on_progress,
        )
    else:
        draft = _structured(
            client, model, PlanRevision,
            load_prompt("planning.revision.system", max_qubits=max_qubits, worker_seconds=worker_seconds),
            load_prompt("planning.revision.user", **prompt_values, previous=previous.model_dump_json()), on_progress=on_progress,
        )
    if draft.status == "needs_clarification":
        raise ClarificationRequired(draft.questions)
    if draft.status == "unsupported":
        raise PlanningError(draft.reason or "The model could not propose a solver for this request.")
    if isinstance(draft, PlanRevision):
        try:
            return apply_edits(previous, draft.edits)
        except ValidationError:
            raise
        except ValueError as error:
            raise _InvalidRevision(str(error)) from error
    if draft.plan is None:
        raise PlanningError("The model did not include a plan in its ready response.")
    return draft.plan


@model_run
def run_planning(
    workspace: Workspace,
    feedback: str | None = None,
    *,
    answers: Sequence[str] = (),
    model: str | None = None,
    client=None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    previous_draft: PlanningCheckpoint | None = None,
    include_original_prompt: bool = False,
    on_progress: ProgressCallback | None = None,
) -> PlanningResult:
    """Save an approved plan, optionally reviewing the original prompt in a standalone run."""
    if not 0 <= max_retries <= DEFAULT_MAX_RETRIES:
        raise PlanningError(f"Use 0 through {DEFAULT_MAX_RETRIES} planning retries.")

    if not workspace.resolve(PROBLEM_JSON).is_file():
        raise PlanningError(f"Run Init first to create {PROBLEM_JSON} for workspace {workspace.id}.")
    problem = load_problem_statement(workspace)
    if not problem.is_optimization_problem:
        raise PlanningError(problem.rejection_reason or "Init rejected the problem.")

    limits = max_qubits, worker_seconds = _planning_limits(workspace, problem)
    if max_qubits <= 0 or worker_seconds <= 0:
        raise PlanningError("The qubit and worker-time limits must be positive.")

    research_model = model_name(DEFAULT_MODEL, model, stage="research")
    review_model = model_name(DEFAULT_MODEL, model, stage="plan_review")
    model = model_name(DEFAULT_MODEL, model, stage="plan")
    problem_json = problem.model_dump_json(exclude_none=True)
    review_policy, context = _review_policy(workspace, limits)
    saved = load_plan(workspace) if workspace.resolve(PLAN_JSON).is_file() else None
    answers = planning_answers(saved.answers if saved else (), answers)
    context["user_answers"] = answers
    scope = _research_scope(problem, limits, context)
    baseline = previous_draft or saved
    research = (
        baseline.research
        if baseline and baseline.research_scope == scope and baseline.research.status == "grounded" else None
    )
    if research is not None:
        report(on_progress, "Reuse grounded research", detail="The problem and research scope are unchanged.")
    previous = previous_draft.plan if previous_draft else None
    if saved and previous is None:
        changed = (
            feedback or answers != saved.answers or saved.problem_title != problem.title
            or saved.research_scope not in (None, scope) or (
                saved.fidelity_fingerprint is not None
                and saved.fidelity_fingerprint != plan_fidelity_fingerprint(workspace, model=review_model, answers=answers)
            )
        )
        if changed:
            previous = saved.plan

    issues = []
    failures = []
    rejected_plans = set()
    candidate = None
    refreshed_research = False
    prompt = workspace.prompt
    for attempt in range(1, max_retries + 2):
        try:
            request_context = {**context, "caller_feedback": feedback, "revision_issues": issues}
            if research is None:
                with progress_step(on_progress, "Research solver approaches") as step:
                    research = _research(client, research_model, problem_json, _compact(request_context), on_progress=on_progress)
                    step.completed(f"Found {len(research.sources)} research sources.")
            if include_original_prompt:
                request_context["original_request"] = prompt
            prompt_values = {
                "problem": problem_json,
                "evidence": _evidence_brief(research),
                "context": _compact(request_context),
            }
            if candidate is None:
                with progress_step(on_progress, f"Draft a solver plan (attempt {attempt})") as step:
                    proposed = _propose_plan(client, model, limits, prompt_values, previous, on_progress=on_progress)
                    step.completed(proposed.selected.name)
                failures = []
                signature = proposed.model_dump_json()
                if signature in rejected_plans:
                    raise PlanningError("The model repeated a rejected plan without progress. " + " ".join(issues))
                with progress_step(on_progress, f"Check plan resource limits (attempt {attempt})") as step:
                    limit_issues = _limit_issues(proposed, max_qubits, worker_seconds)
                    if limit_issues:
                        step.failed("\n".join(limit_issues))
                    else:
                        step.completed(f"Within the {max_qubits}-qubit and {worker_seconds}-second limits.")
                if limit_issues:
                    previous, issues = proposed, limit_issues
                    rejected_plans.add(signature)
                    continue
                candidate = proposed

            with progress_step(on_progress, f"Review the solver plan (attempt {attempt})") as step:
                review = _structured(
                    client, review_model, PlanReview, review_policy,
                    load_prompt("planning.review.user", **prompt_values, draft=candidate.model_dump_json()),
                    stage="plan_review", on_progress=on_progress,
                )
                if review.verdict == "approve":
                    step.completed("Plan approved.")
                else:
                    step.failed(review.reason or "\n".join(review.issues or review.questions))
            failures = []
            if review.verdict == "approve":
                if workspace.prompt != prompt or load_problem_statement(workspace) != problem:
                    raise PlanningError("Init inputs changed during planning. Run Plan again for the current problem.")
                result = PlanningResult(
                    problem_title=problem.title,
                    model=model,
                    plan=candidate,
                    research=research,
                    attempts=attempt,
                    max_qubits=max_qubits,
                    worker_seconds=worker_seconds,
                    review=review,
                    fidelity_fingerprint=plan_fidelity_fingerprint(
                        workspace, model=review_model, answers=answers, plan=candidate, research=research,
                    ),
                    research_scope=scope,
                    answers=answers,
                )
                with progress_step(on_progress, "Save the approved plan"):
                    save_plan(workspace, result)
                return result
            if review.verdict == "needs_clarification":
                raise ClarificationRequired(review.questions)
            if review.verdict == "needs_reinterpretation":
                raise ReinterpretationRequired(
                    review.reason or " ".join(review.issues),
                    checkpoint=PlanningCheckpoint(candidate, research, scope),
                )
            if review.verdict == "unsupported":
                raise PlanningError(review.reason or " ".join(review.issues))
            if review.refresh_research and not refreshed_research:
                research = None
                refreshed_research = True
                rejected_plans.clear()
            else:
                rejected_plans.add(candidate.model_dump_json())
            previous, candidate, issues = candidate, None, review.issues
        except ValidationError as error:
            details = error.errors(include_input=False, include_context=False, include_url=False)
            failures = [f"{'.'.join(map(str, item['loc'])) or 'response'}: {item['msg']}" for item in details[:5]]
            if candidate is None:
                issues = [*issues[:5], *failures]
        except _InvalidRevision as error:
            failures = [str(error)]
            issues = [*issues[:5], *failures]
        if attempt <= max_retries:
            report(on_progress, f"Retry planning (attempt {attempt + 1})", detail="\n".join(failures or issues))

    raise PlanningError(f"No plan was approved after {max_retries + 1} attempts. " + " ".join(failures or issues))


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description="Plan and review a solver for a workspace created by Init.")
    parser.add_argument("workspace_id")
    parser.add_argument("--answer", action="append", default=[], help="User clarification, repeat as needed")
    parser.add_argument("--feedback", help="Build or verification feedback for replanning")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    args = parser.parse_args()

    try:
        workspace = open_workspace(args.workspace_id)
        result = run_planning(
            workspace, args.feedback, answers=args.answer,
            max_retries=args.max_retries, include_original_prompt=True,
        )
    except ClarificationRequired as error:
        sys.exit(f"{error}\nRerun the planner with --answer for each clarification.")
    except ReinterpretationRequired as error:
        sys.exit(f"{error}\nRe-run backend.problem_statement with --feedback before retrying Plan.")
    except (OSError, PlanningError, ModelError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Planning failed: {error}")

    print(f"Selected solver: {result.plan.selected.name}")
    print(f"Approved after {result.attempts} attempt(s)")
    print(f"Wrote: {workspace.resolve(PLAN_MD)}")
    print(f"Wrote: {workspace.resolve(PLAN_JSON)}")


if __name__ == "__main__":
    main()
