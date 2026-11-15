"""Research, draft, and review a solver plan for a workspace created by Init.

Usage:
    python -m backend.planning <workspace-id>
    python -m backend.planning <workspace-id> --answer "..." --feedback "..."
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from contextlib import nullcontext
from importlib import import_module
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.plan import (
    PLAN_JSON,
    PLAN_MD,
    Plan,
    PlanDraft,
    PlanningResult,
    PlanReview,
    Research,
    Source,
    load_plan,
    save_plan,
)
from backend.problem_statement import ENV_FILE, PROBLEM_JSON, load_problem_statement
from backend.progress import ProgressCallback, progress_step, report
from backend.prompt_loader import PromptError, load_prompt
from backend.workspace import Workspace, WorkspaceError, open_workspace

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_MAX_RETRIES = 10

_Response = TypeVar("_Response", bound=BaseModel)


class PlanningError(Exception):
    """Planning stopped before a solver plan could be approved."""


class ClarificationRequired(PlanningError):
    def __init__(self, questions: Sequence[str]) -> None:
        self.questions = tuple(questions)
        super().__init__("The user needs to clarify the problem.\n" + "\n".join(f"- {q}" for q in questions))


class ReinterpretationRequired(PlanningError):
    """The problem statement needs revision before another plan can be made."""


class _RetryableRequestError(PlanningError):
    """A transport or service error can be retried within the planning budget."""


def gemini_transport_errors() -> tuple[type[Exception], ...]:
    errors: list[type[Exception]] = [TimeoutError]
    for module in ("httpx2", "httpx"):
        try:
            transport = import_module(module)
        except ImportError:
            continue
        errors.append(transport.RequestError)
    return tuple(errors)


def _generate(client, model: str, system: str, contents: str, **config):
    from google.genai import errors, types

    try:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
                **config,
            ),
        )
    except errors.APIError as error:
        if error.code in (408, 429, 500, 502, 503, 504):
            raise _RetryableRequestError(f"Gemini request failed temporarily (HTTP {error.code}).") from error
        raise PlanningError(f"Gemini request failed (HTTP {error.code}).") from error
    except gemini_transport_errors() as error:
        raise _RetryableRequestError("Could not complete the request to Gemini.") from error


def _research(client, model: str, problem_json: str, context: str) -> Research:
    from google.genai import types

    response = _generate(
        client,
        model,
        load_prompt("planning.research.system"),
        load_prompt("planning.research.user", problem=problem_json, context=context),
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )
    sources = {}
    candidates = response.candidates or []
    if candidates and candidates[0].grounding_metadata:
        for chunk in candidates[0].grounding_metadata.grounding_chunks or []:
            web = chunk.web
            if web and web.uri and web.uri.startswith(("https://", "http://")):
                sources[web.uri] = Source(title=web.title or web.uri, url=web.uri)

    if response.text and sources:
        return Research(status="grounded", summary=response.text, sources=list(sources.values()))
    return Research(status="unavailable", summary="Gemini returned no research with Google Search sources.", sources=[])


def _structured(
    client,
    model: str,
    schema: type[_Response],
    system: str,
    contents: str,
) -> _Response:
    response = _generate(
        client,
        model,
        system,
        contents,
        response_mime_type="application/json",
        response_json_schema=schema.model_json_schema(),
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


def run_planning(
    workspace: Workspace,
    feedback: str | None = None,
    *,
    answers: Sequence[str] = (),
    model: str | None = None,
    client=None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_progress: ProgressCallback | None = None,
) -> PlanningResult:
    """Read Init's problem statement and save the first approved solver plan."""
    if not 0 <= max_retries <= DEFAULT_MAX_RETRIES:
        raise PlanningError(f"Use 0 through {DEFAULT_MAX_RETRIES} planning retries.")

    if not workspace.resolve(PROBLEM_JSON).is_file():
        raise PlanningError(f"Run Init first to create {PROBLEM_JSON} for workspace {workspace.id}.")
    problem = load_problem_statement(workspace)
    if not problem.is_optimization_problem:
        raise PlanningError(problem.rejection_reason or "Init rejected the problem.")

    metadata = workspace.meta
    limits = metadata["limits"]
    max_qubits = limits["max_qubits"]
    if problem.quantum.qubit_budget is not None:
        max_qubits = min(max_qubits, problem.quantum.qubit_budget)
    worker_seconds = limits["run_timeout_seconds"]
    if max_qubits <= 0 or worker_seconds <= 0:
        raise PlanningError("The qubit and worker-time limits must be positive.")

    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    problem_json = problem.model_dump_json(indent=2)
    context = {"user_answers": list(answers), "caller_feedback": feedback}
    if metadata.get("scene_type") == "ev_fleet":
        context["dashboard_requirements"] = load_prompt("build.ev_fleet")
    if feedback and workspace.resolve(PLAN_JSON).is_file():
        context["previous_approved_plan"] = load_plan(workspace).plan.model_dump(mode="json")

    if client is None:
        from google import genai

        try:
            session = genai.Client()
        except ValueError as error:
            raise PlanningError("Configure GEMINI_API_KEY before running the planning loop.") from error
    else:
        session = nullcontext(client)

    issues = []
    rejected_plans = set()
    research = None
    prompt = workspace.prompt
    with session as gemini:
        for attempt in range(1, max_retries + 2):
            retry_delay = 0
            try:
                context_json = json.dumps(context, ensure_ascii=False)
                if research is None:
                    with progress_step(on_progress, "Research solver approaches") as step:
                        research = _research(gemini, model, problem_json, context_json)
                        if research.sources:
                            step.completed(f"Found {len(research.sources)} research sources.")
                        else:
                            step.completed("No grounded sources were found. Continuing with the problem statement.")
                prompt_values = {
                    "prompt": prompt,
                    "problem": problem_json,
                    "research": research.model_dump_json(indent=2),
                    "context": context_json,
                }
                with progress_step(on_progress, f"Draft a solver plan (attempt {attempt})") as step:
                    draft = _structured(
                        gemini, model, PlanDraft,
                        load_prompt("planning.draft.system", max_qubits=max_qubits, worker_seconds=worker_seconds),
                        load_prompt("planning.draft.user", **prompt_values),
                    )
                    if draft.status == "needs_clarification":
                        step.failed("The draft identified questions about the request.")
                        raise ClarificationRequired(draft.questions)
                    if draft.status == "unsupported":
                        step.failed(draft.reason or "The model could not propose a solver.")
                        raise PlanningError(draft.reason or "Gemini could not propose a solver for this request.")

                    plan = draft.plan
                    if plan is None:
                        raise PlanningError("Gemini did not include a plan in its ready response.")
                    signature = plan.model_dump_json()
                    if signature in rejected_plans:
                        step.failed("The planner repeated an already rejected plan.")
                        raise PlanningError("Gemini repeated a rejected plan without progress. " + " ".join(issues))
                    step.completed(plan.selected.name)

                context["previous_draft"] = plan.model_dump(mode="json")
                with progress_step(on_progress, f"Check plan resource limits (attempt {attempt})") as step:
                    issues = _limit_issues(plan, max_qubits, worker_seconds)
                    if issues:
                        step.failed("\n".join(issues))
                    else:
                        step.completed(f"Within the {max_qubits}-qubit and {worker_seconds}-second limits.")
                if not issues:
                    with progress_step(on_progress, f"Review the solver plan (attempt {attempt})") as step:
                        review = _structured(
                            gemini, model, PlanReview,
                            load_prompt("planning.review.system", max_qubits=max_qubits, worker_seconds=worker_seconds),
                            load_prompt("planning.review.user", **prompt_values, draft=signature),
                        )
                        if review.verdict == "approve":
                            step.completed("Plan approved.")
                        else:
                            review_detail = review.reason or "\n".join(review.issues or review.questions)
                            step.failed(review_detail or "The review did not approve this draft.")
                    if review.verdict == "approve":
                        with progress_step(on_progress, "Save the approved plan") as step:
                            if workspace.prompt != prompt or load_problem_statement(workspace) != problem:
                                step.failed("The prompt or problem statement changed during planning.")
                                raise PlanningError("Init inputs changed during planning. Run Plan again for the current problem.")
                            result = PlanningResult(
                                problem_title=problem.title,
                                model=model,
                                plan=plan,
                                research=research,
                                attempts=attempt,
                                max_qubits=max_qubits,
                                worker_seconds=worker_seconds,
                                review=review,
                            )
                            save_plan(workspace, result)
                            step.completed(f"{len(plan.controls)} adjustable settings.")
                        return result
                    if review.verdict == "needs_clarification":
                        raise ClarificationRequired(review.questions)
                    if review.verdict == "needs_reinterpretation":
                        raise ReinterpretationRequired(review.reason or " ".join(review.issues))
                    if review.verdict == "unsupported":
                        raise PlanningError(review.reason or " ".join(review.issues))
                    issues = review.issues
                rejected_plans.add(signature)
            except ValidationError as error:
                details = error.errors(include_input=False, include_context=False, include_url=False)
                issues = [f"{'.'.join(map(str, item['loc'])) or 'response'}: {item['msg']}" for item in details[:5]]
            except _RetryableRequestError as error:
                issues = [str(error)]
                retry_delay = 2 ** min(attempt - 1, 2)
            context["review_issues"] = issues
            if attempt <= max_retries:
                report(on_progress, f"Retry planning (attempt {attempt + 1})", detail="\n".join(issues))
                if retry_delay:
                    time.sleep(retry_delay)

    raise PlanningError(f"No plan was approved after {max_retries + 1} attempts. " + " ".join(issues))


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
            max_retries=args.max_retries,
        )
    except ClarificationRequired as error:
        sys.exit(f"{error}\nRerun the planner with --answer for each clarification.")
    except ReinterpretationRequired as error:
        sys.exit(f"{error}\nRe-run backend.problem_statement with --feedback before retrying Plan.")
    except (OSError, PlanningError, PromptError, ValidationError, WorkspaceError) as error:
        sys.exit(f"Planning failed: {error}")

    print(f"Selected solver: {result.plan.selected.name}")
    print(f"Approved after {result.attempts} attempt(s)")
    print(f"Wrote: {workspace.resolve(PLAN_MD)}")
    print(f"Wrote: {workspace.resolve(PLAN_JSON)}")


if __name__ == "__main__":
    main()
