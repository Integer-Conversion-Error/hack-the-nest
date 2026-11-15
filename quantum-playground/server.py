"""Serve the EV reference and verified backend handoffs on localhost."""

import json
import math
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from ev_reference import CONTROLS, PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, UnverifiedResult, applied_values, normalize_handoff

ROOT = Path(__file__).resolve().parent
VIEWS = {}
UPLOADED_FRAME = None
VIEW_LOCK = Lock()
BACKEND_TIMEOUT_SECONDS = 300


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def backend_url():
    return os.environ.get("QUANTUM_BACKEND_URL", "").strip().rstrip("/")


def request_handoff(payload):
    base = backend_url()
    if not base:
        raise RequestError(503, "Set QUANTUM_BACKEND_URL to use the live backend.")
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


def read_view(view_id):
    if not isinstance(view_id, str):
        raise ValueError("Expected a view ID.")
    with VIEW_LOCK:
        view = VIEWS.get(view_id)
    if view is None:
        raise RequestError(404, "This result is no longer available. Start a new dispatch.")
    return view


def run_plan(data):
    mode = data.get("mode", "reference")
    if mode not in ("reference", "live"):
        raise ValueError("Unknown run mode.")
    overrides = data.get("variables", {})
    if not isinstance(overrides, dict):
        raise ValueError("Control values must be a JSON object.")
    previous = None
    values = {}
    if "view_id" in data:
        previous = read_view(data["view_id"])
        if mode != previous["mode"] or "prompt" in data:
            raise ValueError("Use the displayed plan and its controls for recalculation.")
        prompt = previous["problem"]["original_prompt"]
        values = applied_values(previous["variables"], previous["effective_values"], overrides)
    else:
        prompt = data.get("prompt", "")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 10000:
            raise ValueError("Enter a prompt of 1–10,000 characters.")
        if overrides:
            raise ValueError("Start a plan before changing its controls.")
        if mode == "reference" and prompt != PROMPT:
            raise ValueError("Load the EV example prompt to run the reference.")

    if mode == "reference":
        handoff = reference_handoff(values, previous)
    else:
        payload = {"schema_version": 1, "values": values}
        if previous is None:
            payload["prompt"] = prompt
        else:
            payload.update(
                run_id=previous["run_id"],
                plan_id=previous["plan_id"],
                previous_attempt_id=previous["attempt_id"],
            )
        handoff = request_handoff(payload)
    result = normalize_handoff(handoff, mode, prompt, previous, values if previous is not None else None)
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
            VIEWS.pop(next(iter(VIEWS)))
        VIEWS[result["view_id"]] = result
    return result


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
            return self.send_json(200, {"prompt": PROMPT, "variables": CONTROLS})
        if url.path == "/api/config":
            return self.send_json(200, {"backend_configured": bool(backend_url())})
        return super().do_GET()

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
        except InvalidHandoff as exc:
            return self.send_json(502, {"error": str(exc)})
        except UnverifiedResult as exc:
            return self.send_json(422, {"error": str(exc)})
        except ValueError as exc:
            return self.send_json(400, {"error": str(exc)})
        self.send_json(200, result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Fleet Lab: http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
