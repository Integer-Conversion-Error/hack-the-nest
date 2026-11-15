# Fleet Lab

A local dashboard that animates EV dispatch schedules with an offscreen Pygame renderer.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. The exact classical reference loads with Van 2 starting at 35% charge and a total cost of $15.78. Choose 20% and click **Apply and recalculate** to replay the $58.37 reference. Click vans, the charger, or delivery stops to inspect the schedule.

The reference uses the committed [EV solver plan](../example-solver-plans/ev-fleet-dispatch-optimization.md) and requires only Pygame. For measured results, set `QUANTUM_BACKEND_URL` to a backend that serves the [verified handoff contract](docs/BACKEND_CONTRACT.md). The backend CLI writes a workspace `handoff.json` after GO. Live mode requires an HTTP adapter that translates the CLI handoff to the dashboard contract.

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
