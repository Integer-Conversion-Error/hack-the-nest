"""Versioned geographic scenario inputs and independent road-metric checks.

Coordinates consistently use GeoJSON/OSRM order: [longitude, latitude].  Solver
inputs preserve requested pins; replay positions use the saved routing snaps.
"""

from __future__ import annotations

import copy

from scenario import (MAX_SCENARIO_BYTES, _canonical, _close, _number, _object, _require,
                      _verify_costs, _verify_simulation, example_scenario, normalize_scenario,
                      numeric_bindings)


def geographic_point(value):
    """Validate a WGS84 map position and normalize it to six decimal places."""
    _require(isinstance(value, list) and len(value) == 2,
             "Geographic positions must be [longitude, latitude].")
    longitude = _number(value[0], "Longitude", -180, 180)
    latitude = _number(value[1], "Latitude", -85, 85)
    return [_number(round(longitude, 6), "Longitude"), _number(round(latitude, 6), "Latitude")]


def normalize_geographic_scenario(data):
    """Validate source v2, retaining the existing common fleet constraints."""
    _object(data, ("schema_version", "title", "map", "depots", "chargers", "deliveries", "vehicles",
                   "fleet", "timing", "costs"), "Geographic scenario")
    _require(type(data["schema_version"]) is int and data["schema_version"] == 2,
             "Unsupported geographic scenario schema version.")
    _object(data["map"], ("kind", "profile"), "Geographic map")
    _require(data["map"] == {"kind": "geographic", "profile": "driving"},
             "Geographic scenarios require the driving road profile.")
    _object(data["timing"], ("start_hour", "duration_min"), "Geographic timing")
    # Reuse the strict, established common schema without converting or changing
    # any stored v1 source. Only placeholder positions pass through its grid check.
    common = copy.deepcopy(data)
    common["schema_version"] = 1
    common.pop("map")
    common["grid"] = {"width": 2, "height": 2, "block_km": 1}
    common["timing"]["speed_kmh"] = 30
    positions = {}
    for collection in ("depots", "chargers", "deliveries"):
        _require(isinstance(common[collection], list), f"{collection} must be a list.")
        positions[collection] = []
        for item in common[collection]:
            _require(isinstance(item, dict), f"Invalid geographic {collection} entity.")
            positions[collection].append(geographic_point(item.get("position")))
            item["position"] = [0, 0]
    # Restore coordinates by ID after common normalization sorts entity lists.
    by_id = {}
    for collection in positions:
        by_id[collection] = [(item.get("id"), position)
                             for item, position in zip(common[collection], positions[collection])]
    result = normalize_scenario(common)
    result["schema_version"] = 2
    result.pop("grid")
    result["map"] = {"kind": "geographic", "profile": "driving"}
    result["timing"].pop("speed_kmh")
    for collection, pairs in by_id.items():
        restored = dict(pairs)
        for item in result[collection]:
            item["position"] = restored[item["id"]]
    _require(len(_canonical(result).encode("utf-8")) <= MAX_SCENARIO_BYTES,
             "Scenario exceeds the 64 KB input limit.")
    return result


def geo_seed():
    """A compact downtown Toronto example with two depots and three stops."""
    source = example_scenario()
    source["schema_version"] = 2
    source.pop("grid")
    source["map"] = {"kind": "geographic", "profile": "driving"}
    source["title"] = "Toronto delivery routes"
    source["timing"].pop("speed_kmh")
    positions = {"P1": [-79.3832, 43.6532], "P2": [-79.395, 43.653],
                 "C1": [-79.386, 43.65], "C2": [-79.389, 43.655],
                 "D1": [-79.379, 43.65], "D2": [-79.392, 43.65], "D3": [-79.398, 43.655]}
    for collection in ("depots", "chargers", "deliveries"):
        for item in source[collection]:
            item["position"] = positions[item["id"]]
    return normalize_geographic_scenario(source)


example_geographic_scenario = geo_seed


