# Fleet Lab

A local website with an interactive Pygame fleet map and a Gemini agent connection.

## Run

```bash
source /mnt/c/Users/azmae/OneDrive/HackTheNest/qr-gpu/bin/activate
cd /mnt/c/Users/azmae/OneDrive/HackTheNest/hack-the-nest/quantum-playground
python -m pip install -r requirements.txt
python server.py
```

Open **http://localhost:8000**. Example mode works without a Gemini key. Live mode requires `GEMINI_API_KEY` and `QUANTUM_BACKEND_URL`.

## Files needed to run the app

| File or folder | Purpose |
| --- | --- |
| `server.py` | Starts the website and handles requests. |
| `agent.py` | Connects Gemini to the team's solver and evaluator. |
| `ev_demo.py` | Supplies the interactive example schedule. |
| `pygame_renderer.py` | Draws the interactive fleet map. |
| `web/` | Website layout, styles, and interaction code. |
| `requirements.txt` | Python dependencies. |

## Supporting folders

- `tests/` — checks used during development; not needed to run the website.
- `docs/` — [setup and development details](docs/DEVELOPMENT.md), plus the [backend contract](docs/BACKEND_CONTRACT.md) for your teammates.
- `archive/` — old preview images and the unused external Pygame bridge; not loaded by the app.

The example is a classical preview. Actual quantum-assisted results require your teammates' backend.
