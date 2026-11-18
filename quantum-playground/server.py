"""Serve recorded EV results, the classical reference, and live handoffs on localhost."""

import json
import math
import os
import sys
from collections import OrderedDict
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from ev_live import LOCAL_PLAN_IDS, SolverUnavailable, SolverFailed, local_solver_available, local_handoff
from ev_recorded import LIVE_PROMPT, RECORDED_CONTROLS, RECORDED_PROMPT, REPO_ROOT, recorded_handoff
from ev_reference import PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, UnverifiedResult, applied_values, normalize_handoff
from pipeline_live import pipeline_handoff, planner_configured
from workspace_runs import (
    SavedResultUnavailable, SavedRunNotFound, SavedRunUnavailable, list_saved_runs, open_saved_run,
    saved_artifact, saved_run,
)

ROOT = Path(__file__).resolve().parent
VIEWS = {}
VIEW_BACKENDS = {}
SOLVER_RESULTS = OrderedDict()
UPLOADED_FRAME = None
VIEW_LOCK = Lock()
BACKEND_TIMEOUT_SECONDS = 300
MAX_CACHED_SOLVER_RESULTS = 100
PIPELINE_RUNS = {}
PIPELINE_LOCK = Lock()
MAX_ACTIVE_PIPELINE_RUNS = 2
MAX_PIPELINE_RUNS = 32
ACTIVE_PIPELINE_STATUSES = {"queued", "running"}
PIPELINE_STAGES = ("init", "plan", "build", "test", "verify")
SAMPLE_PROMPTS = (
    {"id": "ev-charge-range", "label": "EV dispatch · charge range", "prompt": LIVE_PROMPT},
    {"id": "ev-charge-options", "label": "EV dispatch · two charge settings", "prompt": RECORDED_PROMPT},
    {
        "id": "ev-electricity-price",
        "label": "EV dispatch · electricity price range",
        "prompt": RECORDED_PROMPT + "\nLet me vary the electricity price from $0.10 to $0.50 per kWh in $0.10 steps.",
    },
) + tuple(
    {
        "id": sample_id,
        "label": label,
        "prompt": (REPO_ROOT / "example-user-prompts" / f"{sample_id}.md").read_text(encoding="utf-8"),
    }
    for sample_id, label in (
        ("ev-charge-and-energy-prices", "EV dispatch · charge and energy prices"),
        ("ev-waiting-and-late-delivery", "EV dispatch · waiting and late fees"),
        ("ev-battery-and-charger-limits", "EV dispatch · battery and charger limits"),
        ("ev-fleet-dispatch-full-scenario", "EV dispatch · 4 vans, 17 adjustable settings"),
        ("ev-six-van-six-controls", "EV dispatch · 6 vans, 6 adjustable settings"),
    )
)


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ViewBackend:
    source: tuple[str, str]
    # Cache hits get a new display attempt without creating a backend attempt.
    last_attempt_id: str
    revision: str | None = ""


def backend_url():
    return os.environ.get("QUANTUM_BACKEND_URL", "").strip().rstrip("/")


def native_pipeline_configured():
    return not backend_url() and planner_configured()


