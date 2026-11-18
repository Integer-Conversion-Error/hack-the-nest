"""Scenario boundary and independent result checks; no model or quantum calls."""

import copy
import math
import unittest

from scenario import (effective_scenario, example_scenario, normalize_scenario, numeric_bindings,
                      scenario_context, scenario_digest, scenario_prompt, validate_scenario_result)


def street_path(start, end):
    point = list(start)
    path = [list(point)]
    for axis in (0, 1):
        while point[axis] != end[axis]:
            point[axis] += 1 if end[axis] > point[axis] else -1
            path.append(list(point))
    return path


def direct_result(scenario):
    """Deterministic direct-route test data, with explicitly absent measurements.

    This fixture is for HTTP/browser contract tests, not a solver implementation
    or evidence that a quantum calculation ran.  Callers must choose inputs for
    which the direct routes are physically feasible.
    """
    s = normalize_scenario(scenario)
    capacity = s["fleet"]["battery_capacity_kwh"]
    depots = {item["id"]: item["position"] for item in s["depots"]}
    costs = {"driving_cost": 0.0, "electricity_cost": 0.0, "waiting_cost": 0.0, "late_penalty": 0.0}
    vans = []
    for vehicle in s["vehicles"]:
        home = depots[vehicle["depot_id"]]
        position = list(home)
        battery = capacity * vehicle["initial_charge_pct"] / 100
        initial = battery
        minute = 0.0
        events = []
        deliveries = [item for item in s["deliveries"] if item["vehicle_id"] == vehicle["id"]]

        def drive(target):
            nonlocal battery, minute, position
            path = street_path(position, target)
            if len(path) == 1:
                return
            distance = (len(path) - 1) * s["grid"]["block_km"]
            travel = distance / s["timing"]["speed_kmh"] * 60
            next_battery = battery - distance * s["fleet"]["energy_use_kwh_per_km"]
            events.append({"type": "drive", "start_min": minute, "end_min": minute + travel,
                           "position": list(position), "path": path,
                           "battery_start_kwh": battery, "battery_end_kwh": next_battery})
            costs["driving_cost"] += distance * s["costs"]["driving_per_km"]
            position, minute, battery = list(target), minute + travel, next_battery

        for delivery in deliveries:
            drive(delivery["position"])
            end = minute + delivery["service_min"]
            events.append({"type": "deliver", "delivery_id": delivery["id"], "start_min": minute,
                           "end_min": end, "position": list(position),
                           "battery_start_kwh": battery, "battery_end_kwh": battery})
            costs["late_penalty"] += max(0, end - delivery["deadline_min"]) / 60 * s["costs"]["late_per_hour"]
            minute = end
        drive(home)
        vans.append({"id": vehicle["id"], "depot_id": vehicle["depot_id"], "initial_battery_kwh": initial,
                     "delivery_ids": [item["id"] for item in deliveries], "events": events})
    simulation = {"kind": "ev_fleet", "grid": copy.deepcopy(s["grid"]), "depots": copy.deepcopy(s["depots"]),
                  "chargers": [{"id": item["id"], "position": list(item["position"])} for item in s["chargers"]],
                  "deliveries": [{"id": item["id"], "position": list(item["position"]),
                                  "deadline_min": item["deadline_min"]} for item in s["deliveries"]],
                  "start_hour": s["timing"]["start_hour"], "duration_min": s["timing"]["duration_min"],
                  "battery_capacity_kwh": capacity, "reserve_pct": s["fleet"]["reserve_pct"], "vans": vans}
    total = sum(costs.values())
    return {"status": "ok", "controls": {}, "objective": {"value": total, "unit": "USD", "direction": "minimize"},
            "solution": {"cost_breakdown": costs, "verification": {"feasible": True, "violations": []}},
            "simulation": simulation,
            "reference": {"method": "Direct-route test fixture", "objective_value": total, "feasible": True,
                          "optimality_proven": False, "simulation": copy.deepcopy(simulation)},
            "quantum": {"method": "Test fixture: no quantum execution", "qubits": None,
                        "decision_qubits": None, "counts": {}}, "runtime_seconds": 0}


