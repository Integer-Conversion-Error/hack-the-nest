# Verified EV handoff

## Geographic scenarios and replay

Geographic builder source uses `schema_version: 2`. It replaces `grid` with
`map: {kind:"geographic", profile:"driving"}`. Positions are GeoJSON
`[longitude, latitude]`, rounded to six decimals, with longitude −180…180 and
latitude −85…85. `timing` contains `start_hour` and `duration_min`; geographic
driving time comes from road routing, so there is no speed input/control. Fleet,
depot, vehicle, charger, stop assignment, service, deadline and cost fields keep
their existing meaning. Original source v1 grid shapes and prompt bytes remain
compatible with saved runs.

`GET /api/maps/config` supplies tile URL and attribution. `GET
/api/maps/search?q=...` returns `{results:[{label,position:[lon,lat]}]}`; optional
`lon` and `lat` together bias search. Invalid queries return 400, provider
failures return an actionable JSON error. Provider URLs are operator-owned
environment settings and cannot be supplied by clients. Invalid optional map
configuration does not prevent the grid dashboard from starting.

Before planning, the host prepares read-only `routing.json`: version, source
digest, provider/profile/endpoint, retrieval time and optional data version,
stable ordered nodes with requested and road-snapped positions, directed
`distances_m` and `durations_s` matrices, and its content digest. At most 72
locations require one table request. Snapping is bounded to 100 metres; null
cells stay unreachable. The solver and independent verifier read that exact
snapshot. It participates in approval/cache fingerprints and is downloadable
from saved-run artifacts. Matrices are separate from the 64 KB scenario prompt.

The normalized VisualizationHandoff is v3 for geographic replay. Its simulation
has `map: {kind:"geographic",profile:"driving",routing_digest:...}` and no
`grid`; depots, chargers, stops and event positions use road-snapped coordinates.
Drives identify `from_id`, `to_id`, and `distance_km`; elapsed time and energy
are checked independently against the frozen directed matrix. Worker-generated
paths are not trusted: the host retrieves only selected road legs for quantum
and classical schedules, verifies provider metrics/endpoints against the
snapshot, and supplies `path` geometry in the displayed replay.

Captured geometry is saved in the native handoff's `geographic_replay`, bound to
the raw result and routing digests. Opening saved results never fetches new road
geometry. Apply uses the same immutable matrix and source with effective numeric
overlays, capturing any newly selected legs in the temporary attempt; the
original saved handoff remains unchanged. Material provider/snapshot disagreement
requires a fresh plan rather than changing verified costs. The browser animates
geographic paths by traveled distance; the legacy PNG frame endpoint remains
grid-only and returns an actionable 422 for geographic views.

## Interactive scenario inputs

`POST /api/pipeline-runs` also accepts `{ "scenario": { ... } }`. The `scenario`
and text `prompt` fields are mutually exclusive. The server validates the complete version-1
scenario, generates the deterministic prompt, and returns that prompt in job
snapshots. This route requires native planning. `/api/config` advertises that
capability as `scenario_backend_configured`.

The structured input contains `schema_version: 1`, `title`, `grid`, `depots`,
`chargers`, `deliveries`, `vehicles`, `fleet`, `timing`, and `costs`. Each vehicle
has a `depot_id` and `initial_charge_pct`; each delivery has a `vehicle_id`,
`position`, `deadline_min`, and `service_min`. Chargers supply `power_kw` and
named `{id, start_min, end_min}` slots. IDs are stable (`P1`, `C1`, `D1`, `V1`,
and `S1` within each charger). See `scenario.py` for the complete validation and
`example_scenario()` for an executable example. Source JSON and the generated
prompt are each bounded to 64 KB, separately from the existing 10,000-character
text prompt limit.

The host saves an immutable `scenario.json` with its digest in workspace
metadata. It independently verifies every stop, depot, route, charging slot,
battery trajectory, service time, and USD component against the effective
scenario. Those checks run during initial pipeline tests, settings
recalculation, and saved-result opening, including the classical replay when
present. The generated solver cannot edit the source file.

Numeric reuse retains `/api/run {mode:"live", view_id, variables}`. Only
server-owned field bindings intersected with approved numeric controls can be
applied; changed topology and other inputs require a new pipeline job. Every
builder result includes host-computed `scenario_context` with `base_digest`,
`effective_digest`, `effective_scenario`, and the approved `bindings`. Paths
address entities by stable ID. Recalculation never modifies the original
scenario or saved default result. History exposes `can_edit` and the scenario
artifact, including incomplete builder runs; the displayed attempt's context
is used when editing its effective inputs.

## Multiple-depot replay (v2)

VisualizationHandoff v2 uses `simulation.depots: [{id,position}]` and each
van's `depot_id` instead of the v1 shared `simulation.depot`. Every van starts
at its home depot. Builder scenarios also require returning there. Classical
and quantum replays must have identical depots and per-vehicle stop/home
assignments. Both renderers and validation continue to accept legacy v1
single-depot scenes; all other handoff identity and verification rules remain.