def request_handoff(base, payload):
    try:
        request = Request(
            base + "/api/run",
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urlopen(request, timeout=BACKEND_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                body = json.load(exc)
                message = body.get("error") if isinstance(body, dict) else None
            except (ValueError, OSError):
                message = None
        raise RequestError(
            exc.code if 400 <= exc.code < 500 else 502,
            message if isinstance(message, str) and message else f"Backend returned HTTP {exc.code}.",
        ) from exc
    except TimeoutError as exc:
        raise RequestError(504, "Timed out waiting for the verified backend result.") from exc
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise RequestError(504, "Timed out waiting for the verified backend result.") from exc
        raise RequestError(502, "Could not reach the configured backend.") from exc
    except (ValueError, OSError) as exc:
        raise RequestError(502, "Could not read a JSON handoff from the backend.") from exc


def view_state(view_id):
    if not isinstance(view_id, str):
        raise ValueError("Expected a view ID.")
    with VIEW_LOCK:
        view = VIEWS.get(view_id)
        backend = VIEW_BACKENDS.get(view_id)
    if view is None:
        raise RequestError(404, "This result is no longer available. Start a new dispatch.")
    return view, backend


def read_view(view_id):
    return view_state(view_id)[0]


def solver_result_key(source, view, values, revision=""):
    return source, revision, view["run_id"], view["plan_id"], view["problem"]["original_prompt"], tuple(sorted(values.items()))


def native_cache_revision(view):
    """Identify the trusted solver inputs and current review policy, or skip reuse."""
    try:
        from backend.build import DEFAULT_MODEL
        from backend.build_review import build_fingerprint, solver_fingerprint
        from backend.model_provider import ModelError, model_name, model_provider
        from backend.prompt_loader import PROMPTS_FILE
        from backend.semantics import semantics_fingerprint
        from backend.workspace import WorkspaceError, fingerprint, open_workspace
    except ImportError:
        return None
    try:
        workspace = open_workspace(view["plan_id"])
        approval = json.loads(workspace.resolve("handoff.json").read_bytes())
        if approval.get("decision") != "GO" or approval.get("workspace_id") != workspace.id:
            return None
        review_models = {
            stage: model_name(DEFAULT_MODEL, stage=stage)
            for stage in ("plan_review", "build_review", "semantic_review", "final_review")
        }
        # Match the worker identity used by Test without retaining credentials in
        # cache metadata. Switching simulators or quantum accounts needs fresh evidence.
        runtime_identity = fingerprint(
            sys.executable, sys.version,
            os.getenv("QR_BACKEND"), os.getenv("QR_ACCOUNT"), os.getenv("QR_KEY"),
        )
        return fingerprint(
            solver_fingerprint(workspace), build_fingerprint(workspace), semantics_fingerprint(),
            model_provider(), review_models, runtime_identity, PROMPTS_FILE.read_text(encoding="utf-8"),
        )
    except (OSError, ValueError, KeyError, WorkspaceError, ModelError):
        # Older or unavailable workspaces can still be recalculated; they simply
        # cannot bypass verification using an unidentifiable cached result.
        return None


def read_prompt(data):
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 10000:
        raise ValueError("Enter a prompt of 1–10,000 characters.")
    return prompt


def register_view(result, previous=None, backend=None):
    result["view_id"] = str(uuid4())
    try:
        json.dumps(result, allow_nan=False)
    except ValueError as exc:
        raise InvalidHandoff("The handoff includes non-finite JSON values.") from exc
    with VIEW_LOCK:
        if previous is not None and any(
            all(view[key] == result[key] for key in ("run_id", "plan_id", "attempt_id"))
            for view in VIEWS.values()
        ):
            raise InvalidHandoff("The backend returned an attempt that was already displayed.")
        if len(VIEWS) >= 100:
            oldest = next(iter(VIEWS))
            VIEWS.pop(oldest)
            VIEW_BACKENDS.pop(oldest, None)
        VIEWS[result["view_id"]] = result
        if backend is not None:
            VIEW_BACKENDS[result["view_id"]] = backend
            if not result.get("cached") and backend.revision is not None:
                key = solver_result_key(backend.source, result, result["effective_values"], backend.revision)
                SOLVER_RESULTS[key] = result
                SOLVER_RESULTS.move_to_end(key)
                if len(SOLVER_RESULTS) > MAX_CACHED_SOLVER_RESULTS:
                    SOLVER_RESULTS.popitem(last=False)
    return result


def workspace_response(operation, *args):
    try:
        return operation(*args)
    except SavedRunNotFound as exc:
        raise RequestError(404, str(exc)) from exc
    except SavedResultUnavailable as exc:
        raise RequestError(422, str(exc)) from exc
    except SavedRunUnavailable as exc:
        raise RequestError(409, str(exc)) from exc


def reopen_workspace(run_id):
    result = workspace_response(open_saved_run, run_id)
    result["workspace_origin"] = "local"
    return register_view(result, backend=ViewBackend(("native", ""), result["attempt_id"], native_cache_revision(result)))


def live_result(prompt, values, previous, previous_backend, *, on_stage=None, on_progress=None):
    local_workspace = previous is not None and previous.get("workspace_origin") == "local"
    attempt_id = str(uuid4())
    base = "" if local_workspace else backend_url()
    if local_workspace and not planner_configured():
        raise RequestError(503, "Configure the native planning pipeline to recalculate this saved run.")
    use_pipeline = planner_configured() if previous is None else (
        local_workspace or previous["plan_id"] not in LOCAL_PLAN_IDS
    )
    if on_stage is not None and (base or not use_pipeline):
        raise RequestError(503, "Configure the native planning pipeline to run a problem with progress.")
    source = ("external", base) if base else ("native" if use_pipeline else "local", "")
    revision = native_cache_revision(previous) if source[0] == "native" and previous is not None else ""
    if previous is not None:
        if previous_backend is None or previous_backend.source != source:
            raise RequestError(409, "The solver backend changed. Start a new dispatch.")
        key = solver_result_key(source, previous, values, revision)
        with VIEW_LOCK:
            cached = SOLVER_RESULTS.get(key) if revision is not None else None
            if cached is not None:
                SOLVER_RESULTS.move_to_end(key)
        if cached is not None:
            return {**cached, "attempt_id": attempt_id, "cached": True}, ViewBackend(
                source, previous_backend.last_attempt_id, revision,
            )

    if base:
        payload = {"schema_version": 1, "attempt_id": attempt_id, "values": values}
        if previous is None:
            payload["prompt"] = prompt
        else:
            payload.update(
                run_id=previous["run_id"], plan_id=previous["plan_id"],
                previous_attempt_id=previous_backend.last_attempt_id,
            )
        handoff = request_handoff(base, payload)
    elif on_stage is not None:
        handoff = pipeline_handoff(prompt, values, previous, attempt_id, on_stage=on_stage, on_progress=on_progress)
    elif use_pipeline:
        handoff = pipeline_handoff(prompt, values, previous, attempt_id)
    else:
        handoff = local_handoff(prompt, values, previous, attempt_id)
    result = normalize_handoff(
        handoff, "live", prompt, previous,
        expected_values=values if previous is not None else None, expected_attempt_id=attempt_id,
    )
    if local_workspace:
        result["workspace_origin"] = "local"
    if source[0] == "native":
        current = native_cache_revision(result)
        revision = current if previous is None or current == revision else None
    return result, ViewBackend(source, attempt_id, revision)


def run_plan(data, *, on_stage=None, on_progress=None):
    mode = data.get("mode", "recorded")
    if mode not in ("recorded", "reference", "live"):
        raise ValueError("Unknown run mode.")
    overrides = data.get("variables", {})
    if not isinstance(overrides, dict):
        raise ValueError("Control values must be a JSON object.")
    previous = None
    previous_backend = None
    values = {}
    if "view_id" in data:
        previous, previous_backend = view_state(data["view_id"])
        if mode != previous["mode"] or "prompt" in data:
            raise ValueError("Use the displayed plan and its controls for recalculation.")
        prompt = previous["problem"]["original_prompt"]
        values = applied_values(previous["variables"], previous["effective_values"], overrides)
    else:
        prompt = read_prompt(data)
        if overrides:
            raise ValueError("Start a plan before changing its controls.")
        if (mode == "recorded" and prompt != RECORDED_PROMPT) or (mode == "reference" and prompt != PROMPT):
            raise ValueError("Load the EV example prompt to replay the example.")

    view_backend = None
    if mode == "live":
        result, view_backend = live_result(
            prompt, values, previous, previous_backend, on_stage=on_stage, on_progress=on_progress,
        )
    else:
        handoff = recorded_handoff(values, previous) if mode == "recorded" else reference_handoff(values, previous)
        result = normalize_handoff(handoff, mode, prompt, previous,
                                   expected_values=values if previous is not None else None)
    if on_progress is not None:
        on_progress("Publish the verified schedule", status="running")
    view = register_view(result, previous, view_backend)
    if on_progress is not None:
        on_progress("Publish the verified schedule", status="completed")
    return view


def pipeline_run_snapshot(run):
    snapshot = {key: run[key] for key in ("id", "status", "stage", "attempt")}
    snapshot["history"] = [
        {**visit, "steps": [dict(step) for step in visit["steps"]]}
        for visit in run["history"]
    ]
    if run["status"] == "completed":
        snapshot["result"] = run["result"]
    elif run["status"] == "failed":
        snapshot["error"] = run["error"]
    return snapshot


def read_pipeline_run(run_id):
    with PIPELINE_LOCK:
        run = PIPELINE_RUNS.get(run_id)
        if run is None:
            raise RequestError(404, "This pipeline run is no longer available. Start a new run.")
        return pipeline_run_snapshot(run)


def complete_pipeline_run(run):
    attempts = {}
    history = run["history"]

    def finish_visit(status):
        if not history:
            return
        visit = history[-1]
        visit["status"] = status
        for step in visit["steps"]:
            if step["status"] == "running":
                step["status"] = "completed" if status == "completed" else "failed"

    def update_stage(stage):
        with PIPELINE_LOCK:
            if history:
                advancing = PIPELINE_STAGES.index(stage) > PIPELINE_STAGES.index(history[-1]["stage"])
                finish_visit("completed" if advancing else "revising")
            attempts[stage] = attempts.get(stage, 0) + 1
            run.update(stage=stage, attempt=attempts[stage])
            history.append({
                "id": len(history) + 1, "stage": stage, "attempt": attempts[stage],
                "status": "running", "steps": [],
            })

    protected = sorted(
        {os.getenv(key, "").strip() for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "QR_KEY", "QR_ACCOUNT")} - {""},
        key=len, reverse=True,
    )

    def update_progress(label, *, status="info", detail=None):
        if detail:
            for value in protected:
                detail = detail.replace(value, "[redacted]")
            if len(detail) > 2400:
                detail = detail[:2399] + "…"
        with PIPELINE_LOCK:
            steps = history[-1]["steps"]
            step = None
            if status in ("completed", "failed"):
                step = next((step for step in reversed(steps)
                             if step["label"] == label and step["status"] == "running"), None)
            if step is None:
                step = {"id": len(steps) + 1, "label": label}
                steps.append(step)
            step["status"] = status
            if detail:
                step["detail"] = detail

    with PIPELINE_LOCK:
        run["status"] = "running"
    try:
        result = run_plan(
            {"mode": "live", "prompt": run["prompt"]}, on_stage=update_stage, on_progress=update_progress,
        )
    except (RequestError, SolverUnavailable, InvalidHandoff, SolverFailed, UnverifiedResult) as exc:
        outcome = {"status": "failed", "error": str(exc)}
    except TimeoutError:
        outcome = {"status": "failed", "error": "The pipeline timed out. Try running the problem again."}
    except Exception as exc:
        print(f"Pipeline run {run['id']} failed ({type(exc).__name__}).", flush=True)
        outcome = {"status": "failed", "error": "The pipeline stopped unexpectedly. Check the server terminal."}
    else:
        # Publish a stable copy even if feedback is later added to the live view.
        outcome = {"status": "completed", "result": dict(result)}
    with PIPELINE_LOCK:
        finish_visit(outcome["status"])
        run.update(outcome)


def start_pipeline_run(prompt):
    if not native_pipeline_configured():
        raise RequestError(503, "Configure the native planning pipeline to run a problem with progress.")
    with PIPELINE_LOCK:
        active = [run for run in PIPELINE_RUNS.values() if run["status"] in ACTIVE_PIPELINE_STATUSES]
        duplicate = next((run for run in active if run["prompt"] == prompt), None)
        if duplicate is not None:
            return pipeline_run_snapshot(duplicate)
        if len(active) >= MAX_ACTIVE_PIPELINE_RUNS:
            raise RequestError(429, "The pipeline is busy. Wait for a run to finish and try again.")
        if len(PIPELINE_RUNS) >= MAX_PIPELINE_RUNS:
            finished = next(run_id for run_id, run in PIPELINE_RUNS.items()
                            if run["status"] not in ACTIVE_PIPELINE_STATUSES)
            del PIPELINE_RUNS[finished]
        run_id = str(uuid4())
        run = {"id": run_id, "status": "queued", "stage": None, "attempt": 0, "prompt": prompt, "history": []}
        PIPELINE_RUNS[run_id] = run
        snapshot = pipeline_run_snapshot(run)
        try:
            Thread(target=complete_pipeline_run, args=(run,), daemon=True).start()
        except RuntimeError as exc:
            del PIPELINE_RUNS[run_id]
            raise RequestError(503, "Could not start a pipeline worker. Try again.") from exc
    return snapshot


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def send_json(self, status, value):
        data = json.dumps(value, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def require_local_workspace_host(self):
        try:
            host = urlparse("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            host = None
        if host not in {"127.0.0.1", "::1", "localhost", "localhost."}:
            raise RequestError(403, "Open Fleet Lab on localhost to access saved runs.")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/pygame-frame":
            return self.send_frame(parse_qs(url.query))
        if url.path == "/api/frame":
            with VIEW_LOCK:
                frame = UPLOADED_FRAME
            if frame is None:
                return self.send_json(404, {"error": "No Pygame frame received yet."})
            return self.send_png(frame)
        if url.path == "/api/example":
            return self.send_json(200, {
                "prompt": RECORDED_PROMPT, "live_prompt": LIVE_PROMPT, "variables": RECORDED_CONTROLS,
                "samples": SAMPLE_PROMPTS,
            })
        if url.path == "/api/config":
            external = bool(backend_url())
            pipeline = native_pipeline_configured()
            local = not external and not pipeline and local_solver_available()
            return self.send_json(200, {
                "backend_configured": external or pipeline or local,
                "pipeline_backend_configured": pipeline,
                "local_backend_configured": local,
                "saved_run_recalculation_configured": planner_configured(),
            })
        if url.path.startswith("/api/pipeline-runs/"):
            try:
                snapshot = read_pipeline_run(url.path.removeprefix("/api/pipeline-runs/"))
            except RequestError as exc:
                return self.send_json(exc.status, {"error": str(exc)})
            return self.send_json(200, snapshot)
        if url.path == "/api/workspaces" or url.path.startswith("/api/workspaces/"):
            try:
                return self.send_workspace(url.path)
            except RequestError as exc:
                return self.send_json(exc.status, {"error": str(exc)})
        return super().do_GET()

    def send_workspace(self, path):
        self.require_local_workspace_host()
        if path == "/api/workspaces":
            return self.send_json(200, {"workspaces": workspace_response(list_saved_runs)})
        segments = path.removeprefix("/api/workspaces/").split("/")
        if len(segments) == 1:
            return self.send_json(200, workspace_response(saved_run, segments[0]))
        if len(segments) == 3 and segments[1] == "artifacts":
            filename, data = workspace_response(saved_artifact, segments[0], segments[2])
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        raise RequestError(404, "Unknown saved workspace endpoint.")

    def send_frame(self, params):
        from pygame_renderer import PygameUnavailable, render_png, scene_layout

        try:
            run = read_view(params.get("view_id", [""])[0])
            simulation = run["simulation"]
            minute = float(params.get("minute", ["0"])[0])
            if not math.isfinite(minute) or not 0 <= minute <= simulation["duration_min"]:
                raise ValueError("Invalid replay time.")
            speed = float(params.get("speed", ["4"])[0])
            if speed not in (1, 4, 12):
                raise ValueError("Invalid playback speed.")
            resolution = int(params.get("resolution", ["2"])[0])
            if resolution not in (1, 2, 3):
                raise ValueError("Invalid frame resolution.")
            frame = render_png(
                simulation, minute, params.get("selected", [None])[0],
                params.get("playing", ["false"])[0] == "true", speed, resolution,
            )
            scene = scene_layout(simulation, minute)
            scene.update(pixel_width=scene["width"] * resolution, pixel_height=scene["height"] * resolution)
        except RequestError as exc:
            return self.send_json(exc.status, {"error": str(exc)})
        except PygameUnavailable as exc:
            return self.send_json(503, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            print(f"Pygame rendering failed: {exc}", flush=True)
            return self.send_json(500, {"error": "Pygame rendering failed. Check the server terminal."})

        return self.send_png(frame, scene)

    def send_png(self, frame, scene=None):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-store")
        if scene is not None:
            self.send_header("X-Pygame-Scene", json.dumps(scene, separators=(",", ":")))
        self.end_headers()
        self.wfile.write(frame)

    def do_POST(self):
        global UPLOADED_FRAME
        try:
            workspace_path = urlparse(self.path).path
            if workspace_path.startswith("/api/workspaces/"):
                self.require_local_workspace_host()
                workspace_segments = workspace_path.removeprefix("/api/workspaces/").split("/")
                if len(workspace_segments) != 2 or workspace_segments[1] != "open":
                    raise RequestError(404, "Unknown saved workspace endpoint.")
                if self.headers.get_content_type() != "application/json":
                    raise RequestError(415, "Send the request as application/json.")
                workspace_id = workspace_segments[0]
            else:
                workspace_id = None
            if self.path == "/api/pipeline-runs" and self.headers.get_content_type() != "application/json":
                raise RequestError(415, "Send the problem as application/json.")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 5_000_000:
                raise ValueError("Invalid request size.")
            raw = self.rfile.read(size)
            if self.path == "/api/frame":
                if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Expected a PNG frame.")
                with VIEW_LOCK:
                    UPLOADED_FRAME = raw
                return self.send_json(200, {"ok": True})
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/run":
                result = run_plan(data)
            elif self.path == "/api/pipeline-runs":
                return self.send_json(202, start_pipeline_run(read_prompt(data)))
            elif workspace_id is not None:
                result = reopen_workspace(workspace_id)
            elif self.path == "/api/feedback":
                view = read_view(data.get("view_id"))
                feedback = str(data.get("feedback", ""))[:2000]
                with VIEW_LOCK:
                    view.setdefault("feedback", []).append(feedback)
                result = {"ok": True}
            else:
                return self.send_json(404, {"error": "Unknown endpoint."})
        except RequestError as exc:
            return self.send_json(exc.status, {"error": str(exc)})
        except SolverUnavailable as exc:
            return self.send_json(503, {"error": str(exc)})
        except TimeoutError as exc:
            return self.send_json(504, {"error": str(exc)})
        except (InvalidHandoff, SolverFailed) as exc:
            return self.send_json(502, {"error": str(exc)})
        except UnverifiedResult as exc:
            return self.send_json(422, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        self.send_json(200, result)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    port = int(os.environ.get("PORT", 8000))
    print(f"Fleet Lab: http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