def numeric_control(key="van_2_initial_charge_pct", default=35, minimum=20, maximum=100, step=1):
    return {"key": key, "label": key, "type": "number", "default": default,
            "minimum": minimum, "maximum": maximum, "step": step, "rerun_mode": "rebuild"}


def sixty_stop_scenario():
    s = example_scenario()
    s["vehicles"] = [{"id": f"V{number}", "depot_id": "P1", "initial_charge_pct": 100} for number in range(1, 7)]
    s["deliveries"] = [{"id": f"D{number}", "position": [2, 2], "vehicle_id": f"V{(number - 1) // 10 + 1}",
                        "deadline_min": 60, "service_min": 1} for number in range(1, 61)]
    return normalize_scenario(s)


def charging_fixture(initial_charge_pct=40):
    """One van books adjacent slots, with actual waiting and capped energy."""
    scenario = example_scenario()
    scenario["depots"] = [{"id": "P1", "position": [0, 0]}]
    scenario["vehicles"] = [{"id": "V1", "depot_id": "P1", "initial_charge_pct": initial_charge_pct}]
    scenario["deliveries"] = [{"id": "D1", "position": [2, 0], "vehicle_id": "V1", "deadline_min": 20, "service_min": 2}]
    scenario["chargers"] = [{"id": "C1", "position": [1, 0], "power_kw": 6,
                             "slots": [{"id": "S1", "start_min": 5, "end_min": 15},
                                       {"id": "S2", "start_min": 15, "end_min": 25}]}]
    scenario["fleet"] = {"battery_capacity_kwh": 10, "reserve_pct": 20, "energy_use_kwh_per_km": 0.5}
    result = direct_result(scenario)
    events = []
    battery = initial_charge_pct / 10
    initial = battery

    def event(kind, start, end, position, next_battery, **extra):
        nonlocal battery
        events.append({"type": kind, "start_min": start, "end_min": end, "position": position,
                       "battery_start_kwh": battery, "battery_end_kwh": next_battery, **extra})
        battery = next_battery

    event("drive", 0, 1, [0, 0], battery - 0.25, path=[[0, 0], [1, 0]])
    event("wait", 1, 5, [1, 0], battery, charger_id="C1")
    energy = min(1, 10 - battery)
    event("charge", 5, 15, [1, 0], battery + energy, charger_id="C1", slot_id="S1", energy_kwh=energy)
    second_energy = min(1, 10 - battery)
    event("charge", 15, 25, [1, 0], battery + second_energy, charger_id="C1", slot_id="S2", energy_kwh=second_energy)
    event("drive", 25, 26, [1, 0], battery - 0.25, path=[[1, 0], [2, 0]])
    event("deliver", 26, 28, [2, 0], battery, delivery_id="D1")
    event("drive", 28, 30, [2, 0], battery - 0.5, path=[[2, 0], [1, 0], [0, 0]])
    result["simulation"]["vans"][0].update(events=events, initial_battery_kwh=initial)
    costs = {"driving_cost": 0.6, "electricity_cost": (energy + second_energy) * 0.2,
             "waiting_cost": 4 / 60 * 25, "late_penalty": 8}
    result["solution"]["cost_breakdown"] = costs
    result["objective"]["value"] = sum(costs.values())
    result["reference"].update(simulation=copy.deepcopy(result["simulation"]), objective_value=sum(costs.values()))
    return scenario, result


