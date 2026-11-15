"""Bridge the native agent pipeline to the verified fleet visualization."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
from uuid import uuid4

from ev_live import SolverFailed, SolverUnavailable
from fleet_handoff import UnverifiedResult, applied_values, is_number, normalize_controls, normalize_handoff, require

def planner_configured():
    return bool(os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip())


@contextmanager
def planner_session():
    try:
        from google import genai
        from google.genai.errors import APIError

        from backend.planning import gemini_transport_errors
    except ImportError:
        raise SolverUnavailable("Install the repository requirements to enable the planning backend.") from None
    try:
        client = genai.Client()
    except ValueError:
        raise SolverUnavailable("Configure GEMINI_API_KEY or GOOGLE_API_KEY to run the planner.") from None
    try:
        with client:
            yield client
    except APIError as exc:
        raise SolverFailed(f"The planning service returned HTTP {exc.code}. Check the Gemini key and model configuration.") from None
    except TimeoutError:
        raise
    except gemini_transport_errors():
        raise SolverFailed("Could not reach the planning service. Retry the dispatch.") from None


def project_result(artifact, output, prompt, identity):
    """Translate a native solver result using the approved plan and the solver's own replay."""
    require(isinstance(output, dict) and output.get("status") == "ok", "The fleet solver did not succeed.")
    solution = output.get("solution")
    verification = solution.get("verification") if isinstance(solution, dict) else None
    require(
        isinstance(verification, dict) and verification.get("feasible") is True
        and verification.get("violations") == [],
        "The fleet solver must independently verify the schedule and report any violations.",
    )
    objective = output.get("objective")
    require(isinstance(objective, dict) and objective.get("unit") == "USD"
            and is_number(objective.get("value")), "The fleet solver must report its objective in USD.")
    simulation = output.get("simulation")
    quantum = output.get("quantum")
    require(isinstance(simulation, dict) and isinstance(quantum, dict), "Missing fleet replay or circuit measurements.")
    vans = simulation.get("vans")
    require(isinstance(vans, list) and all(isinstance(van, dict) and isinstance(van.get("events"), list)
                                         for van in vans), "Missing timed van schedules.")
    evaluation = {
        "feasible": True,
        "objective_value": objective["value"],
        "costs": solution.get("cost_breakdown"),
        "delivered": sum(isinstance(event, dict) and event.get("type") == "deliver"
                         for van in vans for event in van["events"]),
    }
    reference = output.get("reference")
    if isinstance(reference, dict):
        reference_value = reference.get("objective_value", reference.get("value"))
        if is_number(reference_value):
            evaluation.update(reference_value=reference_value, gap=objective["value"] - reference_value)
    quantum_details = {key: value for key, value in quantum.items() if key != "counts"}
    quantum_details.update(actual_qubits=quantum.get("qubits"), binary_variables=quantum.get("decision_qubits"))
    return {
        "schema_version": 1,
        **identity,
        "scene_type": "ev_fleet",
        "problem": {
            "title": artifact["problem"]["title"],
            "statement": artifact["problem"]["summary"],
            "original_prompt": prompt,
        },
        "controls": artifact["controls"],
        "result": {
            **identity,
            "status": "verified",
            "provenance": "live",
            "effective_values": output.get("controls"),
            "simulation": {**simulation, "counts": quantum.get("counts")},
            "evaluation": evaluation,
            "quantum": quantum_details,
            "circuits": output.get("circuits", []),
        },
    }


def pipeline_handoff(prompt, values, previous, attempt_id):
    """Plan a new dispatch, or run and verify fresh values in a copy of the approved workspace."""
    try:
        from pydantic import ValidationError

        from backend.build import BuildError
        from backend.pipeline import HANDOFF_JSON, PipelineError, run_pipeline
        from backend.plan import load_plan
        from backend.planning import ClarificationRequired, PlanningError
        from backend.problem_statement import ProblemStatementError, load_problem_statement
        from backend.prompt_loader import PromptError
        from backend.runner import run_solver
        from backend.semantics import evaluate_semantics
        from backend.workspace import Workspace, WorkspaceError, init_workspace, open_workspace
    except ImportError:
        raise SolverUnavailable("Install the repository requirements to enable the planning backend.") from None

    try:
        if previous is None:
            with planner_session() as client:
                workspace = init_workspace(prompt, name="fleet", scene_type="ev_fleet")
                identity = {"run_id": str(uuid4()), "plan_id": workspace.id, "attempt_id": attempt_id}

                def validate_results(output, overrides):
                    planning = load_plan(workspace)
                    problem = load_problem_statement(workspace)
                    draft = {
                        "controls": [control.model_dump(mode="json") for control in planning.plan.controls],
                        "problem": {"title": problem.title, "summary": problem.summary},
                    }
                    controls = normalize_controls(draft["controls"])
                    defaults = {control["id"]: control["default"] for control in controls}
                    expected = applied_values(controls, defaults, overrides)
                    handoff = project_result(draft, output, prompt, identity)
                    normalize_handoff(handoff, "live", prompt, expected_values=expected)

                artifact = run_pipeline(workspace, client=client, validate_results=validate_results)
            if artifact["decision"] != "GO":
                raise UnverifiedResult("The pipeline did not approve the fleet plan.")
            return project_result(artifact, artifact["results"], prompt, identity)

        workspace = open_workspace(previous["plan_id"])
        identity = {"run_id": previous["run_id"], "plan_id": workspace.id, "attempt_id": attempt_id}
        artifact = json.loads(workspace.resolve(HANDOFF_JSON).read_text(encoding="utf-8"))
        if (workspace.meta.get("scene_type") != "ev_fleet" or artifact.get("decision") != "GO"
                or artifact.get("workspace_id") != workspace.id):
            raise UnverifiedResult("The approved fleet plan is unavailable. Start a new dispatch.")
        with TemporaryDirectory(dir=workspace.path.parent, prefix=f"{workspace.id}-attempt-") as directory:
            attempt = Workspace(workspace.id, Path(directory))
            copytree(workspace.path, attempt.path, dirs_exist_ok=True,
                     ignore=ignore_patterns("__pycache__", "results.json", "test.json", HANDOFF_JSON))
            outcome = run_solver(attempt, values)
            if outcome.timed_out:
                raise TimeoutError("The fleet solver timed out while recalculating the requested settings.")
            if not outcome.passed:
                raise UnverifiedResult("The fleet solver could not verify the requested settings.")
            handoff = project_result(artifact, outcome.results, prompt, identity)
            normalize_handoff(handoff, "live", prompt, previous, expected_values=values)
            with planner_session() as client:
                review = evaluate_semantics(attempt, [outcome], client=client)
            if not review.matches:
                raise UnverifiedResult("The recalculated schedule did not pass independent review.")
            return handoff
    except ClarificationRequired as exc:
        raise UnverifiedResult("Add these details to the original prompt and run again: " + " ".join(exc.questions)) from None
    except (PipelineError, BuildError, PlanningError, ProblemStatementError):
        raise UnverifiedResult("The planning pipeline could not approve the fleet dispatch. Check the prompt and try again.") from None
    except TimeoutError:
        raise
    except (OSError, ValidationError, WorkspaceError, PromptError, KeyError, TypeError, json.JSONDecodeError):
        raise SolverFailed("The fleet planning workspace could not produce a complete result.") from None
