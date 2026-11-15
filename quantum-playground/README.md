# Fleet Lab

A local dashboard that animates EV dispatch schedules with an offscreen Pygame renderer.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. The built-in live backend solves the EV example at 35% starting charge for Van 2. Choose 20% to launch a fresh solve and update the map when its schedule passes verification.

The worker uses the local QAOA simulator by default. To run through Quantum Rings, install `QuantumRingsLib` in the same environment and set `QR_ACCOUNT` and `QR_KEY` in the repository root's ignored `.env`. An external service can take over Live backend by setting `QUANTUM_BACKEND_URL` and implementing the [verified handoff contract](docs/BACKEND_CONTRACT.md).

For replay, choose **Recorded backend run** to see the [saved measurements](../example-backend-final-result), or **Exact classical reference** to see the exact schedules.

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