class ScenarioValidationTests(unittest.TestCase):
    def test_seed_is_canonical_detached_and_contains_multiple_stops_and_depots(self):
        scenario = example_scenario()
        self.assertEqual(scenario["title"], "Two depots, three deliveries")
        self.assertEqual(len(scenario["depots"]), 2)
        self.assertEqual([item["vehicle_id"] for item in scenario["deliveries"]], ["V1", "V1", "V2"])
        scenario["vehicles"].reverse()
        scenario["vehicles"][0]["initial_charge_pct"] = 35.0
        scenario["depots"].reverse()
        self.assertEqual(scenario_digest(scenario), scenario_digest(example_scenario()))
        normalized = normalize_scenario(scenario)
        normalized["depots"][0]["position"][0] = 0
        self.assertEqual(example_scenario()["depots"][0]["position"], [2, 2])

    def test_rejects_unknown_missing_malformed_and_nonfinite_inputs(self):
        for mutation in (
            lambda s: s.update(unknown=True),
            lambda s: s.pop("costs"),
            lambda s: s.update(schema_version=True),
            lambda s: s["grid"].update(width=51),
            lambda s: s["grid"].update(width=6.0),
            lambda s: s["grid"].update(block_km=math.inf),
            lambda s: s["grid"].update(block_km=True),
            lambda s: s["fleet"].update(energy_use_kwh_per_km=0),
            lambda s: s["timing"].update(duration_min=1441),
            lambda s: s["timing"].update(start_hour=24),
            lambda s: s["costs"].update(driving_per_km=-1),
            lambda s: s["deliveries"][0].update(position=[6, 0]),
            lambda s: s["deliveries"][0].update(vehicle_id="V99"),
            lambda s: s["deliveries"][0].update(vehicle_id=[]),
            lambda s: s["vehicles"][0].update(depot_id={}),
            lambda s: s["vehicles"][0].update(initial_charge_pct=float("nan")),
            lambda s: s["depots"].append(copy.deepcopy(s["depots"][0])),
            lambda s: s["deliveries"][0].update(id="D01"),
            lambda s: s.update(title="x" * 121),
            lambda s: s.update(title="bad\nname"),
        ):
            with self.subTest(mutation=mutation):
                scenario = example_scenario()
                mutation(scenario)
                with self.assertRaises(ValueError):
                    normalize_scenario(scenario)

    def test_zero_chargers_idle_vehicles_and_adjacent_slots(self):
        scenario = example_scenario()
        scenario["chargers"] = []
        scenario["vehicles"].append({"id": "V3", "depot_id": "P1", "initial_charge_pct": 100})
        self.assertEqual(len(normalize_scenario(scenario)["vehicles"]), 3)
        scenario["chargers"] = [{"id": "C1", "position": [0, 0], "power_kw": 10,
                                 "slots": [{"id": "S2", "start_min": 10, "end_min": 20},
                                           {"id": "S1", "start_min": 0, "end_min": 10}]}]
        self.assertEqual(normalize_scenario(scenario)["chargers"][0]["slots"][0]["id"], "S1")
        scenario["chargers"][0]["slots"][0]["start_min"] = 9
        with self.assertRaisesRegex(ValueError, "overlap"):
            normalize_scenario(scenario)

    def test_sixty_stop_boundary_and_per_vehicle_limit(self):
        scenario = sixty_stop_scenario()
        self.assertEqual(len(scenario["deliveries"]), 60)
        scenario["deliveries"][10]["vehicle_id"] = "V1"
        with self.assertRaisesRegex(ValueError, "10 delivery stops"):
            normalize_scenario(scenario)
        scenario = sixty_stop_scenario()
        scenario["deliveries"].append({**scenario["deliveries"][-1], "id": "D61"})
        with self.assertRaises(ValueError):
            normalize_scenario(scenario)

    def test_prompt_contains_complete_source_registry_and_bounded_search_contract(self):
        prompt = scenario_prompt(sixty_stop_scenario())
        self.assertIn('"id":"D60"', prompt)
        self.assertIn("delivery_d60_deadline_min", prompt)
        self.assertIn("do not enumerate all permutations", prompt)
        self.assertIn("repeated charger visits", prompt)
        self.assertLessEqual(len(prompt.encode("utf-8")), 65_536)
        self.assertEqual(prompt, scenario_prompt(sixty_stop_scenario()))


