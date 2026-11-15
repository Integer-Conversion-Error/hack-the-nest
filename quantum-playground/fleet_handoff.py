"""Validate the backend's verified EV handoff before exposing a replay to the browser."""

import math
import re


class InvalidHandoff(ValueError):
    pass


class UnverifiedResult(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidHandoff(message)


def is_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def normalize_controls(controls):
    require(isinstance(controls, list), "The handoff must include approved controls.")
    normalized = []
    seen = set()
    for control in controls:
        require(isinstance(control, dict), "Invalid control definition.")
        key = control.get("key", control.get("id"))
        choices = control.get("allowed_values")
        default = control.get("default")
        require(
            isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key) and key not in seen
            and is_text(control.get("label")) and isinstance(choices, list) and choices
            and all(is_number(value) for value in choices) and len(set(choices)) == len(choices)
            and is_number(default) and default in choices
            and control.get("rerun_mode") in ("bind", "rebuild", "resample")
            and isinstance(control.get("unit", ""), str),
            "The EV dashboard expects unique numeric enum controls with an allowed default.",
        )
        normalized.append({
            "id": key,
            "label": control["label"],
            "allowed_values": choices,
            "default": default,
            "unit": control.get("unit", ""),
            "rerun_mode": control["rerun_mode"],
        })
        seen.add(key)
    return normalized


def applied_values(controls, values, overrides):
    if not isinstance(overrides, dict):
        raise ValueError("Control values must be a JSON object.")
    if set(overrides) - {control["id"] for control in controls}:
        raise ValueError("Unknown plan control.")
    applied = {}
    for control in controls:
        key = control["id"]
        value = overrides.get(key, values[key])
        choices = control["allowed_values"]
        if not is_number(value) or value not in choices:
            raise ValueError(f"Invalid value for {control['label']}.")
        applied[key] = choices[choices.index(value)]
    return applied


def named_items(items, label):
    require(isinstance(items, list), f"The scene must include a {label} list.")
    by_id = {}
    for item in items:
        require(isinstance(item, dict) and is_text(item.get("id")), f"Invalid {label} ID.")
        key = item["id"]
        require(key not in by_id, f"Duplicate {label} ID: {key}.")
        by_id[key] = item
    return by_id


def validate_replay(sim):
    require(isinstance(sim, dict) and sim.get("kind") == "ev_fleet", "Missing EV fleet scene.")
    grid = sim.get("grid")
    require(
        isinstance(grid, dict)
        and all(type(grid.get(axis)) is int and 2 <= grid[axis] <= 50 for axis in ("width", "height"))
        and is_number(grid.get("block_km")) and grid["block_km"] > 0,
        "Invalid EV grid.",
    )
    duration = sim.get("duration_min")
    capacity = sim.get("battery_capacity_kwh")
    reserve = sim.get("reserve_pct")
    start_hour = sim.get("start_hour")
    require(
        is_number(duration) and duration > 0 and is_number(capacity) and capacity > 0
        and is_number(reserve) and 0 <= reserve <= 100 and is_number(start_hour) and 0 <= start_hour < 24,
        "Invalid operating hours or battery settings.",
    )

    def point(value):
        return (
            isinstance(value, list) and len(value) == 2
            and all(type(coordinate) is int for coordinate in value)
            and 0 <= value[0] < grid["width"] and 0 <= value[1] < grid["height"]
        )

    depot = sim.get("depot")
    chargers = named_items(sim.get("chargers"), "charger")
    deliveries = named_items(sim.get("deliveries"), "delivery")
    vans = named_items(sim.get("vans"), "van")
    require(1 <= len(vans) <= 4, "The EV scene supports one to four vans.")
    require(
        point(depot) and all(point(item.get("position")) for item in (*chargers.values(), *deliveries.values())),
        "A scene entity is outside the grid.",
    )
    require(
        all(is_number(delivery.get("deadline_min")) and delivery["deadline_min"] >= 0
            for delivery in deliveries.values()),
        "Invalid delivery deadline.",
    )
    delivered = set()
    for van_id, van in vans.items():
        battery = van.get("initial_battery_kwh")
        required = van.get("delivery_ids")
        events = van.get("events")
        require(
            is_number(battery) and 0 <= battery <= capacity and isinstance(events, list)
            and isinstance(required, list) and all(is_text(key) and key in deliveries for key in required)
            and len(set(required)) == len(required),
            f"Invalid assignment for {van_id}.",
        )
        position = depot
        previous_end = 0
        van_delivered = set()
        for event in events:
            require(isinstance(event, dict), f"Invalid event for {van_id}.")
            kind = event.get("type")
            start, end = event.get("start_min"), event.get("end_min")
            battery_start, battery_end = event.get("battery_start_kwh"), event.get("battery_end_kwh")
            require(
                kind in ("drive", "wait", "charge", "deliver") and event.get("position") == position
                and is_number(start) and is_number(end) and previous_end <= start <= end <= duration
                and (start < end or kind == "deliver") and is_number(battery_start)
                and is_number(battery_end) and math.isclose(battery_start, battery, abs_tol=1e-7)
                and -1e-7 <= battery_end <= capacity + 1e-7,
                f"Inconsistent event timing, position, or battery for {van_id}.",
            )
            if kind == "drive":
                path = event.get("path")
                require(
                    isinstance(path, list) and len(path) >= 2 and path[0] == position and all(map(point, path))
                    and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(path, path[1:])),
                    f"Invalid street path for {van_id}.",
                )
                position = path[-1]
            if kind == "charge" or (kind == "wait" and "charger_id" in event):
                key = event.get("charger_id")
                require(
                    is_text(key) and key in chargers and chargers[key]["position"] == position,
                    f"Unknown charger or charger position for {van_id}.",
                )
            if kind == "deliver":
                key = event.get("delivery_id")
                require(
                    is_text(key) and key in required and deliveries[key]["position"] == position
                    and key not in delivered,
                    f"Unknown or repeated delivery for {van_id}.",
                )
                delivered.add(key)
                van_delivered.add(key)
            previous_end, battery = end, battery_end
        require(van_delivered == set(required), f"Missing scheduled delivery for {van_id}.")
    return len(delivered)