## Native pipeline

With Gemini or OpenRouter [configured](../../backend/MODEL_PROVIDERS.md), the dashboard starts the native `backend.pipeline` in an EV fleet workspace. The generated solver supplies a timed `simulation`, the effective control values, the cost breakdown, and its feasibility verification. When a feasible classical solution is computed, `reference` includes its method, cost, feasibility, optimality status, and optional timed replay. Test checks the replay contract before GO. The dashboard translates the approved workspace `handoff.json` into the response below, using the workspace ID as the plan ID.

The browser starts a new native run with `POST /api/pipeline-runs` and a JSON object containing `prompt`. The dashboard returns HTTP 202 with a run snapshot. Poll `GET /api/pipeline-runs/{id}` for the latest snapshot. IDs refer to jobs in the running dashboard process.

Each snapshot includes `id`, `status`, `stage`, and `attempt`. Status is `queued`, `running`, `completed`, or `failed`. Stage is initially null, then one of `init`, `plan`, `build`, `test`, or `verify`. The attempt count tracks how many times the run has entered the current stage. The stage can move backward when a check requires revisions. A completed snapshot includes the normalized dashboard `result` with its `view_id`. A failed snapshot includes an `error` message the browser can display alongside the original prompt.

`history` starts empty and lists every stage visit in order. A visit has a stable `id`, a `stage`, its `attempt` number, a `status` of `running`, `completed`, `revising`, or `failed`, and a `steps` list. Each step has an `id` within the visit, a `label`, optional plain-text `detail`, and a `status` of `running`, `completed`, `failed`, or `info`. Returning to an earlier stage marks the current visit as `revising` and appends a visit for the new attempt. Each poll returns the full history, including visits that finished between polls.

Saved runs are read from the configured workspace directory. `GET /api/workspaces` lists their IDs, titles, timestamps, and replay availability. `GET /api/workspaces/{id}` adds the original prompt and artifact keys. Fetch an individual file from `GET /api/workspaces/{id}/artifacts/{key}`. `POST /api/workspaces/{id}/open` with an empty JSON object registers a view of a verified EV handoff and returns it with a fresh `view_id`. Opening a saved result does not call the planner or solver.

Apply runs the approved solver with the full set of settings in a temporary copy of the workspace. It checks the replay and runs the pipeline's semantic review on the fresh result before displaying it. The approved workspace retains its original result for later attempts.

The built-in example worker is available without model-provider configuration. It accepts the live EV example prompt and rebuilds the QUBO for each whole percentage from 20% to 35%. The worker reports circuit measurements and passes the chosen schedule through independent verification before the dashboard updates. Recently verified settings can be served from the bounded result cache, with `cached: true` and a fresh display attempt ID. The next backend request retains the last actual backend attempt ID.

## External backend

Set `QUANTUM_BACKEND_URL` to use an external backend host. The host owns prompt interpretation, planning, bounded solver execution, and independent verification. It returns the versioned response below, including attempt identity and the timed EV schedule.

Set the URL in the dashboard server environment. For example, in PowerShell:

```powershell
$env:QUANTUM_BACKEND_URL = 'http://127.0.0.1:8001'
python quantum-playground/server.py
```

## Host request

Implement `POST /api/run` with an `application/json` body. The dashboard supplies a fresh `attempt_id` for each request, including retries. The host uses that ID for the execution and returns it in the handoff. An initial request carries the original prompt and lets the host resolve the plan defaults.

```json
{"schema_version":1,"attempt_id":"attempt-1","prompt":"Dispatch the fleet using these inputs...","values":{}}
```

Apply requests carry the last accepted backend identities and the complete set of requested values for the approved controls. Load the original prompt and approved plan from the run, validate the control values, and start the requested attempt under the same plan. The EV plan uses `rerun_mode: rebuild` for `van_2_initial_charge_pct`. Rebuild the options and circuit, execute, then independently verify the selected result and recompute its exact reference.

```json
{"schema_version":1,"attempt_id":"attempt-2","run_id":"ev-run-1","plan_id":"ev-plan-1","previous_attempt_id":"attempt-1","values":{"van_2_initial_charge_pct":20}}
```

The host enforces its workspace limits. The dashboard waits up to 300 seconds for the response. For clarification, unsupported problems, timeouts, and NO-GO, return an appropriate HTTP error and a JSON `error` string with the actionable next step. An identified result with a status other than `verified` is also rejected. Its `message` is displayed when supplied.

## Host response

A successful response is a v1 `VisualizationHandoff` with these fields.

