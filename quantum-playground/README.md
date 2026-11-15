# Fleet Lab

A local dashboard that animates EV dispatch schedules with an offscreen Pygame renderer.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. The built-in live backend starts Van 2 at 35% charge. Move the slider to any whole percentage from 20% to 35% to launch a fresh solve and update the map when its schedule passes verification.

The example worker uses the local QAOA simulator by default. To run it through Quantum Rings, install `QuantumRingsLib` in the same environment and set `QR_ACCOUNT` and `QR_KEY` in the repository root's ignored `.env`.

## Plan a fleet run from a prompt

Install the backend requirements in the same Python environment.

```sh
python -m pip install -r requirements.txt
```

Set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `QR_ACCOUNT`, and `QR_KEY` in the repository root's `.env`. Set `GEMINI_MODEL` if your key requires a specific model. Restart the dashboard, select **Live backend**, and choose **Plan and run**. Open **Original prompt** to edit the fleet scenario or ask the planner to expose a setting such as the electricity rate. The settings displayed after a successful run come from the approved plan. Applying a new setting runs the solver and checks the resulting schedule before updating the map.

Numeric sliders follow the plan's approved values or range. The live example asks the planner for a 20–35% charge range in one-point steps. Releasing the Van 2 slider runs the solver when charge is the only pending change. For grouped rates, open **Costs** and select **Apply changes** to run with all pending edits.

Set `QUANTUM_BACKEND_URL` to use an external service that implements the [verified handoff contract](docs/BACKEND_CONTRACT.md).

For replay, choose **Recorded backend run** to see the [saved measurements](../example-backend-final-result), or **Exact classical reference** to see the exact schedules.

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
