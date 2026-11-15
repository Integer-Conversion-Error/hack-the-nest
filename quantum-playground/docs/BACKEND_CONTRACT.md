# EV fleet integration contract

The dashboard server calls the quantum team's HTTP service at `QUANTUM_BACKEND_URL`. Units: kilometers, kWh, dollars, minutes. Coordinates are `[x,y]` intersections. All event times are minutes after `simulation.start_hour` (8 means 08:00). Events must be chronological and non-overlapping per van. A drive path lists every consecutive intersection, without diagonal movement. Battery endpoints support linear playback; the backend remains responsible for energy physics and evaluation.

## Solver request

`POST /api/solve`

```json
{"problem_statement":"Complete natural language problem including required inputs","parameters":{"battery_kwh":20,"initial_1":20,"charging_kw":10}}
```

The Python Gemini tool merges UI overrides into the parameters, overriding any conflicting model value. On the first live submission only sliders actually changed by the user are sent. After a live solver response, its slider values are submitted on subsequent runs. Demo defaults are not silently imposed on the first live prompt.

Backend parameter names supported by the demo UI: `battery_kwh`, `reserve_pct`, `charging_kw`, `consumption`, `electricity_price`, `driving_cost`, `waiting_cost`, `late_cost`, `undelivered_cost`, `speed_kmh`, `service_minutes`, `initial_1` through `initial_4`. The live backend can return different numeric variable definitions.

## Solver response

Return `run_id`, `problem`, `variables`, `circuits`, `quantum`, `assumptions`, and `simulation`. This is a minimal format illustration, not a feasible full fleet plan:

```json
{
  "run_id": "ev-run-123",
  "problem": {"title":"EV Fleet Dispatch","statement":"Classical routes and QAOA charging"},
  "variables": [{"id":"charging_kw","label":"Charging power","min":2,"max":22,"step":1,"default":10,"unit":"kW"}],
  "circuits": [{"title":"Actual QAOA circuit","drawing":"Your actual circuit draw() output"}],
  "quantum": {"binary_variables":32,"qubit_target":80,"actual_qubits":32},
  "assumptions": ["Document user-approved deadlines, capacity, charging horizon, travel and service times here"],
  "simulation": {
    "kind":"ev_fleet",
    "grid":{"width":6,"height":6,"block_km":0.5},
    "depot":[2,2],
    "chargers":[{"id":"C1","position":[2,1]},{"id":"C2","position":[5,4]}],
    "deliveries":[{"id":"D1","position":[0,0],"deadline_min":180}],
    "start_hour":8,"duration_min":240,"charging_slot_count":4,"slot_minutes":30,
    "battery_capacity_kwh":20,"reserve_pct":15,
    "vans":[{
      "id":"V1","initial_battery_kwh":4,"delivery_ids":["D1"],
      "events":[
        {"type":"drive","start_min":0,"end_min":1.5,"position":[2,2],"path":[[2,2],[2,1]],"battery_start_kwh":4,"battery_end_kwh":3.85},
        {"type":"wait","start_min":1.5,"end_min":30,"position":[2,1],"charger_id":"C1","battery_start_kwh":3.85,"battery_end_kwh":3.85},
        {"type":"charge","start_min":30,"end_min":60,"position":[2,1],"charger_id":"C1","energy_kwh":5,"battery_start_kwh":3.85,"battery_end_kwh":8.85}
      ]
    }],
    "counts":{}
  }
}
```

`position` is the event's starting position; non-drive events remain there. Complete delivery events use `type: "deliver"`, `delivery_id`, position, times, and unchanged battery endpoints. Include driving, waiting, charging, service events and the final return drive. Model long idle gaps explicitly as `wait` when they incur waiting cost. Events extending past the operating horizon are clipped in the timeline, not hidden from the evaluator. `counts` contains real measurements only; leave empty if unavailable. `actual_qubits` must reflect the executed circuit including auxiliaries; use null if unknown. The 80-qubit target is not a verified property of a missing circuit.

## Evaluation

`POST /api/evaluate` receives `{"run_id":"ev-run-123"}`. Evaluate the stored original input and stored solver output, not a Gemini reconstruction.

```json
{
  "feasible":false,
  "objective_value":55.82,
  "costs":{"driving":7.20,"electricity":3.00,"waiting":45.62,"late":0,"undelivered":0},
  "violations":["Example only: replace with actual evaluation issues"],
  "delivered":8,
  "optimality_proven":false
}
```

Costs are schedule totals; delivery progress in the website is relative to the playback clock. The interface displays the backend's evaluation rather than independently certifying a live result. Reject unsupported problems and invalid parameters with a useful HTTP error.

## Clarifications for Prompt V2

Ask for speed/travel times, service times, capacity and package sizes, deadlines, depot return requirements, and the placement of four half-hour charging slots in a four-hour operating day. If stations start occupied, obtain occupancy intervals. Demo mode explicitly supplies assumptions; live mode must get user inputs or approved assumptions. Routing is classical; charging decisions are the QAOA scope. Do not claim optimality or quantum advantage without comparison evidence.

To inspect a full demo-shaped payload locally:

```bash
python3 -c 'import json; from ev_demo import demo; print(json.dumps(demo("EV preview", {}), indent=2))'
```

## Plain-language display fields

Return `problem.statement` as one or two short sentences describing the objective and essential constraints based on the user input. Keep full solver input in stored run data. The local server preserves the submitted prompt in `problem.original_prompt`. Supply actual circuit drawings in `circuits[].drawing`; the website keeps waiting slots until these are available.
