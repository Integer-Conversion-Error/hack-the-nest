"""Offline geographic input, numeric reuse and independent road verification."""

import copy
import hashlib
import json
import math
import unittest

from geographic import geo_seed, geographic_point
from scenario import (effective_scenario, example_scenario, normalize_scenario, numeric_bindings,
                      scenario_context, scenario_digest, scenario_prompt, validate_scenario_result)


def routing_fixture(source=None):
    """A deterministic directed OSRM-shaped snapshot; no network requests."""
    source = normalize_scenario(source or geo_seed())
    nodes = [{"id": item["id"], "position": list(item["position"]),
              "snapped_position": list(item["position"]), "snap_distance_m": 0}
             for name in ("depots", "chargers", "deliveries") for item in source[name]]
    distances = [[0 if i == j else 400 + 80 * i + 110 * j for j in range(len(nodes))] for i in range(len(nodes))]
    durations = [[0 if i == j else distances[i][j] / 7.5 + i * 5 for j in range(len(nodes))] for i in range(len(nodes))]
    result = {"schema_version": 1, "source_digest": scenario_digest(source), "provider": "osrm",
              "base_url": "https://router.project-osrm.org", "profile": "driving",
              "created_at": "2026-11-20T00:00:00Z", "data_version": None, "nodes": nodes,
              "distances_m": distances, "durations_s": durations}
    return resign_routing(result)


