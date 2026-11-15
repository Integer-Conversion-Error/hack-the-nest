"""Local dashboard. Run: python server.py (no dependencies needed for demo)."""
import json
import os
import math
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from uuid import uuid4
from threading import Lock

ROOT = Path(__file__).parent
RUNS = {}
LOCK = Lock()
FRAME = None


def backend(path, payload):
    base = os.environ.get('QUANTUM_BACKEND_URL', '').rstrip('/')
    if not base:
        raise ValueError('Set QUANTUM_BACKEND_URL to your teammates’ backend URL.')
    request = Request(base + path, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


from ev_demo import demo, variables, PROMPT


def run_agent(prompt, variables):
    from agent import orchestrate
    return orchestrate(prompt, variables, backend)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'web'), **kwargs)

    def send_json(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlparse(self.path).path == '/api/pygame-frame':
            from pygame_renderer import render_png, scene_layout, PygameUnavailable
            try:
                params = parse_qs(urlparse(self.path).query)
                with LOCK:
                    run = RUNS.get(params.get('run_id', [''])[0])
                if run is None:
                    return self.send_json(404, {'error': 'This run is no longer available. Generate a new dispatch.'})
                minute = float(params.get('minute', ['0'])[0])
                if not math.isfinite(minute) or not 0 <= minute <= run.get('simulation', {}).get('duration_min', 240):
                    raise ValueError('Invalid replay time.')
                speed = float(params.get('speed', ['4'])[0])
                if speed not in (1,4,12):
                    raise ValueError('Invalid playback speed.')
                resolution = int(params.get('resolution', ['2'])[0])
                if resolution not in (1,2,3):
                    raise ValueError('Invalid frame resolution.')
                simulation = run.get('simulation', {})
                frame = render_png(simulation, minute, params.get('selected', [None])[0],
                                   params.get('playing', ['false'])[0]=='true', speed, resolution)
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(frame)))
                self.send_header('Cache-Control', 'no-store')
                scene = scene_layout(simulation, minute)
                scene.update(pixel_width=scene['width']*resolution, pixel_height=scene['height']*resolution)
                self.send_header('X-Pygame-Scene', json.dumps(scene, separators=(',', ':')))
                self.end_headers()
                self.wfile.write(frame)
            except PygameUnavailable as exc:
                self.send_json(503, {'error': str(exc)})
            except (ValueError, TypeError, KeyError) as exc:
                self.send_json(400, {'error': str(exc)})
            except Exception as exc:
                print(f'Pygame rendering failed: {exc}')
                self.send_json(500, {'error': 'Pygame rendering failed. Check the server terminal.'})
            return
        if self.path == '/api/example':
            return self.send_json(200, {'prompt': PROMPT, 'variables': variables()})
        if self.path == '/api/config':
            return self.send_json(200, {'gemini_configured': bool(os.environ.get('GEMINI_API_KEY')), 'backend_configured': bool(os.environ.get('QUANTUM_BACKEND_URL'))})
        if urlparse(self.path).path == '/api/frame':
            with LOCK:
                frame = FRAME
            if frame is None:
                return self.send_json(404, {'error': 'No Pygame frame received yet.'})
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(frame)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(frame)
            return
        return super().do_GET()

    def do_POST(self):
        global FRAME
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 5_000_000:
                raise ValueError('Invalid request size.')
            raw = self.rfile.read(size)
            if self.path == '/api/frame':
                if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Expected a PNG frame.')
                with LOCK:
                    FRAME = raw
                return self.send_json(200, {'ok': True})
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            if self.path == '/api/run':
                prompt = data.get('prompt', '')
                if not isinstance(prompt, str):
                    raise ValueError('Prompt must be text.')
                prompt = prompt.strip()
                if not prompt or len(prompt) > 10000:
                    raise ValueError('Enter a prompt of 1–10,000 characters.')
                mode = data.get('mode', 'demo')
                if mode not in ('demo', 'live'):
                    raise ValueError('Unknown mode.')
                variables = data.get('variables', {})
                if not isinstance(variables, dict):
                    raise ValueError('Variables must be an object.')
                if mode == 'live':
                    if not os.environ.get('GEMINI_API_KEY') or not os.environ.get('QUANTUM_BACKEND_URL'):
                        raise ValueError('Live mode needs GEMINI_API_KEY and QUANTUM_BACKEND_URL on the server.')
                    result = run_agent(prompt, variables)
                else:
                    result = demo(prompt, variables)
                result.setdefault('problem', {})['original_prompt'] = prompt
                with LOCK:
                    if len(RUNS) >= 100:
                        RUNS.pop(next(iter(RUNS)))
                    RUNS[result['run_id']] = result
                return self.send_json(200, result)
            if self.path == '/api/feedback':
                with LOCK:
                    run = RUNS.get(data.get('run_id'))
                    if run is None:
                        raise ValueError('Unknown run ID.')
                    feedback = str(data.get('feedback', ''))[:2000]
                    run.setdefault('feedback', []).append(feedback)
                return self.send_json(200, {'ok': True})
            self.send_json(404, {'error': 'Unknown endpoint.'})
        except (ValueError, TypeError, KeyError) as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception as exc:
            print(f'Request failed: {type(exc).__name__}: {exc}')
            self.send_json(502, {'error': 'Agent/backend request failed. Check the server terminal for details.'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    print(f'Quantum Playground: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
