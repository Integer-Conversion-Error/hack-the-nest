# Verified EV handoff

The dashboard calls the backend host at `QUANTUM_BACKEND_URL`. The host owns prompt interpretation, planning, bounded solver execution, and independent verification. The repository currently implements Init and Plan. The future backend HTTP handler should call the host and serialize the verified result after GO. See the [solver skill](../../agent-prompts/skills/backend/run-quantum-solver/SKILL.md) and [verification skill](../../agent-prompts/skills/backend/verify-and-handoff/SKILL.md).

Set the URL in the dashboard server environment. For example, in PowerShell:

```powershell
$env:QUANTUM_BACKEND_URL = 'http://127.0.0.1:8001'
python quantum-playground/server.py
```

## Host request

Implement `POST /api/run` with an `application/json` body. An initial request carries the original prompt and lets the host resolve the plan defaults.

```json
{"schema_version":1,"prompt":"Dispatch the fleet using these inputs...","values":{}}
```

Apply requests carry the last accepted backend identities and the complete set of requested values for the approved controls. Load the original prompt and approved plan from the run, validate the control values, and create a fresh attempt under the same plan. The EV plan uses `rerun_mode: rebuild` for `van_2_initial_charge_pct`. Rebuild the options and circuit, execute, then independently verify the selected result and recompute its exact reference.

```json
{"schema_version":1,"run_id":"ev-run-1","plan_id":"ev-plan-1","previous_attempt_id":"attempt-1","values":{"van_2_initial_charge_pct":20}}
```

The host enforces its workspace limits. The dashboard waits up to 300 seconds for the response. For clarification, unsupported problems, timeouts, and NO-GO, return an appropriate HTTP error and a JSON `error` string with the actionable next step. An identified result with a status other than `verified` is also rejected. Its `message` is displayed when supplied.

## Host response

A successful response is a v1 `VisualizationHandoff` with these fields.

| Field | Contents |
| --- | --- |
| `schema_version` | `1` |
| `run_id`, `plan_id`, `attempt_id` | The host IDs, repeated exactly in `result`. An Apply response keeps the run and plan IDs and returns a fresh attempt ID. |
| `scene_type` | `"ev_fleet"` |
| `problem` | `title`, a concise `statement`, and the exact `original_prompt`. |
| `controls` | Approved numeric enum controls using the plan's `key` (or `id`), `label`, `allowed_values`, `default`, and `rerun_mode`. An optional `unit` is appended to displayed choice values. Keep the definitions stable for the same plan ID. |
| `result` | The verified `RunResult` described below. |

The result includes the three IDs, `status: "verified"`, and `provenance: "live"` for a fresh execution or `"recorded"` for previously verified execution data. `effective_values` gives the complete control values used for the attempt. The initial values match the approved defaults. Apply results match the requested values.

- `simulation` describes the selected, timed EV schedule. It has `kind: "ev_fleet"`, `grid: {width, height, block_km}`, `depot: [x,y]`, `chargers: [{id, position}]`, `deliveries: [{id, position, deadline_min}]`, `start_hour`, `duration_min`, `battery_capacity_kwh`, `reserve_pct`, and `vans`. The scene supports grids from 2 to 50 intersections on each axis and up to four vans.
- Each van has `id`, `initial_battery_kwh`, `delivery_ids`, and `events`. A van starts at the depot and its events are chronological and non-overlapping. Include the return drive when required by the plan.
- Each event supplies `type` (`drive`, `wait`, `charge`, or `deliver`), `start_min`, `end_min`, `position` at its start, `battery_start_kwh`, and `battery_end_kwh`. Drive events list successive neighboring intersections in `path`. Charging uses `charger_id`, charger waits use it when applicable, and deliveries use `delivery_id`. All positions are integer grid intersections. Times are minutes from `start_hour`; charging intervals are half-open.
- `evaluation` supplies `feasible: true`, the total `objective_value`, a component map `costs`, and the number `delivered`. Report costs in USD, without rounding away precision. Optional `reference_value` and `gap` give the exact classical comparison in USD on the same effective inputs and options.
- Supply actual evidence when available in `circuits: [{title, drawing}]`, `quantum: {binary_variables, actual_qubits}`, and `simulation.counts` (measured bitstrings mapped to integer counts). `actual_qubits` includes auxiliary qubits and may be null. Optional `assumptions` and `explanation` describe the verified run.

The dashboard validates handoff identity, approved settings, and replay consistency before creating a view. It consumes the host's feasibility and objective result. The host retains execution and verification evidence.

## Reference data and integration checks

The dashboard includes two timed exact classical replays from the [EV solver plan](../../example-solver-plans/ev-fleet-dispatch-optimization.md). These show the intended payload shape with `status: "reference"` and `provenance: "classical_reference"` inside the local example path. They contain no measured outcomes.

From `quantum-playground`, inspect a full handoff envelope or run the stub-backend tests.

```sh
python -c "import json; from ev_reference import reference_handoff; print(json.dumps(reference_handoff({}), indent=2))"
python -m unittest discover -s tests -p test_handoff_http.py -v
```
