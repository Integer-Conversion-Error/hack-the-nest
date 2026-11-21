"""Canonical, pipeline-owned inputs for the interactive fleet scenario builder.

This module deliberately uses only the standard library.  Bindings are generated
by the host from stable IDs; a client or generated solver cannot supply a path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from fractions import Fraction

SCHEMA_VERSION = 1
MAX_SCENARIO_BYTES = 65_536
_TOL = 1e-6


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, fields, label):
    _require(isinstance(value, dict), f"{label} must be an object.")
    _require(set(value) == set(fields), f"{label} must contain exactly: {', '.join(fields)}.")
    return value


def _number(value, label, minimum=None, maximum=None, *, positive=False, integer=False):
    _require(type(value) in (int, float) and (not integer or type(value) is int),
             f"{label} must be {'an integer' if integer else 'a number'}.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, f"{label} must be finite.")
    _require(not positive or value > 0, f"{label} must be greater than zero.")
    _require(minimum is None or value >= minimum, f"{label} is below its minimum {minimum}.")
    _require(maximum is None or value <= maximum, f"{label} exceeds its maximum {maximum}.")
    # The same numeric input has the same canonical digest, whether decoded as
    # 20 or 20.0.  Avoid negative zero as a distinct serialization as well.
    return int(value) if value == int(value) else float(value)


def _items(value, label, minimum, maximum):
    _require(isinstance(value, list) and minimum <= len(value) <= maximum,
             f"{label} must contain {minimum}–{maximum} items.")
    return value


def _identifier(value, prefix, label):
    _require(isinstance(value, str) and re.fullmatch(prefix + r"[1-9][0-9]{0,5}", value),
             f"{label} must use a stable ID such as {prefix}1.")
    return value


def _sort(items):
    return sorted(items, key=lambda item: int(item["id"][1:]))


def _unique(items, label):
    _require(len({item["id"] for item in items}) == len(items), f"Duplicate {label} ID.")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize_scenario(data):
    """Validate a versioned scenario and return a detached canonical value."""
    if isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 2:
        from geographic import normalize_geographic_scenario

        return normalize_geographic_scenario(data)
    _object(data, ("schema_version", "title", "grid", "depots", "chargers", "deliveries", "vehicles",
                   "fleet", "timing", "costs"), "Scenario")
    _require(type(data["schema_version"]) is int and data["schema_version"] == SCHEMA_VERSION,
             "Unsupported scenario schema version.")
    title = data["title"]
    _require(isinstance(title, str) and 1 <= len(title.strip()) <= 120, "Title must contain 1–120 characters.")
    _require(not any(ord(char) < 32 for char in title), "Title cannot contain control characters.")
    grid = _object(data["grid"], ("width", "height", "block_km"), "Grid")
    grid = {"width": _number(grid["width"], "Grid width", 2, 50, integer=True),
            "height": _number(grid["height"], "Grid height", 2, 50, integer=True),
            "block_km": _number(grid["block_km"], "Block length", positive=True)}

    def point(value, label):
        _require(isinstance(value, list) and len(value) == 2, f"{label} must be an [x, y] intersection.")
        return [_number(value[0], label + " x", 0, grid["width"] - 1, integer=True),
                _number(value[1], label + " y", 0, grid["height"] - 1, integer=True)]

    fleet = _object(data["fleet"], ("battery_capacity_kwh", "reserve_pct", "energy_use_kwh_per_km"), "Fleet")
    fleet = {"battery_capacity_kwh": _number(fleet["battery_capacity_kwh"], "Battery capacity", positive=True),
             "reserve_pct": _number(fleet["reserve_pct"], "Battery reserve", 0, 100),
             "energy_use_kwh_per_km": _number(fleet["energy_use_kwh_per_km"], "Energy use", positive=True)}
    timing = _object(data["timing"], ("start_hour", "duration_min", "speed_kmh"), "Timing")
    timing = {"start_hour": _number(timing["start_hour"], "Start hour", 0),
              "duration_min": _number(timing["duration_min"], "Shift duration", 1, 1440),
              "speed_kmh": _number(timing["speed_kmh"], "Driving speed", positive=True)}
    _require(timing["start_hour"] < 24, "Start hour must be before 24:00.")
    costs = _object(data["costs"], ("driving_per_km", "electricity_per_kwh", "waiting_per_hour", "late_per_hour"), "Costs")
    costs = {key: _number(value, key.replace("_", " "), 0) for key, value in costs.items()}
    depots = []
    for entry in _items(data["depots"], "Depots", 1, 6):
        _object(entry, ("id", "position"), "Depot")
        key = _identifier(entry["id"], "P", "Depot ID")
        depots.append({"id": key, "position": point(entry["position"], key)})
    _unique(depots, "depot")
    depot_ids = {entry["id"] for entry in depots}
    vehicles = []
    for entry in _items(data["vehicles"], "Vehicles", 1, 6):
        _object(entry, ("id", "depot_id", "initial_charge_pct"), "Vehicle")
        key = _identifier(entry["id"], "V", "Vehicle ID")
        _require(isinstance(entry["depot_id"], str) and entry["depot_id"] in depot_ids, f"Unknown home depot for {key}.")
        vehicles.append({"id": key, "depot_id": entry["depot_id"],
                         "initial_charge_pct": _number(entry["initial_charge_pct"], key + " starting charge", 0, 100)})
    _unique(vehicles, "vehicle")
    vehicle_ids = {entry["id"] for entry in vehicles}
    deliveries = []
    assignments = {key: 0 for key in vehicle_ids}
    for entry in _items(data["deliveries"], "Deliveries", 1, 60):
        _object(entry, ("id", "position", "vehicle_id", "deadline_min", "service_min"), "Delivery")
        key = _identifier(entry["id"], "D", "Delivery ID")
        _require(isinstance(entry["vehicle_id"], str) and entry["vehicle_id"] in vehicle_ids, f"Unknown vehicle for {key}.")
        assignments[entry["vehicle_id"]] += 1
        deliveries.append({"id": key, "position": point(entry["position"], key), "vehicle_id": entry["vehicle_id"],
                           "deadline_min": _number(entry["deadline_min"], key + " deadline", 0),
                           "service_min": _number(entry["service_min"], key + " service time", 0)})
    _unique(deliveries, "delivery")
    _require(all(count <= 10 for count in assignments.values()), "Each vehicle supports at most 10 delivery stops.")
    chargers = []
    for entry in _items(data["chargers"], "Chargers", 0, 6):
        _object(entry, ("id", "position", "power_kw", "slots"), "Charger")
        key = _identifier(entry["id"], "C", "Charger ID")
        slots = []
        for slot in _items(entry["slots"], key + " slots", 0, 4):
            _object(slot, ("id", "start_min", "end_min"), "Charging slot")
            slot_id = _identifier(slot["id"], "S", "Slot ID")
            start = _number(slot["start_min"], key + " " + slot_id + " start", 0, timing["duration_min"])
            end = _number(slot["end_min"], key + " " + slot_id + " end", 0, timing["duration_min"])
            _require(start < end, f"{key} {slot_id} must end after it starts.")
            slots.append({"id": slot_id, "start_min": start, "end_min": end})
        _unique(slots, key + " slot")
        chronological = sorted(slots, key=lambda slot: slot["start_min"])
        _require(all(a["end_min"] <= b["start_min"] for a, b in zip(chronological, chronological[1:])),
                 f"{key} charging slots cannot overlap.")
        chargers.append({"id": key, "position": point(entry["position"], key),
                         "power_kw": _number(entry["power_kw"], key + " power", positive=True), "slots": _sort(slots)})
    _unique(chargers, "charger")
    result = {"schema_version": SCHEMA_VERSION, "title": title.strip(), "grid": grid,
              "depots": _sort(depots), "chargers": _sort(chargers), "deliveries": _sort(deliveries),
              "vehicles": _sort(vehicles), "fleet": fleet, "timing": timing, "costs": costs}
    try:
        size = len(_canonical(result).encode("utf-8"))
    except UnicodeError:
        raise ValueError("Scenario must contain valid Unicode text.") from None
    _require(size <= MAX_SCENARIO_BYTES, "Scenario exceeds the 64 KB input limit.")
    return result


def scenario_digest(scenario):
    """Digest canonical source data, independent of object/list ordering."""
    return hashlib.sha256(_canonical(normalize_scenario(scenario)).encode("utf-8")).hexdigest()


def numeric_bindings(scenario):
    """Return allowed control keys and logical paths addressed by entity ID."""
    scenario = normalize_scenario(scenario)
    bindings = {
        "battery_capacity_kwh": ["fleet", "battery_capacity_kwh"],
        "minimum_reserve_pct": ["fleet", "reserve_pct"],
        "energy_use_kwh_per_km": ["fleet", "energy_use_kwh_per_km"],
        "driving_speed_km_per_hour": ["timing", "speed_kmh"],
        "start_hour": ["timing", "start_hour"],
        "shift_duration_minutes": ["timing", "duration_min"],
        "driving_cost_per_km": ["costs", "driving_per_km"],
        "electricity_price_per_kwh": ["costs", "electricity_per_kwh"],
        "waiting_cost_per_hour": ["costs", "waiting_per_hour"],
        "late_delivery_cost_per_hour": ["costs", "late_per_hour"],
    }
    if scenario["schema_version"] == 2:
        # Geographic travel times come from the immutable road-routing table.
        bindings.pop("driving_speed_km_per_hour")
    for vehicle in scenario["vehicles"]:
        bindings[f"van_{vehicle['id'][1:]}_initial_charge_pct"] = ["vehicles", vehicle["id"], "initial_charge_pct"]
    for delivery in scenario["deliveries"]:
        for field in ("deadline_min", "service_min"):
            bindings[f"delivery_{delivery['id'].lower()}_{field}"] = ["deliveries", delivery["id"], field]
    for charger in scenario["chargers"]:
        prefix = "charger_" + charger["id"].lower()
        bindings[prefix + "_power_kw"] = ["chargers", charger["id"], "power_kw"]
        for slot in charger["slots"]:
            for field in ("start_min", "end_min"):
                bindings[prefix + "_slot_" + slot["id"].lower() + "_" + field] = [
                    "chargers", charger["id"], "slots", slot["id"], field]
    return dict(sorted(bindings.items()))


def _target(scenario, path):
    current = scenario
    for component in path[:-1]:
        if isinstance(current, list):
            current = next(item for item in current if item["id"] == component)
        else:
            current = current[component]
    return current, path[-1]


def _control_value(control, value):
    kind = control.get("type")
    value = _number(value, "Control value", integer=kind == "integer")
    choices = control.get("allowed_values")
    if choices is not None:
        _require(isinstance(choices, list) and choices and all(type(item) in (int, float) for item in choices),
                 "Numeric controls require numeric choices.")
        for choice in choices:
            _number(choice, "Control choice", integer=kind == "integer")
        _require(value in choices, "Control value is not an approved choice.")
    else:
        minimum = _number(control.get("minimum"), "Control minimum")
        maximum = _number(control.get("maximum"), "Control maximum")
        _require(minimum <= value <= maximum, "Control value is outside its approved range.")
        step = control.get("step")
        if step is not None:
            step = _number(step, "Control step", positive=True)
            _require((Fraction(str(value)) - Fraction(str(minimum))) % Fraction(str(step)) == 0,
                     "Control value does not align with its approved step.")
    return value


def effective_scenario(base, controls, values):
    """Overlay approved numeric values on immutable source defaults and validate."""
    base = normalize_scenario(base)
    _require(isinstance(controls, list) and isinstance(values, dict), "Controls and values must be a list and object.")
    bindings = numeric_bindings(base)
    result = copy.deepcopy(base)
    seen = set()
    for control in controls:
        _require(isinstance(control, dict), "Invalid scenario control.")
        key = control.get("id", control.get("key"))
        _require(isinstance(key, str) and key in bindings and key not in seen, "Unknown or repeated scenario numeric control.")
        _require(control.get("type") in ("integer", "number"), "Scenario controls must be numeric.")
        source, field = _target(base, bindings[key])
        default = _control_value(control, control.get("default"))
        _require(default == source[field], f"The approved default for {key} differs from the source scenario.")
        target, field = _target(result, bindings[key])
        target[field] = _control_value(control, values.get(key, default))
        seen.add(key)
    _require(not set(values) - seen, "Unknown scenario control value.")
    return normalize_scenario(result)


def scenario_context(base, controls, values):
    """Host-produced metadata describing exactly the scenario an attempt used."""
    effective = effective_scenario(base, controls, values)
    bindings = numeric_bindings(base)
    approved = {control.get("id", control.get("key")) for control in controls}
    return {"base_digest": scenario_digest(base), "effective_digest": scenario_digest(effective),
            "effective_scenario": effective, "bindings": {key: path for key, path in bindings.items() if key in approved}}


def example_scenario():
    """A feasible two-vehicle example that exercises multiple stops on one van."""
    return normalize_scenario({
        "schema_version": 1, "title": "Two depots, three deliveries",
        "grid": {"width": 6, "height": 6, "block_km": 0.5},
        "depots": [{"id": "P1", "position": [2, 2]}, {"id": "P2", "position": [4, 4]}],
        "chargers": [{"id": "C1", "position": [2, 1], "power_kw": 10,
                      "slots": [{"id": "S1", "start_min": 30, "end_min": 60},
                                {"id": "S2", "start_min": 60, "end_min": 90}]},
                     {"id": "C2", "position": [4, 3], "power_kw": 10,
                      "slots": [{"id": "S1", "start_min": 30, "end_min": 60},
                                {"id": "S2", "start_min": 60, "end_min": 90}]}],
        "deliveries": [{"id": "D1", "position": [0, 0], "vehicle_id": "V1", "deadline_min": 60, "service_min": 5},
                       {"id": "D2", "position": [1, 4], "vehicle_id": "V1", "deadline_min": 90, "service_min": 5},
                       {"id": "D3", "position": [5, 0], "vehicle_id": "V2", "deadline_min": 90, "service_min": 5}],
        "vehicles": [{"id": "V1", "depot_id": "P1", "initial_charge_pct": 40},
                     {"id": "V2", "depot_id": "P2", "initial_charge_pct": 35}],
        "fleet": {"battery_capacity_kwh": 20, "reserve_pct": 15, "energy_use_kwh_per_km": 0.3},
        "timing": {"start_hour": 8, "duration_min": 120, "speed_kmh": 30},
        "costs": {"driving_per_km": 0.3, "electricity_per_kwh": 0.2, "waiting_per_hour": 25, "late_per_hour": 60},
    })


def scenario_prompt(scenario):
    """Create the complete deterministic request for the existing native pipeline."""
    scenario = normalize_scenario(scenario)
    if scenario["schema_version"] == 2:
        from geographic import geographic_prompt

        return geographic_prompt(scenario)
    prompt = """Solve this interactive EV fleet scenario with measured QAOA and visualize the verified result.