| Field | Contents |
| --- | --- |
| `schema_version` | `1` |
| `run_id`, `plan_id`, `attempt_id` | The host run and plan IDs and the requested attempt ID, repeated exactly in `result`. An Apply response keeps the run and plan IDs. A completion for an earlier attempt is rejected. |
| `scene_type` | `"ev_fleet"` |
| `problem` | `title`, a concise `statement`, and the exact `original_prompt`. |
| `controls` | Approved Plan controls with `key` (or `id`), `label`, `type`, `default`, and `rerun_mode`. Specify `allowed_values` for discrete choices, or `minimum`, `maximum`, and optional `step` for a numeric range. A boolean can omit choices. Optional `unit` and `group` affect display. Keep the definitions stable for the same plan ID. |
| `result` | The verified `RunResult` described below. |

The Plan phase selects controls that the proposed implementation can apply. The dashboard renders the controls approved for that run and posts their typed values. Use those values in both the solver and independent verifier before sending the new schedule. The live EV example approves Van 2 starting charge from 20% to 35%, with a step of one percentage point and a default of 35%.

The result includes the three IDs, `status: "verified"`, and `provenance: "live"` for a fresh execution or `"recorded"` for previously verified execution data. `effective_values` gives the complete control values used for the attempt. The initial values match the approved defaults. Apply results match the requested values.

- `simulation` describes the selected, timed EV schedule. It has `kind: "ev_fleet"`, `grid: {width, height, block_km}`, `depot: [x,y]`, `chargers: [{id, position}]`, `deliveries: [{id, position, deadline_min}]`, `start_hour`, `duration_min`, `battery_capacity_kwh`, `reserve_pct`, and `vans`. The scene supports grids from 2 to 50 intersections on each axis and up to six vans.
- Each van has `id`, `initial_battery_kwh`, `delivery_ids`, and `events`. A van starts at the depot and its events are chronological and non-overlapping. Include the return drive when required by the plan. When the plan exposes `van_2_initial_charge_pct`, identify Van 2 as `V2` and set its initial battery to the effective percentage of `battery_capacity_kwh`.
- Each event supplies `type` (`drive`, `wait`, `charge`, or `deliver`), `start_min`, `end_min`, `position` at its start, `battery_start_kwh`, and `battery_end_kwh`. Drive events list successive neighboring intersections in `path`. Charging uses `charger_id`, charger waits use it when applicable, and deliveries use `delivery_id`. All positions are integer grid intersections. Times are minutes from `start_hour`; charging intervals are half-open.
- `evaluation` supplies `feasible: true`, the total `objective_value`, a component map `costs`, and the number `delivered`. Report costs in USD, without rounding away precision. Optional `reference_value` and `gap` give the classical reference cost and difference in USD on the same effective inputs and options; optimality is reported separately.
- Optional `evaluation.classical` records `method`, `objective_value`, `feasible: true`, `optimality_proven`, and optional `selected_options`. Its cost must agree with `reference_value`; `gap` is the quantum cost minus the classical cost. Do not claim an exact optimum when `optimality_proven` is false.
- Optional `classical_simulation` supplies the independently verified classical schedule in the same replay format, sharing the quantum scene, assignments, operating clock, and effective inputs. This enables synchronized maps. Omit it when there is no compatible classical replay; the classical cost metadata can still be shown.
- Supply actual evidence when available in `circuits: [{title, drawing}]`, `quantum: {binary_variables, actual_qubits}`, and `simulation.counts` (measured bitstrings mapped to integer counts). `actual_qubits` includes auxiliary qubits and may be null. Optional `assumptions` and `explanation` describe the verified run.
- A circuit can supply `image: "data:image/png;base64,..."` instead of, or alongside, `drawing`, with optional plain-text `description`. Only bounded validated inline PNGs are accepted. Native PNGs are captured from the current verified attempt before temporary cleanup; saved-run PNGs must match the saved default result and circuit digest. Curated EV replays can reconstruct the solver's gate sequence from their own recorded angles and qubit mapping, and label that provenance explicitly. Missing image evidence is never replaced with an illustrative generic circuit.

The dashboard validates handoff identity, approved settings, and replay consistency before creating a view. It consumes the host's feasibility and objective result. The host retains execution and verification evidence.

## Local recordings and integration checks

Recorded backend mode projects the [recorded backend workspace](../../example-backend-final-result/20261115-071610-ev-fleet-dispatch-optimization-6252) into the same replay model. It uses `problem.json` for the map and operating constraints, the final GO `handoff.json` result for 35%, and the successful 20% run in `test.json`. The player preserves each result's measured counts, timed route, and comparison with the exact reference. The downloaded view identifies its source workspace and file.

The optional exact classical replays from the [EV solver plan](../../example-solver-plans/ev-fleet-dispatch-optimization.md) use `status: "reference"` and `provenance: "classical_reference"`. Their measurement counts are empty.

From `quantum-playground`, inspect a full recorded handoff envelope or run the HTTP tests.

```sh
python -c "import json; from ev_recorded import recorded_handoff; print(json.dumps(recorded_handoff({}), indent=2))"
python -m unittest discover -s tests -p test_handoff_http.py -v
```