def normalize_handoff(handoff, mode, prompt, previous=None, expected_values=None):
    require(isinstance(handoff, dict) and type(handoff.get("schema_version")) is int
            and handoff["schema_version"] == 1, "Expected a VisualizationHandoff with schema_version 1.")
    result = handoff.get("result")
    require(isinstance(result, dict), "Missing RunResult in the handoff.")
    identity = {key: handoff.get(key) for key in ("run_id", "plan_id", "attempt_id")}
    require(
        all(is_text(value) and result.get(key) == value for key, value in identity.items()),
        "The handoff and result IDs do not match.",
    )
    if previous is not None:
        require(
            identity["run_id"] == previous["run_id"] and identity["plan_id"] == previous["plan_id"]
            and identity["attempt_id"] != previous["attempt_id"],
            "Recalculation must return a new attempt for the same run and plan.",
        )
    status, provenance = result.get("status"), result.get("provenance")
    if mode == "live":
        if status != "verified":
            message = result.get("message")
            raise UnverifiedResult(message if is_text(message) else "The backend did not verify a result.")
        require(provenance in ("live", "recorded"), "Missing live or recorded provenance.")
    else:
        require(
            status == "reference" and provenance == "classical_reference",
            "Invalid classical-reference provenance.",
        )
    require(handoff.get("scene_type") == "ev_fleet", "Unsupported scene type. The dashboard expects an EV fleet.")

    problem = handoff.get("problem")
    require(
        isinstance(problem, dict) and is_text(problem.get("title")) and is_text(problem.get("statement"))
        and problem.get("original_prompt") == prompt,
        "The handoff problem does not match the submitted prompt.",
    )
    controls = normalize_controls(handoff.get("controls"))
    if previous is not None:
        require(controls == previous["variables"], "Approved controls changed for the same plan.")
    defaults = {control["id"]: control["default"] for control in controls}
    effective = result.get("effective_values")
    require(
        isinstance(effective, dict) and set(effective) == set(defaults)
        and all(is_number(effective[c["id"]]) and effective[c["id"]] in c["allowed_values"] for c in controls)
        and effective == (defaults if expected_values is None else expected_values),
        "The result's effective values do not match the requested settings.",
    )

    simulation = result.get("simulation")
    delivered_count = validate_replay(simulation)
    evaluation = result.get("evaluation")
    require(
        isinstance(evaluation, dict) and evaluation.get("feasible") is True
        and is_number(evaluation.get("objective_value"))
        and isinstance(evaluation.get("costs"), dict)
        and all(is_number(cost) for cost in evaluation["costs"].values())
        and type(evaluation.get("delivered")) is int and evaluation["delivered"] == delivered_count,
        "The handoff is missing a feasible evaluation matching the schedule.",
    )
    circuits = result.get("circuits", [])
    quantum = result.get("quantum", {})
    assumptions = result.get("assumptions", [])
    explanation = result.get("explanation", "")
    counts = simulation.get("counts", {})
    require(
        isinstance(circuits, list) and all(isinstance(circuit, dict) and is_text(circuit.get("drawing"))
                                          for circuit in circuits)
        and isinstance(quantum, dict) and isinstance(assumptions, list) and all(map(is_text, assumptions))
        and isinstance(explanation, str) and isinstance(counts, dict)
        and all(re.fullmatch(r"[01]+", bits) and type(count) is int and count >= 0 for bits, count in counts.items()),
        "Invalid circuit, measurement, or explanatory data.",
    )
    return {
        **identity,
        "mode": mode,
        "status": "completed",
        "provenance": provenance,
        "problem": problem,
        "variables": controls,
        "effective_values": effective,
        "simulation": simulation,
        "evaluation": evaluation,
        "circuits": circuits,
        "quantum": quantum,
        "assumptions": assumptions,
        "explanation": explanation,
    }
