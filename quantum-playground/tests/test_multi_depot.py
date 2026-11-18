import copy
import importlib.util
import unittest

from fleet_handoff import (
    InvalidHandoff, depot_for, normalize_classical_replay, normalize_handoff,
    replay_depots, validate_replay,
)
from pygame_renderer import map_point, render_png, scene_layout, state_at


def multi_depot_scene():
    battery = 10
    events = []

    def event(kind, start, end, position, **fields):
        nonlocal battery
        end_battery = fields.pop("battery_end_kwh", battery)
        events.append({"type": kind, "start_min": start, "end_min": end, "position": position,
                       "battery_start_kwh": battery, "battery_end_kwh": end_battery, **fields})
        battery = end_battery

    event("drive", 0, 1, [0, 0], path=[[0, 0], [1, 0]], battery_end_kwh=9.9)
    event("deliver", 1, 2, [1, 0], delivery_id="D1")
    event("drive", 2, 3, [1, 0], path=[[1, 0], [2, 0]], battery_end_kwh=9.8)
    event("deliver", 3, 4, [2, 0], delivery_id="D2")
    event("drive", 4, 6, [2, 0], path=[[2, 0], [1, 0], [0, 0]], battery_end_kwh=9.6)
    return {
        "kind": "ev_fleet", "grid": {"width": 6, "height": 6, "block_km": 0.5},
        "depots": [{"id": "P1", "position": [0, 0]}, {"id": "P2", "position": [5, 5]}],
        "start_hour": 8, "duration_min": 120, "battery_capacity_kwh": 20, "reserve_pct": 15,
        "chargers": [],
        "deliveries": [{"id": "D1", "position": [1, 0], "deadline_min": 90},
                       {"id": "D2", "position": [2, 0], "deadline_min": 90}],
        "vans": [
            {"id": "V1", "depot_id": "P1", "initial_battery_kwh": 10,
             "delivery_ids": ["D1", "D2"], "events": events},
            {"id": "V2", "depot_id": "P2", "initial_battery_kwh": 10,
             "delivery_ids": [], "events": []},
        ],
    }


def envelope(scene, version=2):
    identity = {"run_id": "run", "plan_id": "plan", "attempt_id": "attempt"}
    return {
        "schema_version": version, **identity, "scene_type": "ev_fleet", "controls": [],
        "problem": {"title": "Two depots", "statement": "Two stops for one vehicle.", "original_prompt": "Fleet"},
        "result": {**identity, "status": "verified", "provenance": "live", "effective_values": {},
                   "simulation": scene,
                   "evaluation": {"feasible": True, "objective_value": 1.5, "costs": {"driving": 1.5},
                                  "delivered": 2}},
    }


class MultiDepotTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("pygame"), "Pygame is not installed")
    def test_offscreen_frame_renders_each_home_depot(self):
        scene = multi_depot_scene()
        frame = render_png(scene, 0, resolution=1)
        self.assertTrue(frame.startswith(b"\x89PNG\r\n\x1a\n"))
        scene["depots"][1]["position"] = [4, 5]
        self.assertNotEqual(frame, render_png(scene, 0, resolution=1))

    def test_multiple_stops_and_home_depots_survive_v2_handoff(self):
        scene = multi_depot_scene()
        self.assertEqual(validate_replay(scene), 2)
        view = normalize_handoff(envelope(scene), "live", "Fleet")
        self.assertIs(view["simulation"], scene)
        for van, expected in zip(scene["vans"], ([0, 0], [5, 5])):
            home = depot_for(scene, van)
            self.assertEqual(home, expected)
            self.assertEqual(state_at(van, 0, home)[0], expected)
            self.assertEqual(state_at(van, 120, home)[0], expected)
        targets = scene_layout(scene, 0)["targets"]
        for van in scene["vans"]:
            target = next(item for item in targets if item["kind"] == "van" and item["id"] == van["id"])
            x, y, width, height = target["rect"]
            self.assertEqual((x + width / 2, y + height / 2), map_point(scene, depot_for(scene, van)))

    def test_legacy_shared_depot_keeps_its_original_shape(self):
        scene = multi_depot_scene()
        scene["depot"] = scene.pop("depots")[0]["position"]
        for van in scene["vans"]:
            del van["depot_id"]
        self.assertEqual(validate_replay(scene), 2)
        self.assertEqual(replay_depots(scene), [{"id": "P1", "position": [0, 0]}])
        self.assertNotIn("depots", normalize_handoff(envelope(scene, 1), "live", "Fleet")["simulation"])

    def test_rejects_missing_unknown_duplicate_or_outside_home_depots(self):
        for change in (lambda scene: scene["vans"][0].pop("depot_id"),
                       lambda scene: scene["vans"][0].update(depot_id="unknown"),
                       lambda scene: scene["depots"][1].update(id="P1"),
                       lambda scene: scene["depots"][1].update(position=[6, 5])):
            with self.subTest(change=change):
                scene = multi_depot_scene()
                change(scene)
                with self.assertRaises(InvalidHandoff):
                    validate_replay(scene)
        with self.assertRaisesRegex(InvalidHandoff, "schema_version 2"):
            normalize_handoff(envelope(multi_depot_scene(), 1), "live", "Fleet")

    def test_classical_replay_must_preserve_home_depot_and_stop_assignments(self):
        quantum = multi_depot_scene()
        self.assertEqual(normalize_classical_replay(copy.deepcopy(quantum), quantum), quantum)
        wrong_home = copy.deepcopy(quantum)
        wrong_home["vans"][1]["depot_id"] = "P1"
        with self.assertRaisesRegex(InvalidHandoff, "home depots"):
            normalize_classical_replay(wrong_home, quantum)

        quantum["depots"][1]["position"] = [0, 0]
        classical = copy.deepcopy(quantum)
        first, second = classical["vans"]
        first["events"], second["events"] = second["events"], first["events"]
        first["delivery_ids"], second["delivery_ids"] = second["delivery_ids"], first["delivery_ids"]
        self.assertEqual(validate_replay(classical), 2)
        with self.assertRaisesRegex(InvalidHandoff, "delivery assignments"):
            normalize_classical_replay(classical, quantum)