def geographic_prompt(source):
    """Generate a complete request without changing any legacy grid prompt."""
    source = normalize_geographic_scenario(source)
    prompt = """Solve this geographic EV fleet dispatch with measured QAOA and visualize the verified road routes.
The source JSON below and read-only scenario.json are authoritative. Read the
pipeline-owned, read-only routing.json before implementing any route calculation.
It contains the immutable OSRM road snapshot: source identity, ordered nodes with
requested positions and snapped_position, directed distances_m and durations_s.
Use its node IDs and matrix order exactly. Matrices can be asymmetric, and null
cells mean unreachable road legs. Do not assume symmetry, fetch routing yourself,
use straight-line/crow-flies distances, invent geometry, or derive time from speed.
Every road leg's time is durations_s[from][to] / 60 minutes; its driven distance is
distances_m[from][to] / 1000 km. There is no adjustable driving-speed control.
Maintain the source entity IDs, requested coordinates, stop assignments and home
depots. Replays use the routing snapshot's SNAPPED [longitude,latitude] positions.
Each vehicle starts at its assigned depot at minute zero and returns there before
the shift ends. Visit every assigned delivery exactly once; choose its stop order.
Idle vehicles may stay home. Model events contiguously, including explicit waits.
Keep battery at or above the shared reserve throughout; consume the selected
energy_use_kwh_per_km times the ROAD distance, and never exceed battery capacity.
Deliveries take their own service_min. Delivery deadlines are soft: charge late
cost from deadline_min until service completion; shift duration is a hard limit.
Each single-plug charger visit occupies one entire configured half-open slot.
Arrive by its start, wait explicitly if early, and add min(power_kw * slot minutes
/ 60, remaining capacity) kWh. Charging between stops and repeated visits are
allowed, provided reservations do not overlap and all other constraints hold.
Use bounded candidate generation and route search, including several stop orders
and charging choices. Do not enumerate all permutations of ten delivery stops.
Use measured QAOA to choose candidates within the worker budget; do not substitute
an exact classical answer for a failed measurement. Independently verify all
stops, road metrics, event timing, batteries, slots, home returns and USD costs.
Compute a classical reference over the same candidates. Disclose heuristic
pruning and scope any optimality proof to that candidate set.
Minimize driving_per_km for all ROAD kilometers including returns, electricity_per_kwh
for actual charged energy, waiting_per_hour for explicit waits, and late_per_hour
for late delivery minutes. Prorate hourly rates by minutes / 60. Report USD
cost_breakdown fields driving_cost, electricity_cost, waiting_cost, late_penalty.
Emit simulation.map {kind:"geographic",profile:"driving",routing_digest:<routing.json digest>}
and NO grid field. Include depots, chargers and deliveries at their SNAP positions,
the existing start_hour, duration_min, battery_capacity_kwh, reserve_pct and vans.
Each van has id, depot_id, initial_battery_kwh, delivery_ids and timed events.
Each event includes position and battery_start_kwh/battery_end_kwh. A drive adds
from_id, to_id and distance_km, with event duration taken from the directed matrix.
Do not fabricate a path: the host adds actual road geometry for selected legs.
Omit a zero-time, zero-distance drive between coincident routing points. Deliver
events identify delivery_id; charge/wait events identify charger_id when relevant.
Include reference.simulation for a feasible classical schedule, with the same
routing digest, snapped nodes and input settings.
You may approve a small subset of numeric controls in the registry below. Defaults
MUST equal source values. Do not invent keys, location/assignment/entity controls,
or a speed control. Apply effective values to both solver and independent verifier.

Authoritative geographic scenario JSON:
""" + _canonical(source) + "\n\nHost numeric control registry (entity IDs address list members):\n" + _canonical(numeric_bindings(source))
    _require(len(prompt.encode("utf-8")) <= MAX_SCENARIO_BYTES, "Generated scenario prompt exceeds 64 KB.")
    return prompt


def validate_geographic_result(source, output, routing):
    """Independently verify selected road schedules against the saved matrix."""
    from road_network import validate_routing

    source = normalize_geographic_scenario(source)
    _require(isinstance(routing, dict), "Geographic results require the original routing snapshot.")
    # Pipeline loading first binds this artifact to the immutable base source.
    # Here approved numeric controls can differ; node coordinates/profile must
    # still match and the complete routing artifact digest is checked again.
    routing = validate_routing(source, routing, expected_source_digest=routing.get("source_digest"))
    _require(isinstance(output, dict) and output.get("status") == "ok", "The scenario solver did not succeed.")
    costs = _verify_simulation(source, output.get("simulation"), "Quantum", routing=routing)
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
        classical_costs = _verify_simulation(source, reference["simulation"], "Classical", routing=routing)
        _close(reference.get("objective_value", reference.get("value")), sum(classical_costs.values()), "Classical objective")
        if "cost_breakdown" in reference:
            _verify_costs(reference["cost_breakdown"], classical_costs, "Classical")