def resign_routing(artifact):
    """Test-only signing; production artifacts are signed by road_network."""
    payload = {key: value for key, value in artifact.items() if key != "digest"}
    artifact["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return artifact


def raw_geo_result(source, routing):
    """Compute direct routes from snapshot metrics, explicitly without quantum evidence.

    Drives deliberately omit path: the host must supply genuine selected road
    geometry, rather than treating a fabricated fixture line as a measured route.
    """
    source = normalize_scenario(source)
    nodes = {item["id"]: item for item in routing["nodes"]}
    indexes = {item["id"]: i for i, item in enumerate(routing["nodes"])}
    capacity = source["fleet"]["battery_capacity_kwh"]
    vans = []
    costs = {"driving_cost": 0.0, "electricity_cost": 0.0, "waiting_cost": 0.0, "late_penalty": 0.0}
    for vehicle in source["vehicles"]:
        current_id = vehicle["depot_id"]
        battery = capacity * vehicle["initial_charge_pct"] / 100
        initial = battery
        minute = 0.0
        events = []
        assigned = [item for item in source["deliveries"] if item["vehicle_id"] == vehicle["id"]]

        def drive(to_id):
            nonlocal current_id, battery, minute
            origin, destination = indexes[current_id], indexes[to_id]
            distance_km = routing["distances_m"][origin][destination] / 1000
            duration = routing["durations_s"][origin][destination] / 60
            if duration == 0 and distance_km == 0:
                current_id = to_id
                return
            next_battery = battery - distance_km * source["fleet"]["energy_use_kwh_per_km"]
            events.append({"type": "drive", "from_id": current_id, "to_id": to_id, "distance_km": distance_km,
                           "start_min": minute, "end_min": minute + duration,
                           "position": list(nodes[current_id]["snapped_position"]),
                           "battery_start_kwh": battery, "battery_end_kwh": next_battery})
            costs["driving_cost"] += distance_km * source["costs"]["driving_per_km"]
            current_id, battery, minute = to_id, next_battery, minute + duration

        for delivery in assigned:
            drive(delivery["id"])
            end = minute + delivery["service_min"]
            events.append({"type": "deliver", "delivery_id": delivery["id"], "start_min": minute,
                           "end_min": end, "position": list(nodes[current_id]["snapped_position"]),
                           "battery_start_kwh": battery, "battery_end_kwh": battery})
            costs["late_penalty"] += max(0, end - delivery["deadline_min"]) / 60 * source["costs"]["late_per_hour"]
            minute = end
        drive(vehicle["depot_id"])
        vans.append({"id": vehicle["id"], "depot_id": vehicle["depot_id"], "initial_battery_kwh": initial,
                     "delivery_ids": [item["id"] for item in assigned], "events": events})
    simulation = {"kind": "ev_fleet", "map": {"kind": "geographic", "profile": "driving", "routing_digest": routing["digest"]},
                  "depots": [{"id": item["id"], "position": list(nodes[item["id"]]["snapped_position"])}
                             for item in source["depots"]],
                  "chargers": [{"id": item["id"], "position": list(nodes[item["id"]]["snapped_position"])}
                               for item in source["chargers"]],
                  "deliveries": [{"id": item["id"], "position": list(nodes[item["id"]]["snapped_position"]),
                                  "deadline_min": item["deadline_min"]} for item in source["deliveries"]],
                  "start_hour": source["timing"]["start_hour"], "duration_min": source["timing"]["duration_min"],
                  "battery_capacity_kwh": capacity, "reserve_pct": source["fleet"]["reserve_pct"], "vans": vans}
    total = sum(costs.values())
    return {"status": "ok", "controls": {}, "objective": {"value": total, "unit": "USD", "direction": "minimize"},
            "solution": {"cost_breakdown": costs, "verification": {"feasible": True, "violations": []}},
            "simulation": simulation,
            "reference": {"method": "Road-matrix test fixture", "objective_value": total, "feasible": True,
                          "optimality_proven": False, "simulation": copy.deepcopy(simulation)},
            "quantum": {"method": "Test fixture: no quantum execution", "qubits": None,
                        "decision_qubits": None, "counts": {}}, "runtime_seconds": 0}


class GeographicSourceTests(unittest.TestCase):
    def test_geo_seed_preserves_shared_fleet_but_uses_lon_lat_and_road_times(self):
        source = geo_seed()
        self.assertEqual(source["schema_version"], 2)
        self.assertEqual(source["title"], "Toronto delivery routes")
        self.assertEqual(source["map"], {"kind": "geographic", "profile": "driving"})
        self.assertNotIn("grid", source)
        self.assertNotIn("speed_kmh", source["timing"])
        self.assertEqual(source["depots"][0]["position"], [-79.3832, 43.6532])
        self.assertEqual(source["vehicles"], example_scenario()["vehicles"])

    def test_six_decimal_canonical_coordinates_and_entity_order(self):
        source = geo_seed()
        source["depots"][0]["position"] = [-79.38320009, 43.65320009]
        source["depots"].reverse()
        self.assertEqual(scenario_digest(source), scenario_digest(geo_seed()))
        self.assertEqual(geographic_point([180, -85]), [180, -85])
        self.assertEqual(geographic_point([-0.0, 0.0]), [0, 0])

    def test_invalid_coordinates_map_fields_and_references_are_rejected(self):
        mutations = (
            lambda s: s["depots"][0].update(position=[181, 0]),
            lambda s: s["depots"][0].update(position=[0, 85.1]),
            lambda s: s["depots"][0].update(position=[0, math.nan]),
            lambda s: s["depots"][0].update(position=[True, 0]),
            lambda s: s["depots"][0].update(position=["-79.3", 43]),
            lambda s: s["map"].update(profile="walking"),
            lambda s: s["map"].update(zoom=13),
            lambda s: s["timing"].update(speed_kmh=30),
            lambda s: s.update(grid={"width": 6, "height": 6, "block_km": 0.5}),
            lambda s: s["vehicles"][0].update(depot_id=[]),
            lambda s: s["deliveries"][0].update(vehicle_id="V99"),
            lambda s: s["deliveries"][0].update(unknown=True),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                source = geo_seed()
                mutate(source)
                with self.assertRaises(ValueError):
                    normalize_scenario(source)

    def test_grid_prompt_bytes_and_digest_are_unchanged(self):
        prompt = scenario_prompt(example_scenario())
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                         "08799cfc0ae6007e4071f8b9e67692794a9b58a7da9a7ea5de995bf32ebd2949")
        self.assertIn("shortest\ngrid paths", prompt)

    def test_prompt_requires_directed_snapshot_and_never_invents_geometry(self):
        prompt = scenario_prompt(geo_seed())
        self.assertIn("routing.json", prompt)
        self.assertIn("asymmetric", prompt)
        self.assertIn("null", prompt)
        self.assertIn("Do not fabricate a path", prompt)
        self.assertIn('"profile":"driving"', prompt)
        self.assertLessEqual(len(prompt.encode()), 65_536)

    def test_numeric_binding_reuse_excludes_speed_and_all_positions(self):
        source = geo_seed()
        bindings = numeric_bindings(source)
        self.assertNotIn("driving_speed_km_per_hour", bindings)
        self.assertTrue(all("position" not in path for path in bindings.values()))
        control = {"id": "driving_cost_per_km", "type": "number", "default": 0.3,
                   "minimum": 0, "maximum": 1, "step": 0.1}
        effective = effective_scenario(source, [control], {"driving_cost_per_km": 0.5})
        self.assertEqual(effective["costs"]["driving_per_km"], 0.5)
        self.assertEqual(effective["depots"], source["depots"])
        self.assertNotEqual(scenario_context(source, [control], {"driving_cost_per_km": 0.5})["effective_digest"], scenario_digest(source))
        with self.assertRaises(ValueError):
            effective_scenario(source, [{**control, "id": "driving_speed_km_per_hour"}], {})


class GeographicResultTests(unittest.TestCase):
    def test_directed_road_metrics_drive_time_energy_and_usd_in_both_schedules(self):
        source = geo_seed()
        routing = routing_fixture(source)
        self.assertNotEqual(routing["distances_m"][0][4], routing["distances_m"][4][0])
        self.assertNotEqual(routing["durations_s"][0][4], routing["durations_s"][4][0])
        output = raw_geo_result(source, routing)
        self.assertNotIn("path", output["simulation"]["vans"][0]["events"][0])
        validate_scenario_result(source, output, routing=routing)

    def test_numeric_reuse_keeps_original_routing_source_identity(self):
        source = geo_seed()
        routing = routing_fixture(source)
        control = {"id": "driving_cost_per_km", "type": "number", "default": 0.3,
                   "minimum": 0, "maximum": 1, "step": 0.1}
        effective = effective_scenario(source, [control], {"driving_cost_per_km": 0.5})
        validate_scenario_result(effective, raw_geo_result(effective, routing), routing=routing)

    def test_snapped_positions_are_required_and_requested_source_is_unchanged(self):
        source = geo_seed()
        routing = routing_fixture(source)
        # A very small road snap, well inside the provider's documented bound.
        node = routing["nodes"][0]
        node["snapped_position"][0] += 0.0000103
        node["snap_distance_m"] = 0.8046
        resign_routing(routing)
        output = raw_geo_result(source, routing)
        validate_scenario_result(source, output, routing=routing)
        self.assertEqual(source["depots"][0]["position"], [-79.3832, 43.6532])
        output["simulation"]["depots"][0]["position"] = source["depots"][0]["position"]
        with self.assertRaisesRegex(ValueError, "moved"):
            validate_scenario_result(source, output, routing=routing)

    def test_unreachable_directed_leg_is_rejected_without_distance_fallback(self):
        source = geo_seed()
        routing = routing_fixture(source)
        output = raw_geo_result(source, routing)
        i, j = 0, 4
        routing["distances_m"][i][j] = routing["durations_s"][i][j] = None
        resign_routing(routing)
        for scene in (output["simulation"], output["reference"]["simulation"]):
            scene["map"]["routing_digest"] = routing["digest"]
        with self.assertRaisesRegex(ValueError, "unreachable"):
            validate_scenario_result(source, output, routing=routing)

    def test_rejects_fabricated_distance_time_battery_assignment_and_reference(self):
        source = geo_seed()
        routing = routing_fixture(source)
        mutations = (
            lambda r: r["simulation"]["vans"][0]["events"][0].update(distance_km=0.001),
            lambda r: r["simulation"]["vans"][0]["events"][0].update(end_min=0.01),
            lambda r: r["simulation"]["vans"][0]["events"][0].update(battery_end_kwh=8),
            lambda r: r["simulation"]["vans"][0]["events"][0].update(from_id="D3"),
            lambda r: r["simulation"]["vans"][0].update(delivery_ids=["D1"]),
            lambda r: r["simulation"]["map"].update(routing_digest="f" * 64),
            lambda r: r["reference"]["simulation"]["vans"][0]["events"][0].update(distance_km=0.001),
            lambda r: r["solution"]["cost_breakdown"].update(driving_cost=0),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = raw_geo_result(source, routing)
                mutate(result)
                with self.assertRaises(ValueError):
                    validate_scenario_result(source, result, routing=routing)

    def test_verifies_routing_digest_and_node_coordinates_and_requires_snapshot(self):
        source = geo_seed()
        routing = routing_fixture(source)
        output = raw_geo_result(source, routing)
        with self.assertRaises(ValueError):
            validate_scenario_result(source, output)
        routing["distances_m"][0][4] += 100
        with self.assertRaises(ValueError):
            validate_scenario_result(source, output, routing=routing)
        routing = routing_fixture(source)
        source["depots"][0]["position"][0] += 0.01
        with self.assertRaises(ValueError):
            validate_scenario_result(source, output, routing=routing)

    def test_raw_optional_path_checks_coordinates_and_endpoints_without_grid_adjacency(self):
        source = geo_seed()
        routing = routing_fixture(source)
        output = raw_geo_result(source, routing)
        event = output["simulation"]["vans"][0]["events"][0]
        origin = routing["nodes"][0]["snapped_position"]
        destination = routing["nodes"][4]["snapped_position"]
        event["path"] = [origin, [-79.38, 43.651], destination]
        validate_scenario_result(source, output, routing=routing)
        event["path"][-1] = [0, 0]
        with self.assertRaisesRegex(ValueError, "path endpoints"):
            validate_scenario_result(source, output, routing=routing)

    def test_all_sixty_geographic_stops_are_required_without_truncation(self):
        source = geo_seed()
        source["vehicles"] = [{"id": f"V{number}", "depot_id": "P1", "initial_charge_pct": 100}
                              for number in range(1, 7)]
        source["deliveries"] = [{"id": f"D{number}", "position": [-79.38 + number * 0.0001, 43.65],
                                 "vehicle_id": f"V{(number - 1) // 10 + 1}", "deadline_min": 120, "service_min": 1}
                                for number in range(1, 61)]
        source = normalize_scenario(source)
        routing = routing_fixture(source)
        for i in range(len(routing["nodes"])):
            for j in range(len(routing["nodes"])):
                routing["distances_m"][i][j] = 0 if i == j else 100
                routing["durations_s"][i][j] = 0 if i == j else 60
        resign_routing(routing)
        output = raw_geo_result(source, routing)
        validate_scenario_result(source, output, routing=routing)
        self.assertIn('"id":"D60"', scenario_prompt(source))
        output["simulation"]["vans"][3]["delivery_ids"].remove("D35")
        with self.assertRaisesRegex(ValueError, "assignments"):
            validate_scenario_result(source, output, routing=routing)


if __name__ == "__main__":
    unittest.main()