The complete JSON below is authoritative and is also saved as read-only scenario.json.
Preserve every entity ID, position, assigned vehicle, home depot, and numeric setting.
Each vehicle starts at its assigned depot at elapsed minute zero and returns to that
same depot before the shift ends. Deliver every assigned stop exactly once; the
solver chooses stop ordering. Idle vehicles may remain at home. Follow shortest
grid paths between visits, with actual fractional driving times and battery use.
Maintain the shared reserve throughout every route and cap batteries at capacity.
Delivery service takes its own service_min; lateness starts at deadline_min and is
measured at service completion. Delivery deadlines are soft; the shift end is hard.
Chargers are single-plug. A charging visit occupies one complete configured slot,
arriving by its start; model any waiting explicitly. Slots are half-open intervals.
Charge may occur between deliveries, and repeated charger visits are allowed when
needed. Added energy is min(power_kw * slot duration / 60, remaining capacity).
Use bounded route search and candidate generation, including several orders and
charging choices; do not enumerate all permutations of ten stops. Use QAOA to
select among feasible candidates and keep measured decisions separate from a
classical reference over the same candidates. Never replace a failed measurement
with the classical answer. Bound circuits and candidate menus within the existing
worker budget. Disclose heuristic pruning and limit optimality claims to the
retained candidate set; report unsupported when no valid bounded design fits.
Minimize USD driving_per_km for all driven distance, electricity_per_kwh for energy
actually charged, waiting_per_hour for explicit waits, and late_per_hour for each
delivery's late minutes; prorate hourly costs by elapsed minutes / 60.
Independently verify the complete timed route, battery trajectory, slot bookings,
delivery coverage, depot return, and costs using the exact effective inputs.
Emit simulation.depots [{id,position}] and simulation.vans with depot_id,
initial_battery_kwh, delivery_ids, and chronological contiguous events beginning
at zero. Keep the existing grid, chargers, deliveries, start_hour, duration_min,
battery_capacity_kwh and reserve_pct replay fields. Omit zero-distance drives.
Charge events identify charger_id and may identify slot_id. Report cost_breakdown
components driving_cost, electricity_cost, waiting_cost, late_penalty in USD.
Include reference.simulation when a feasible classical schedule is computed.
You may approve a small useful subset of numeric controls from the registry below.
Use its exact keys; defaults MUST equal the corresponding source values. Do not
invent controls for coordinates, grid geometry, IDs, entity counts, assignments,
or any other unregistered field. Apply effective values in both solver and verifier.

