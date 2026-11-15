# Fleet Lab

A local dashboard that animates EV dispatch schedules with an offscreen Pygame renderer.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. The dashboard starts with the recorded backend result at 35% starting charge for Van 2. It cost $15.78 and used five qubits. Choose 20% and load the recorded result for the $58.37 run with four qubits. Each replay includes the original paths, battery levels, and 2,048 measurement shots. Click a van, the charger, or a delivery to inspect the schedule.

Both recordings are available locally in [example-backend-final-result](../example-backend-final-result). The separate **Exact classical reference** mode shows the classical schedules from the [EV solver plan](../example-solver-plans/ev-fleet-dispatch-optimization.md). **Live backend** uses `QUANTUM_BACKEND_URL` and the [verified handoff contract](docs/BACKEND_CONTRACT.md).

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
