"""Project recorded and live EV solver results into a dashboard replay."""

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fleet_handoff import applied_values, normalize_controls, require


REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO = "ev-fleet-dispatch-optimization"
RECORDING_DIR = (
    REPO_ROOT / "example-backend-final-result" / "20261115-071610-ev-fleet-dispatch-optimization-6252"
)
RECORDED_PROMPT = (REPO_ROOT / "example-user-prompts" / f"{SCENARIO}.md").read_text(encoding="utf-8")


def _read_json(name):
    return json.loads((RECORDING_DIR / name).read_text(encoding="utf-8"))


_GO_HANDOFF = _read_json("handoff.json")
_PROBLEM = _read_json("problem.json")
RECORDED_CONTROLS = normalize_controls([
    {**control, "label": control["label"].removesuffix(" (%)"), "unit": "%"}
    if control["key"] == "van_2_initial_charge_pct" else control
    for control in _GO_HANDOFF["controls"]
])


def _recording(values):
    require(_GO_HANDOFF["decision"] == "GO", "The example backend handoff did not receive a GO.")
    final_result = _GO_HANDOFF["results"]
    requested = applied_values(RECORDED_CONTROLS, final_result["controls"], values)
    if final_result["controls"] == requested:
        return final_result, "handoff.json", "Final GO handoff"

    test = _read_json("test.json")
    semantics = test["semantics"]
    require(
        semantics["verdict"] == "matches" and not semantics["issues"],
        "The backend test recordings did not pass semantic verification.",
    )
    for run in test["runs"]:
        result = run["results"]
        if (
            run["exit_code"] == 0 and not run["timed_out"] and not run["issues"]
            and result is not None and result["controls"] == requested
        ):
            return result, "test.json", "Verified backend test run"
    raise ValueError("No verified backend recording matches the requested controls.")


def _clock_minutes(clock):
    time = datetime.strptime(clock, "%I:%M %p")
    return time.hour * 60 + time.minute


def _point(coordinates):
    return [int(axis) for axis in coordinates.strip("()").split(",")]


def _charger_slots(given, bookings, start):
    slots = []
    for slot, option in bookings.items():
        number = slot.removeprefix("slot_")
        opens, closes = given[f"Charger slot {number}"].split("–")
        if len(opens.split()) == 1:
            opens += " " + closes.split()[-1]
        slots.append({
            "id": slot,
            "start_min": _clock_minutes(opens) - start,
            "end_min": _clock_minutes(closes) - start,
            "option_id": option,
        })
    return slots


def _replay_event(leg, delivery_id):
    kind = leg["leg_type"]
    event = {
        "type": kind,
        "start_min": leg["start_min"],
        "end_min": leg["end_min"],
        "position": leg["start_coord"],
        "battery_start_kwh": leg["soc_start_kwh"],
        "battery_end_kwh": leg["soc_end_kwh"],
    }
    if kind == "drive":
        event["path"] = leg["path"]
    if kind in ("wait", "charge"):
        event["charger_id"] = "C1"
    if kind == "charge":
        event["energy_kwh"] = leg["energy_delta_kwh"]
    if kind == "deliver":
        event["delivery_id"] = delivery_id
    return event


def _replay(solution, counts):
    given = {item["name"]: item["value"] for item in _PROBLEM["given_data"]}
    dimensions = given["Grid dimensions"].split(",", 1)[0]
    width, height = map(int, dimensions.split("x"))
    start = _clock_minutes(given["Depot departure time"])
    vans, deliveries = [], []
    for selected in solution["vans"]:
        number = selected["van_id"]
        delivery_id = f"D{number}"
        deliveries.append({
            "id": delivery_id,
            "position": _point(given[f"Van {number} delivery destination"]),
            "deadline_min": _clock_minutes(given[f"Van {number} delivery deadline"]) - start,
        })
        vans.append({
            "id": f"V{number}",
            "name": selected["van_name"],
            "option_id": selected["option_id"],
            "mode": selected["mode"],
            "initial_battery_kwh": selected["initial_soc_kwh"],
            "delivery_ids": [delivery_id],
            "events": [_replay_event(leg, delivery_id) for leg in selected["legs"]],
        })

    return {
        "kind": "ev_fleet",
        "grid": {"width": width, "height": height, "block_km": float(given["Block length"]) / 1000},
        "depot": _point(given["Depot location"]),
        "chargers": [{
            "id": "C1",
            "position": _point(given["Charger location"]),
            "slots": _charger_slots(given, solution["charger_bookings"], start),
        }],
        "deliveries": deliveries,
        "start_hour": start / 60,
        "duration_min": _clock_minutes(given["Depot return deadline"]) - start,
        "battery_capacity_kwh": float(given["Van battery capacity"]),
        "reserve_pct": float(given["Minimum allowable battery state of charge"]),
        "vans": vans,
        "counts": counts,
    }


def recorded_handoff(values, previous=None):
    """Load a verified recorded result for the requested settings."""
    recorded, filename, source_label = _recording(values)
    workspace_id = _GO_HANDOFF["workspace_id"]
    identity = {
        "run_id": previous["run_id"] if previous is not None else str(uuid4()),
        "plan_id": previous["plan_id"] if previous is not None else workspace_id,
        "attempt_id": str(uuid4()),
    }
    return project_ev_result(
        recorded, identity, provenance="recorded",
        explanation=f"{source_label} from workspace {workspace_id}.",
        source={"workspace_id": workspace_id, "file": filename, "label": source_label},
    )


def project_ev_result(output, identity, *, provenance, explanation, source=None):
    """Map the checked solver output to the existing EV display contract."""
    require(
        isinstance(output, dict) and output.get("status") == "ok" and output.get("error") is None,
        "The EV solver run did not succeed.",
    )
    solution = output["solution"]
    verification = solution["verification"]
    require(
        verification["feasible"] is True and not verification["violations"],
        "The EV schedule did not pass feasibility checks.",
    )
    cost = solution["cost_breakdown"]
    quantum = output["quantum"]
    simulation = _replay(solution, quantum["counts"])
    return {
        "schema_version": 1,
        **identity,
        "scene_type": "ev_fleet",
        "problem": {
            "title": _GO_HANDOFF["problem"]["title"],
            "statement": _GO_HANDOFF["problem"]["summary"],
            "original_prompt": RECORDED_PROMPT,
        },
        "controls": RECORDED_CONTROLS,
        "result": {
            **identity,
            "status": "verified",
            "provenance": provenance,
            **({"source": source} if source is not None else {}),
            "effective_values": output["controls"],
            "simulation": simulation,
            "evaluation": {
                "feasible": verification["feasible"],
                "objective_value": output["objective"]["value"],
                "reference_value": output["reference"]["objective_value"],
                "gap": verification["optimality_gap_usd"],
                "costs": {
                    "driving": cost["driving_cost"],
                    "electricity": cost["electricity_cost"],
                    "waiting": cost["waiting_cost"],
                    "late": cost["late_penalty"],
                },
                "delivered": sum(
                    event["type"] == "deliver" for van in simulation["vans"] for event in van["events"]
                ),
                "selected_options": solution["selected_options"],
                "late_minutes": {f'D{van["van_id"]}': van["late_minutes"] for van in solution["vans"]},
            },
            "quantum": {
                **{name: value for name, value in quantum.items() if name != "counts"},
                "binary_variables": quantum["decision_qubits"],
                "actual_qubits": quantum["qubits"],
            },
            "assumptions": _PROBLEM["assumptions"],
            "explanation": explanation,
        },
    }