class ScenarioControlTests(unittest.TestCase):
    def test_host_bindings_and_effective_snapshot_preserve_source(self):
        source = example_scenario()
        original = copy.deepcopy(source)
        control = numeric_control()
        applied = effective_scenario(source, [control], {"van_2_initial_charge_pct": 50})
        self.assertEqual(applied["vehicles"][1]["initial_charge_pct"], 50)
        self.assertEqual(source, original)
        self.assertEqual(numeric_bindings(source)["van_2_initial_charge_pct"], ["vehicles", "V2", "initial_charge_pct"])
        context = scenario_context(source, [control], {"van_2_initial_charge_pct": 50})
        self.assertEqual(set(context["bindings"]), {"van_2_initial_charge_pct"})
        self.assertEqual(context["effective_scenario"], applied)
        self.assertEqual(context["base_digest"], scenario_digest(source))
        self.assertNotEqual(context["base_digest"], context["effective_digest"])

    def test_defaults_normalized_ids_and_fractional_steps(self):
        control = numeric_control("electricity_price_per_kwh", default=0.2, minimum=0.1, maximum=0.5, step=0.1)
        control["id"] = control.pop("key")
        self.assertEqual(effective_scenario(example_scenario(), [control], {})["costs"]["electricity_per_kwh"], 0.2)
        self.assertEqual(effective_scenario(example_scenario(), [control], {control["id"]: 0.3})["costs"]["electricity_per_kwh"], 0.3)
        with self.assertRaisesRegex(ValueError, "step"):
            effective_scenario(example_scenario(), [control], {control["id"]: 0.25})

    def test_rejects_invented_geometry_controls_bad_defaults_and_unapproved_values(self):
        for controls, values in (
            ([numeric_control("charger_c1_x", default=2)], {}),
            ([numeric_control(default=40)], {}),
            ([numeric_control()], {"van_2_initial_charge_pct": True}),
            ([numeric_control()], {"van_2_initial_charge_pct": 101}),
            ([{**numeric_control(), "type": "string"}], {}),
            ([numeric_control(), numeric_control()], {}),
            ([], {"van_2_initial_charge_pct": 40}),
            ([{**numeric_control(), "allowed_values": [35, float("nan")]}], {}),
        ):
            with self.subTest(controls=controls, values=values):
                with self.assertRaises(ValueError):
                    effective_scenario(example_scenario(), controls, values)

    def test_slot_bindings_revalidate_timing_after_all_overrides(self):
        scenario = example_scenario()
        scenario["chargers"][0]["slots"] = [{"id": "S1", "start_min": 30, "end_min": 60}]
        controls = [numeric_control("charger_c1_slot_s1_start_min", 30, 0, 100),
                    numeric_control("charger_c1_slot_s1_end_min", 60, 0, 120)]
        applied = effective_scenario(scenario, controls, {controls[0]["key"]: 70, controls[1]["key"]: 90})
        self.assertEqual(applied["chargers"][0]["slots"][0]["start_min"], 70)
        with self.assertRaises(ValueError):
            effective_scenario(scenario, controls, {controls[0]["key"]: 70})


