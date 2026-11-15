"""Replay the exact classical schedules described in the EV solver plan."""

import json
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

SCENARIO = "ev-fleet-dispatch-optimization"
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "example-solver-plans" / SCENARIO

PROMPT = (REPO_ROOT / "example-user-prompts" / f"{SCENARIO}.md").read_text(encoding="utf-8")
PROBLEM = json.loads((FIXTURES / "problem.json").read_text(encoding="utf-8"))
PLAN = json.loads((FIXTURES / "plan.json").read_text(encoding="utf-8"))["plan"]
PLAN_ID = "ev-fleet-dispatch-v1"

CONTROL_KEY = "van_2_initial_charge_pct"
CONTROL = next(control for control in PLAN["controls"] if control["key"] == CONTROL_KEY)
CONTROLS = [{
    "id": CONTROL_KEY,
    "label": CONTROL["label"].removesuffix(" (%)"),
    "allowed_values": CONTROL["allowed_values"],
    "default": CONTROL["default"],
    "unit": "%",
    "rerun_mode": CONTROL["rerun_mode"],
}]

DEPOT = (2, 2)
CHARGER = (2, 1)
DELIVERIES = (((0, 0), 90), ((5, 0), 60))
SLOT_STARTS = (30, 60)
BOOKINGS = {35: (SLOT_STARTS[0], None), 20: (SLOT_STARTS[1], SLOT_STARTS[0])}
BINARY_VARIABLES = {35: 5, 20: 4}
BATTERY_CAPACITY = 20
RESERVE_PCT = 15
SLOT_MINUTES = 30
DURATION_MINUTES = 120
SERVICE_MINUTES = 5
ENERGY_PER_BLOCK = Fraction(15, 100)
DRIVING_PER_BLOCK = Fraction(15, 100)
ELECTRICITY_PER_KWH = Fraction(1, 5)
WAITING_PER_MINUTE = Fraction(25, 60)
LATE_PER_MINUTE = Fraction(1)
COST_FIELDS = ("driving", "electricity", "waiting", "late")


def _street_path(origin, destination):
    """Use a shortest path that follows the horizontal axis first."""
    path = [list(origin)]
    for axis in (0, 1):
        while path[-1][axis] != destination[axis]:
            point = path[-1].copy()
            point[axis] += 1 if destination[axis] > point[axis] else -1
            path.append(point)
    return path


def _recorded_route(number, initial_charge_pct, charge_start):
    delivery, deadline = DELIVERIES[number - 1]
    delivery_id = f"D{number}"
    initial_battery = Fraction(BATTERY_CAPACITY * initial_charge_pct, 100)
    battery = initial_battery
    position = DEPOT
    minute = 0
    events = []
    costs = dict.fromkeys(COST_FIELDS, Fraction(0))

    def add_event(kind, duration, energy_change=0, **details):
        nonlocal minute, battery
        end_battery = battery + energy_change
        events.append({
            "type": kind,
            "start_min": minute,
            "end_min": minute + duration,
            "position": list(position),
            "battery_start_kwh": float(battery),
            "battery_end_kwh": float(end_battery),
            **details,
        })
        minute += duration
        battery = end_battery

    def drive(destination):
        nonlocal position
        path = _street_path(position, destination)
        blocks = len(path) - 1
        # Each half-kilometre block takes one minute at 30 km/h.
        add_event("drive", blocks, -blocks * ENERGY_PER_BLOCK, path=path)
        costs["driving"] += blocks * DRIVING_PER_BLOCK
        position = destination

    if charge_start is not None:
        drive(CHARGER)
        wait = charge_start - minute
        add_event("wait", wait, charger_id="C1")
        costs["waiting"] += wait * WAITING_PER_MINUTE
        energy = min(5, BATTERY_CAPACITY - battery)
        add_event("charge", SLOT_MINUTES, energy, charger_id="C1", energy_kwh=float(energy))
        costs["electricity"] += energy * ELECTRICITY_PER_KWH

    drive(delivery)
    add_event("deliver", SERVICE_MINUTES, delivery_id=delivery_id)
    late_minutes = max(0, minute - deadline)
    costs["late"] += late_minutes * LATE_PER_MINUTE
    drive(DEPOT)

    return {
        "id": f"V{number}",
        "initial_battery_kwh": float(initial_battery),
        "delivery_ids": [delivery_id],
        "events": events,
    }, costs, late_minutes


def reference_handoff(values, previous=None):
    """Create a recorded replay under the EV plan, with a new attempt per Apply."""
    if set(values) - {CONTROL_KEY}:
        raise ValueError(f"The EV reference only supports {CONTROL_KEY}.")
    charge = values.get(CONTROL_KEY, CONTROL["default"])
    if type(charge) is not int or charge not in CONTROL["allowed_values"]:
        raise ValueError(f"{CONTROL_KEY} must be 35 or 20.")

    identities = {
        "run_id": previous["run_id"] if previous is not None else str(uuid4()),
        "plan_id": previous["plan_id"] if previous is not None else PLAN_ID,
        "attempt_id": str(uuid4()),
    }
    first, second = BOOKINGS[charge]
    van1, costs1, late1 = _recorded_route(1, 20, first)
    van2, costs2, late2 = _recorded_route(2, charge, second)
    costs = {key: costs1[key] + costs2[key] for key in COST_FIELDS}
    total = sum(costs.values())

    simulation = {
        "kind": "ev_fleet",
        "grid": {"width": 6, "height": 6, "block_km": 0.5},
        "depot": list(DEPOT),
        "chargers": [{"id": "C1", "position": list(CHARGER)}],
        "deliveries": [
            {"id": f"D{i}", "position": list(position), "deadline_min": deadline}
            for i, (position, deadline) in enumerate(DELIVERIES, 1)
        ],
        "start_hour": 8,
        "duration_min": DURATION_MINUTES,
        "charging_slot_count": len(SLOT_STARTS),
        "slot_minutes": SLOT_MINUTES,
        "battery_capacity_kwh": BATTERY_CAPACITY,
        "reserve_pct": RESERVE_PCT,
        "vans": [van1, van2],
        "counts": {},
    }
    evaluation = {
        "feasible": True,
        "objective_value": float(total),
        "objective_units_60": int(total * 60),
        "costs": {name: float(value) for name, value in costs.items()},
        "delivered": len(DELIVERIES),
        "late_minutes": {"D1": late1, "D2": late2},
        "violations": [],
        "optimality_proven": True,
        "evaluation_scope": "Exact classical enumeration of the EV plan's feasible route-and-charging options",
    }

    return {
        "schema_version": 1,
        **identities,
        "scene_type": "ev_fleet",
        "problem": {
            "title": PROBLEM["title"],
            "statement": "Dispatch two vans on a 6 × 6 street grid and schedule their use of one charger. "
                         "Minimize driving, charging, waiting, and lateness costs while meeting the battery reserve "
                         "and returning both vans by 10:00 AM.",
            "original_prompt": PROMPT,
        },
        "controls": CONTROLS,
        "result": {
            **identities,
            "status": "reference",
            "provenance": "classical_reference",
            "effective_values": {CONTROL_KEY: charge},
            "simulation": simulation,
            "evaluation": evaluation,
            "circuits": [],
            "quantum": {"binary_variables": BINARY_VARIABLES[charge], "actual_qubits": None},
            "assumptions": PROBLEM["assumptions"],
            "explanation": f"Recorded exact classical reference. At {charge}% starting charge for Van 2, "
                           f"the two deliveries cost ${float(total):.2f} in total.",
        },
    }