Authoritative scenario JSON:
""" + _canonical(scenario) + "\n\nHost numeric control registry (paths use entity IDs, not array indexes):\n" + _canonical(numeric_bindings(scenario))
    _require(len(prompt.encode("utf-8")) <= MAX_SCENARIO_BYTES, "Generated scenario prompt exceeds 64 KB.")
    return prompt


def _close(actual, expected, label):
    actual = _number(actual, label)
    _require(math.isclose(actual, expected, rel_tol=1e-8, abs_tol=_TOL), f"{label} does not match the effective scenario.")


def _entities(items, label):
    _require(isinstance(items, list), f"Missing {label} list in the result.")
    result = {}
    for item in items:
        _require(isinstance(item, dict) and isinstance(item.get("id"), str), f"Invalid result {label}.")
        _require(item["id"] not in result, f"Duplicate result {label} ID.")
        result[item["id"]] = item
    return result


def _verify_simulation(scenario, simulation, label, routing=None):
    """Recompute physical feasibility and all cost components independently."""
    _require(isinstance(simulation, dict) and simulation.get("kind") == "ev_fleet", f"Missing {label} EV simulation.")
    geographic = scenario["schema_version"] == 2
    if geographic:
        _require(isinstance(routing, dict), "A geographic replay requires its saved routing table.")
        replay_map = simulation.get("map")
        _require(isinstance(replay_map, dict) and replay_map.get("kind") == "geographic"
                 and replay_map.get("profile") == "driving" and replay_map.get("routing_digest") == routing["digest"]
                 and "grid" not in simulation, f"{label} geographic map does not match the routing snapshot.")
        node_indexes = {node["id"]: index for index, node in enumerate(routing["nodes"])}
        nodes = {node["id"]: node for node in routing["nodes"]}
        # Entity/event positions in geographic replays are road-snapped points;
        # the immutable scenario retains the originally requested map pins.
        scenario = copy.deepcopy(scenario)
        for name in ("depots", "chargers", "deliveries"):
            for item in scenario[name]:
                item["position"] = nodes[item["id"]]["snapped_position"]
    else:
        grid = simulation.get("grid")
        _require(isinstance(grid, dict), f"Missing {label} grid.")
        for field, value in scenario["grid"].items():
            _close(grid.get(field), value, label + " grid " + field)
    for field in ("start_hour", "duration_min"):
        _close(simulation.get(field), scenario["timing"][field], label + " " + field)
    for field in ("battery_capacity_kwh", "reserve_pct"):
        _close(simulation.get(field), scenario["fleet"][field], label + " " + field)
    source = {name: {item["id"]: item for item in scenario[name]}
              for name in ("depots", "chargers", "deliveries", "vehicles")}
    for name in ("depots", "chargers", "deliveries"):
        actual = _entities(simulation.get(name), label + " " + name)
        _require(set(actual) == set(source[name]), f"{label} changed or omitted {name}.")
        for key, item in source[name].items():
            position = actual[key].get("position")
            if geographic:
                from geographic import geographic_point

                geographic_point(position)
                # Source pins are rounded for stable input identity, while
                # provider snaps retain their original road-coordinate precision.
                _require(position == item["position"], f"{label} moved {key}.")
            else:
                _require(isinstance(position, list) and all(type(value) is int for value in position)
                         and position == item["position"], f"{label} moved {key}.")
            if name == "deliveries":
                _close(actual[key].get("deadline_min"), item["deadline_min"], label + " " + key + " deadline")
    vans = _entities(simulation.get("vans"), label + " vans")
    _require(set(vans) == set(source["vehicles"]), f"{label} changed or omitted vehicles.")
    capacity = scenario["fleet"]["battery_capacity_kwh"]
    reserve = capacity * scenario["fleet"]["reserve_pct"] / 100
    duration = scenario["timing"]["duration_min"]
    costs = {"driving_cost": 0.0, "electricity_cost": 0.0, "waiting_cost": 0.0, "late_penalty": 0.0}
    bookings = {key: [] for key in source["chargers"]}
    delivered = set()

    def point(value):
        if geographic:
            try:
                from geographic import geographic_point

                geographic_point(value)
                return True
            except ValueError:
                return False
        return (isinstance(value, list) and len(value) == 2 and all(type(item) is int for item in value)
                and 0 <= value[0] < scenario["grid"]["width"] and 0 <= value[1] < scenario["grid"]["height"])

    for key, vehicle in source["vehicles"].items():
        van = vans[key]
        _require(van.get("depot_id") == vehicle["depot_id"], f"{label} changed {key}'s depot.")
        home = source["depots"][vehicle["depot_id"]]["position"]
        position = home
        battery = capacity * vehicle["initial_charge_pct"] / 100
        _close(van.get("initial_battery_kwh"), battery, label + " " + key + " initial battery")
        _require(battery + _TOL >= reserve, f"{label} {key} starts below its battery reserve.")
        assigned = {item["id"] for item in scenario["deliveries"] if item["vehicle_id"] == key}
        required = van.get("delivery_ids")
        _require(isinstance(required, list) and all(isinstance(item, str) for item in required)
                 and len(required) == len(set(required)) and set(required) == assigned,
                 f"{label} changed or omitted {key}'s delivery assignments.")
        events = van.get("events")
        _require(isinstance(events, list), f"Missing {label} events for {key}.")
        previous_end = 0.0
        van_delivered = set()
        for event in events:
            _require(isinstance(event, dict), f"Invalid {label} event for {key}.")
            kind = event.get("type")
            _require(kind in ("drive", "wait", "charge", "deliver"), f"Invalid {label} event type.")
            start = _number(event.get("start_min"), label + " event start", 0, duration)
            end = _number(event.get("end_min"), label + " event end", 0, duration)
            _close(start, previous_end, label + " contiguous event timing")
            _require(start <= end and (start < end or kind == "deliver"), f"Invalid {label} event duration.")
            _require(point(event.get("position")) and event["position"] == position, f"Inconsistent {label} event position.")
            _close(event.get("battery_start_kwh"), battery, label + " event starting battery")
            battery_end = _number(event.get("battery_end_kwh"), label + " event ending battery")
            event_duration = end - start
            expected_battery = battery
            if kind == "drive":
                if geographic:
                    from_id, to_id = event.get("from_id"), event.get("to_id")
                    _require(isinstance(from_id, str) and isinstance(to_id, str)
                             and from_id in node_indexes and to_id in node_indexes,
                             f"Unknown {label} geographic drive endpoints.")
                    _require(nodes[from_id]["snapped_position"] == position,
                             f"Inconsistent {label} geographic drive origin.")
                    origin, destination = node_indexes[from_id], node_indexes[to_id]
                    distance_m = routing["distances_m"][origin][destination]
                    duration_s = routing["durations_s"][origin][destination]
                    _require(distance_m is not None and duration_s is not None,
                             f"{label} uses an unreachable road leg {from_id} to {to_id}.")
                    distance = distance_m / 1000
                    _close(event.get("distance_km"), distance, label + " road distance")
                    _close(event_duration, duration_s / 60, label + " road duration")
                    next_position = nodes[to_id]["snapped_position"]
                    if "path" in event:
                        path = event["path"]
                        _require(isinstance(path, list) and len(path) >= 2 and all(point(item) for item in path),
                                 f"Invalid {label} geographic path.")
                        _require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-5)
                                     for actual, expected in ((path[0], position), (path[-1], next_position))
                                     for a, b in zip(actual, expected)), f"Invalid {label} geographic path endpoints.")
                else:
                    path = event.get("path")
                    _require(isinstance(path, list) and len(path) >= 2 and all(point(item) for item in path)
                             and path[0] == position
                             and all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(path, path[1:])),
                             f"Invalid {label} street path.")
                    blocks = len(path) - 1
                    _require(blocks == abs(path[-1][0] - position[0]) + abs(path[-1][1] - position[1]),
                             f"{label} drive must use a shortest grid path.")
                    distance = blocks * scenario["grid"]["block_km"]
                    _close(event_duration, distance / scenario["timing"]["speed_kmh"] * 60, label + " drive duration")
                    next_position = path[-1]
                expected_battery -= distance * scenario["fleet"]["energy_use_kwh_per_km"]
                costs["driving_cost"] += distance * scenario["costs"]["driving_per_km"]
                position = next_position
            elif kind == "deliver":
                delivery_id = event.get("delivery_id")
                _require(isinstance(delivery_id, str) and delivery_id in assigned and delivery_id not in delivered,
                         f"Unknown or repeated {label} delivery.")
                delivery = source["deliveries"][delivery_id]
                _require(position == delivery["position"], f"{label} delivery is at the wrong location.")
                _close(event_duration, delivery["service_min"], label + " delivery service time")
                costs["late_penalty"] += max(0, end - delivery["deadline_min"]) / 60 * scenario["costs"]["late_per_hour"]
                delivered.add(delivery_id)
                van_delivered.add(delivery_id)
            elif kind == "charge":
                charger_id = event.get("charger_id")
                _require(isinstance(charger_id, str) and charger_id in source["chargers"], f"Unknown {label} charger.")
                charger = source["chargers"][charger_id]
                _require(position == charger["position"], f"{label} charge is at the wrong location.")
                slots = [slot for slot in charger["slots"]
                         if math.isclose(start, slot["start_min"], rel_tol=0, abs_tol=_TOL)
                         and math.isclose(end, slot["end_min"], rel_tol=0, abs_tol=_TOL)]
                _require(len(slots) == 1 and ("slot_id" not in event or event["slot_id"] == slots[0]["id"]),
                         f"{label} charging must occupy one complete configured slot.")
                for other_start, other_end in bookings[charger_id]:
                    _require(start >= other_end - _TOL or end <= other_start + _TOL, f"{label} charger booking conflict.")
                bookings[charger_id].append((start, end))
                energy = min(charger["power_kw"] * event_duration / 60, capacity - battery)
                expected_battery += energy
                if "energy_kwh" in event:
                    _close(event["energy_kwh"], energy, label + " charged energy")
                costs["electricity_cost"] += energy * scenario["costs"]["electricity_per_kwh"]
            else:
                if "charger_id" in event:
                    charger = source["chargers"].get(event["charger_id"]) if isinstance(event["charger_id"], str) else None
                    _require(charger is not None and position == charger["position"], f"Invalid {label} charger wait.")
                costs["waiting_cost"] += event_duration / 60 * scenario["costs"]["waiting_per_hour"]
            _close(battery_end, expected_battery, label + " event battery physics")
            _require(reserve - _TOL <= battery_end <= capacity + _TOL, f"{label} violates battery reserve or capacity.")
            battery, previous_end = battery_end, end
        _require(van_delivered == assigned, f"{label} omitted an assigned delivery for {key}.")
        _require(position == home, f"{label} {key} must return to its assigned depot.")
    _require(delivered == set(source["deliveries"]), f"{label} omitted required delivery stops.")
    return costs


def _verify_costs(actual, expected, label):
    _require(isinstance(actual, dict), f"Missing {label} cost breakdown.")
    aliases = {"driving": "driving_cost", "driving_cost": "driving_cost",
               "electricity": "electricity_cost", "electricity_cost": "electricity_cost",
               "waiting": "waiting_cost", "waiting_cost": "waiting_cost",
               "late": "late_penalty", "late_cost": "late_penalty", "late_penalty": "late_penalty",
               "late_delivery_cost": "late_penalty"}
    seen = set()
    for key, value in actual.items():
        if key in ("total", "total_cost"):
            _close(value, sum(expected.values()), label + " total cost")
            continue
        _require(key in aliases and aliases[key] not in seen, f"Unknown or duplicate {label} cost component.")
        field = aliases[key]
        _close(value, expected[field], label + " " + field)
        seen.add(field)
    _require(seen == set(expected), f"{label} must report all four cost components.")


def validate_scenario_result(scenario, output, routing=None):
    """Reject a native result whose replay, physics, assignments, or USD differ.

    The caller supplies the effective scenario for this attempt, rather than the
    original defaults.  All checks are independent of solver verification claims.
    """
    scenario = normalize_scenario(scenario)
    if scenario["schema_version"] == 2:
        from geographic import validate_geographic_result

        return validate_geographic_result(scenario, output, routing)
    _require(isinstance(output, dict) and output.get("status") == "ok", "The scenario solver did not succeed.")
    costs = _verify_simulation(scenario, output.get("simulation"), "Quantum")
    objective = output.get("objective")
    _require(isinstance(objective, dict) and objective.get("unit") == "USD"
             and objective.get("direction", "minimize") == "minimize", "Scenario objective must minimize USD.")
    _close(objective.get("value"), sum(costs.values()), "Quantum objective")
    solution = output.get("solution")
    _require(isinstance(solution, dict), "Missing scenario solution.")
    _verify_costs(solution.get("cost_breakdown"), costs, "Quantum")
    verification = solution.get("verification")
    _require(isinstance(verification, dict) and verification.get("feasible") is True
             and verification.get("violations") == [], "The solver must report independent feasible verification.")
    reference = output.get("reference")
    if isinstance(reference, dict) and reference.get("simulation") is not None:
        classical_costs = _verify_simulation(scenario, reference["simulation"], "Classical")
        _close(reference.get("objective_value", reference.get("value")), sum(classical_costs.values()), "Classical objective")
        if "cost_breakdown" in reference:
            _verify_costs(reference["cost_breakdown"], classical_costs, "Classical")