class ScenarioResultTests(unittest.TestCase):
    def test_valid_multiple_stops_multiple_depots_and_classical_replay(self):
        scenario = example_scenario()
        validate_scenario_result(scenario, direct_result(scenario))

    def test_effective_inputs_are_checked_instead_of_source_defaults(self):
        scenario = effective_scenario(example_scenario(), [numeric_control()], {"van_2_initial_charge_pct": 50})
        result = direct_result(scenario)
        validate_scenario_result(scenario, result)
        with self.assertRaisesRegex(ValueError, "initial battery"):
            validate_scenario_result(example_scenario(), result)

    def test_rejects_altered_scene_assignments_physics_service_cost_and_reference(self):
        scenario = example_scenario()
        mutations = (
            lambda r: r["simulation"]["depots"][0].update(position=[0, 0]),
            lambda r: r["simulation"]["chargers"][0].update(position=[0, 0]),
            lambda r: r["simulation"]["vans"][0].update(depot_id="P2"),
            lambda r: r["simulation"]["vans"][0].update(delivery_ids=["D1"]),
            lambda r: r["simulation"]["deliveries"].pop(),
            lambda r: r["simulation"]["vans"][0]["events"][0].update(battery_end_kwh=7.9),
            lambda r: r["simulation"]["vans"][0]["events"][0].update(end_min=0.5),
            lambda r: r["simulation"]["vans"][0]["events"][1].update(end_min=10),
            lambda r: r["simulation"]["vans"][0]["events"][-1]["path"].pop(),
            lambda r: r["simulation"]["vans"][0]["events"].pop(),
            lambda r: r["solution"]["cost_breakdown"].update(driving_cost=0),
            lambda r: r["objective"].update(value=0),
            lambda r: r["reference"]["simulation"]["vans"][1].update(depot_id="P1"),
            lambda r: r["reference"].update(objective_value=0),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                result = direct_result(scenario)
                mutate(result)
                with self.assertRaises(ValueError):
                    validate_scenario_result(scenario, result)

    def test_checks_all_sixty_stops_without_truncation(self):
        scenario = sixty_stop_scenario()
        result = direct_result(scenario)
        validate_scenario_result(scenario, result)
        # Drop an interior stop and its assignment, the middle of a model review
        # excerpt would otherwise be easy to miss. Keep all other events valid.
        van = result["simulation"]["vans"][3]
        van["delivery_ids"].remove("D35")
        van["events"] = [event for event in van["events"] if event.get("delivery_id") != "D35"]
        with self.assertRaisesRegex(ValueError, "assignments"):
            validate_scenario_result(scenario, result)

    def test_zero_distance_stops_and_idle_vehicle(self):
        scenario = example_scenario()
        for delivery in scenario["deliveries"]:
            vehicle = next(item for item in scenario["vehicles"] if item["id"] == delivery["vehicle_id"])
            delivery["position"] = next(item["position"] for item in scenario["depots"] if item["id"] == vehicle["depot_id"])
            delivery["service_min"] = 0
        scenario["vehicles"].append({"id": "V3", "depot_id": "P1", "initial_charge_pct": 100})
        validate_scenario_result(scenario, direct_result(scenario))

    def test_reserve_is_independent_of_claimed_verification(self):
        scenario = example_scenario()
        scenario["vehicles"][0]["initial_charge_pct"] = 16
        result = direct_result(scenario)
        self.assertTrue(result["solution"]["verification"]["feasible"])
        with self.assertRaisesRegex(ValueError, "reserve"):
            validate_scenario_result(scenario, result)

    def test_repeated_charging_in_adjacent_half_open_slots_and_capacity_cap(self):
        for initial in (40, 95):
            with self.subTest(initial=initial):
                scenario, result = charging_fixture(initial)
                validate_scenario_result(scenario, result)
        self.assertAlmostEqual(result["solution"]["cost_breakdown"]["electricity_cost"], 0.15)

    def test_rejects_partial_slot_wrong_energy_wrong_slot_and_hidden_wait(self):
        for mutate in (
            lambda r: r["simulation"]["vans"][0]["events"][2].update(end_min=14),
            lambda r: r["simulation"]["vans"][0]["events"][2].update(energy_kwh=2),
            lambda r: r["simulation"]["vans"][0]["events"][2].update(battery_end_kwh=6),
            lambda r: r["simulation"]["vans"][0]["events"][2].update(slot_id="S2"),
            lambda r: r["simulation"]["vans"][0]["events"].pop(1),
            lambda r: r["simulation"]["vans"][0]["events"][1].update(charger_id=[]),
        ):
            with self.subTest(mutation=mutate):
                scenario, result = charging_fixture()
                mutate(result)
                with self.assertRaises(ValueError):
                    validate_scenario_result(scenario, result)

    def test_rejects_two_vans_booking_the_same_charger_slot(self):
        scenario, result = charging_fixture()
        scenario["vehicles"].append({**scenario["vehicles"][0], "id": "V2"})
        scenario["deliveries"].append({**scenario["deliveries"][0], "id": "D2", "vehicle_id": "V2"})
        result["simulation"]["deliveries"].append({**result["simulation"]["deliveries"][0], "id": "D2"})
        second = copy.deepcopy(result["simulation"]["vans"][0])
        second.update(id="V2", delivery_ids=["D2"])
        second["events"][5]["delivery_id"] = "D2"
        result["simulation"]["vans"].append(second)
        with self.assertRaisesRegex(ValueError, "booking conflict"):
            validate_scenario_result(scenario, result)


if __name__ == "__main__":
    unittest.main()
