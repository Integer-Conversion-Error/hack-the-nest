"""Bridge the native agent pipeline to the verified fleet visualization."""

import json
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
from uuid import uuid4

from circuit_images import captured_circuit
from ev_live import SolverFailed, SolverUnavailable
from backend.workspace import WorkspaceError
from fleet_handoff import InvalidHandoff, UnverifiedResult, applied_values, is_number, is_text, normalize_controls, normalize_handoff, require
from scenario import normalize_scenario, scenario_context, scenario_digest, scenario_prompt, validate_scenario_result


def load_scenario(workspace):
    """Load a pipeline-owned source, rejecting missing or changed builder input."""
    try:
        digest = workspace.meta.get("scenario_digest")
        path = workspace.resolve("scenario.json")
        if digest is None:
            require(not path.exists(), "The scenario has no approved source identity.")
            return None
        source = normalize_scenario(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, WorkspaceError):
        require(False, "The original scenario is unavailable. Start a new scenario run.")
    require(scenario_digest(source) == digest and scenario_prompt(source) == workspace.prompt,
            "The original scenario changed. Start a new scenario run.")
    return source


def planner_configured():
    from backend.model_provider import model_configured

    return model_configured()


@contextmanager
def planner_session(workspace=None):
    try:
        from backend.model_provider import ModelConfigurationError, ModelError, model_session
    except ImportError:
        raise SolverUnavailable("Install the repository requirements to enable the planning backend.") from None
    try:
        with model_session(workspace=workspace) as client:
            yield client
    except ModelConfigurationError as exc:
        raise SolverUnavailable(str(exc)) from None
    except ModelError as exc:
        raise SolverFailed(str(exc)) from None


def project_result(artifact, output, prompt, identity, *, workspace=None, saved_image=False, source_scenario=None):
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
    classical_simulation = None
    if isinstance(reference, dict):
        reference_value = reference.get("objective_value", reference.get("value"))
        if is_number(reference_value):
            evaluation.update(reference_value=reference_value, gap=objective["value"] - reference_value)
            method = reference.get("method")
            optimality_proven = reference.get("optimality_proven")
            if is_text(method) and reference.get("feasible") is True and isinstance(optimality_proven, bool):
                classical = {
                    "method": method.strip(),
                    "objective_value": reference_value,
                    "feasible": True,
                    "optimality_proven": optimality_proven,
                }
                selected_options = reference.get("selected_options")
                if isinstance(selected_options, list) and all(map(is_text, selected_options)):
                    classical["selected_options"] = selected_options
                evaluation["classical"] = classical
                classical_simulation = reference.get("simulation")
    quantum_details = {key: value for key, value in quantum.items() if key != "counts"}
    quantum_details.update(actual_qubits=quantum.get("qubits"), binary_variables=quantum.get("decision_qubits"))
    circuits = output.get("circuits", [])
    if workspace is not None:
        captured = captured_circuit(workspace, output, saved=saved_image)
        if captured is not None and isinstance(circuits, list):
            circuits = [*circuits, captured]
    source = load_scenario(workspace) if workspace is not None else source_scenario
    context = None
    if source is not None:
        try:
            context = scenario_context(source, normalize_controls(artifact["controls"]), output.get("controls", {}))
            validate_scenario_result(context["effective_scenario"], output)
        except ValueError as exc:
            raise InvalidHandoff(str(exc)) from None
    return {
        "schema_version": 2 if "depots" in simulation else 1,
        **({"scenario_context": context} if context is not None else {}),
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
            **({"classical_simulation": classical_simulation} if isinstance(classical_simulation, dict) else {}),
            "evaluation": evaluation,
            "quantum": quantum_details,
            "circuits": circuits,
            **({"assumptions": ["Route search uses bounded candidates. An exact classical comparison proves optimality over those candidates."]}
               if context is not None else {}),
        },
    }


def pipeline_handoff(
    prompt, values, previous, attempt_id, *,
    on_stage: Callable[[str], None] | None = None, on_progress=None, scenario=None,
):
    """Plan a new dispatch, or run and verify fresh values in a copy of the approved workspace."""
    try:
        from pydantic import ValidationError

        from backend.build import BuildError
        from backend.build_review import solver_fingerprint
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
            initial_stage = True

            def stage_entered(stage):
                nonlocal initial_stage
                # Workspace creation and the first prompt parse are one Init attempt.
                if on_stage is not None and (not initial_stage or stage != "init"):
                    on_stage(stage)
                initial_stage = False

            if on_progress is not None:
                if on_stage is not None:
                    on_stage("init")
                on_progress("Prepare the dispatch workspace", status="running")
            with planner_session() as client:
                workspace = init_workspace(prompt, name="fleet", scene_type="ev_fleet")
                if scenario is not None:
                    source = normalize_scenario(scenario)
                    require(prompt == scenario_prompt(source), "The scenario does not match the submitted problem.")
                    workspace.resolve("scenario.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
                    metadata = workspace.meta
                    metadata["scenario_digest"] = scenario_digest(source)
                    workspace.resolve("meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
                if on_progress is not None:
                    on_progress("Prepare the dispatch workspace", status="completed")
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
                    handoff = project_result(draft, output, prompt, identity, source_scenario=load_scenario(workspace))
                    normalize_handoff(handoff, "live", prompt, expected_values=expected)

                artifact = run_pipeline(
                    workspace, client=client, on_stage=stage_entered if on_progress is not None else on_stage,
                    on_progress=on_progress, validate_results=validate_results,
                )
            if artifact["decision"] != "GO":
                raise UnverifiedResult("The pipeline did not approve the fleet plan.")
            return project_result(artifact, artifact["results"], prompt, identity, workspace=workspace, saved_image=True)

        workspace = open_workspace(previous["plan_id"])
        load_scenario(workspace)
        identity = {"run_id": previous["run_id"], "plan_id": workspace.id, "attempt_id": attempt_id}
        artifact = json.loads(workspace.resolve(HANDOFF_JSON).read_text(encoding="utf-8"))
        if (workspace.meta.get("scene_type") not in (None, "ev_fleet") or artifact.get("decision") != "GO"
                or artifact.get("workspace_id") != workspace.id):
            raise UnverifiedResult("The approved fleet plan is unavailable. Start a new dispatch.")
        with TemporaryDirectory(dir=workspace.path.parent, prefix=f"{workspace.id}-attempt-") as directory:
            attempt = Workspace(workspace.id, Path(directory))
            copytree(workspace.path, attempt.path, dirs_exist_ok=True,
                     ignore=ignore_patterns("__pycache__", "results.json", "test.json", HANDOFF_JSON))
            approved_inputs = solver_fingerprint(attempt)
            outcome = run_solver(attempt, values)
            if solver_fingerprint(attempt) != approved_inputs:
                raise UnverifiedResult("The solver inputs changed during recalculation. Start a new dispatch to verify them.")
            if outcome.timed_out:
                raise TimeoutError("The fleet solver timed out while recalculating the requested settings.")
            if not outcome.passed:
                raise UnverifiedResult("The fleet solver could not verify the requested settings.")
            handoff = project_result(artifact, outcome.results, prompt, identity, workspace=attempt)
            normalize_handoff(handoff, "live", prompt, previous, expected_values=values)
            with planner_session(workspace=workspace) as client:
                review = evaluate_semantics(attempt, [outcome], client=client)
            if solver_fingerprint(attempt) != approved_inputs:
                raise UnverifiedResult("The solver inputs changed during review. Recalculate the settings again.")
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
