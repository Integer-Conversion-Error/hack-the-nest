# Fleet Lab

A local dashboard for planning, solving, and replaying EV dispatch schedules.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. Use the prompt screen to start a new run or open the recorded example. With the built-in live backend, the EV example starts Van 2 at 35% charge. Move the slider to any whole percentage from 20% to 35% to launch a fresh solve and update the map when its schedule passes verification.

The example worker uses the local QAOA simulator by default. To run it through Quantum Rings, install `QuantumRingsLib` in the same environment and set `QR_ACCOUNT` and `QR_KEY` in the repository root's ignored `.env`.

## Plan a fleet run from a prompt

Install the backend requirements in the same Python environment.

```sh
python -m pip install -r requirements.txt
```

Set `QR_ACCOUNT` and `QR_KEY` in the repository root's `.env`. Gemini is the default model provider: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), with an optional `GEMINI_MODEL` override. To use OpenRouter, set `MODEL_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, and `OPENROUTER_MODEL`. The [model provider guide](../backend/MODEL_PROVIDERS.md) covers models per stage, shared retries, and usage tracking. Restart the dashboard, enter an EV fleet problem under **Your problem**, and select **Run pipeline**. From a results or history screen, select **New problem** to return to the prompt box.

Choose a sample and select **Load sample** to fill the prompt box. Edit the text before running if you want to change the scenario.

- [Charge and energy prices](../example-user-prompts/ev-charge-and-energy-prices.md) lets you vary Van 2's starting charge, electricity price, and driving cost.
- [Waiting and late fees](../example-user-prompts/ev-waiting-and-late-delivery.md) lets you vary Van 2's starting charge, charger waiting cost, and late-delivery penalty.
- [Battery and charger limits](../example-user-prompts/ev-battery-and-charger-limits.md) lets you vary Van 2's starting charge, minimum battery reserve, and charger power.
- [Full EV dispatch scenario](../example-user-prompts/ev-fleet-dispatch-full-scenario.md) uses a 10 × 10 grid, four vans, and two chargers. Its 17 adjustable settings cover starting charges, battery and charging limits, delivery timing, and costs.
- [Six-van EV dispatch](../example-user-prompts/ev-six-van-six-controls.md) uses a 12 × 12 grid and three chargers. Its six settings cover Van 2's starting charge, reserve, charger power, service time, waiting cost, and the late-delivery penalty.

The progress view shows individual steps and findings for prompt interpretation, planning, solver build, testing, and final verification. Collapse an attempt or choose **Hide all steps** for a stage summary. When a check sends the run back, the original attempt stays in the timeline with the reason for revision. Refreshing the same tab reconnects to the run while the dashboard server is running. The dashboard opens the results as soon as the verified schedule is ready, where you can reopen the progress timeline. If the pipeline needs more details, revise the prompt using the feedback and run it again.

The settings displayed after a successful run come from the approved plan. Applying a new setting runs the solver and checks the resulting schedule before updating the map.

Numeric sliders follow the plan's approved values or range. The live example asks the planner for a 20–35% charge range in one-point steps. Releasing the Van 2 slider runs the solver when charge is the only pending change. For grouped rates, open **Costs** and select **Apply changes** to run with all pending edits.

## Reopen a saved run

Open **Run history** and choose a workspace to see its original prompt and download the available plan, solver, and verification artifacts. **Open results** replays a verified EV schedule directly from its saved handoff. Workspaces without a compatible replay still show their prompt and available files.

By default, the CLI and dashboard use the `workspaces/` directory in their checkout. Set `QUANTUM_WORKSPACES_ROOT` to an existing workspace directory to share runs across checkouts. New runs are saved there too.

```powershell
$env:QUANTUM_WORKSPACES_ROOT = 'C:\path\to\workspaces'
python quantum-playground/server.py
```

Set `QUANTUM_BACKEND_URL` to use an external service that implements the [verified handoff contract](docs/BACKEND_CONTRACT.md).

For replay, choose **Recorded backend run** to see the [saved measurements](../example-backend-final-result), or **Exact classical reference** to see the exact schedules.

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
