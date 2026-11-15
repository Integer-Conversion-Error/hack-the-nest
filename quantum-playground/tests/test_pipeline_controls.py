import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from ev_reference import PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, applied_values, normalize_controls, normalize_handoff, validate_replay


def definition(key, kind, default, **constraints):
    return {"key": key, "label": key.replace("_", " ").capitalize(), "type": kind,
            "default": default, "rerun_mode": "rebuild", **constraints}


class PipelineControlsTests(unittest.TestCase):
    def setUp(self):
        self.rate = definition("tariff", "number", 0.25, minimum=0.05, maximum=1.05,
                               step=0.10, unit="$/kWh", group="Costs")
        self.count = definition("capacity", "integer", 3, minimum=1, maximum=7, step=2)
        self.toggle = definition("allow_waiting", "boolean", True)
        self.pick = definition("charger", "string", "depot", allowed_values=["depot", "route"])

    def test_applies_typed_overrides_and_preserves_control_metadata(self):
        controls = normalize_controls([self.rate, self.count, self.toggle, self.pick])
        defaults = {"tariff": 0.25, "capacity": 3, "allow_waiting": True, "charger": "depot"}
        self.assertEqual(
            applied_values(controls, defaults, {"tariff": 0.35, "capacity": 5, "allow_waiting": False, "charger": "route"}),
            {"tariff": 0.35, "capacity": 5, "allow_waiting": False, "charger": "route"},
        )
        self.assertEqual(controls[0]["group"], "Costs")
        self.assertEqual(controls[0]["unit"], "$/kWh")
        self.assertIsNone(controls[2]["allowed_values"])
        self.assertEqual(normalize_controls(controls), controls)
        for invalid in ({"tariff": 0.30}, {"tariff": 1.15}, {"tariff": True}, {"capacity": 5.0},
                        {"allow_waiting": 1}, {"charger": "other"}, {"unknown": 2}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    applied_values(controls, defaults, invalid)

    def test_rejects_defaults_and_constraints_outside_approved_type(self):
        for broken in (
            {**self.rate, "default": 0.3},
            {**self.rate, "step": 0},
            {**self.rate, "allowed_values": [0.15, 0.25]},
            {**self.count, "maximum": 6.5},
            {**self.toggle, "allowed_values": [True, 1]},
            {**self.pick, "allowed_values": ["depot", "depot"]},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(InvalidHandoff):
                    normalize_controls([broken])

    def test_parses_legacy_numeric_values_and_optional_metadata(self):
        legacy = {"id": "charge", "label": "Charge", "default": 35, "allowed_values": [35, 20],
                  "rerun_mode": "rebuild", "unit": None, "group": " "}
        controls = normalize_controls([legacy])
        self.assertEqual(applied_values(controls, {"charge": 35}, {"charge": 20.0}), {"charge": 20})
        self.assertIs(type(applied_values(controls, {"charge": 35}, {"charge": 20.0})["charge"]), int)
        self.assertNotIn("group", controls[0])
        self.assertEqual(controls[0]["unit"], "")

    @unittest.skipUnless(find_spec("pydantic"), "The planner dependencies are required.")
    def test_normalizes_planner_controls_before_handoff_validation(self):
        repo_root = Path(__file__).resolve().parents[2]
        with patch.object(sys, "path", [str(repo_root), *sys.path]):
            from backend.plan import PlanControl

        draft = PlanControl.model_validate(definition(
            "electricity_cost", "number", 0.35, allowed_values=[0.35, 0.55, 0.35], group=" ",
        ))
        controls = normalize_controls([draft.model_dump(mode="json"), self.rate])
        self.assertNotIn("group", controls[0])
        self.assertEqual(controls[0]["allowed_values"], [0.35, 0.55])
        self.assertEqual(controls[1]["group"], "Costs")
        defaults = {"electricity_cost": 0.35, "tariff": self.rate["default"]}
        self.assertEqual(applied_values(controls, defaults, {"electricity_cost": 0.55}), {
            "electricity_cost": 0.55, "tariff": 0.25,
        })

    def test_checks_typed_effective_settings_on_a_verified_handoff(self):
        first = reference_handoff({})
        first["controls"] = [self.rate, self.toggle, self.pick]
        first["result"].update(status="verified", provenance="live",
                               effective_values={"tariff": 0.25, "allow_waiting": True, "charger": "depot"})
        previous = normalize_handoff(first, "live", PROMPT)
        rerun = reference_handoff({}, first)
        rerun["controls"] = first["controls"]
        expected = {"tariff": 0.15, "allow_waiting": False, "charger": "route"}
        rerun["result"].update(status="verified", provenance="live", effective_values=expected)
        current = normalize_handoff(rerun, "live", PROMPT, previous, expected_values=expected)
        self.assertEqual(current["effective_values"], expected)

        rerun["result"]["effective_values"] = {**expected, "allow_waiting": 0}
        with self.assertRaisesRegex(InvalidHandoff, "effective values"):
            normalize_handoff(rerun, "live", PROMPT, previous, expected_values=expected)
        rerun["result"]["effective_values"] = expected
        rerun["controls"] = [{**self.rate, "maximum": 1.25}, self.toggle, self.pick]
        with self.assertRaisesRegex(InvalidHandoff, "Approved controls changed"):
            normalize_handoff(rerun, "live", PROMPT, previous, expected_values=expected)

    def test_replay_accepts_six_vans_on_a_twelve_by_twelve_grid(self):
        simulation = {
            "kind": "ev_fleet", "grid": {"width": 12, "height": 12, "block_km": 0.5},
            "depot": [0, 0], "start_hour": 8, "duration_min": 120,
            "battery_capacity_kwh": 20, "reserve_pct": 15,
            "chargers": [], "deliveries": [], "vans": [],
        }
        for number in range(1, 7):
            delivery_id = f"D{number}"
            destination = [number, 0]
            battery = 10 - 0.15 * number
            simulation["deliveries"].append({"id": delivery_id, "position": destination, "deadline_min": 60})
            simulation["vans"].append({
                "id": f"V{number}", "initial_battery_kwh": 10, "delivery_ids": [delivery_id],
                "events": [
                    {"type": "drive", "start_min": 0, "end_min": number, "position": [0, 0],
                     "path": [[x, 0] for x in range(number + 1)],
                     "battery_start_kwh": 10, "battery_end_kwh": battery},
                    {"type": "deliver", "start_min": number, "end_min": number + 5,
                     "position": destination, "delivery_id": delivery_id,
                     "battery_start_kwh": battery, "battery_end_kwh": battery},
                ],
            })

        self.assertEqual(validate_replay(simulation), 6)
        simulation["vans"].append({"id": "V7", "initial_battery_kwh": 10, "delivery_ids": [], "events": []})
        with self.assertRaisesRegex(InvalidHandoff, "one to six vans"):
            validate_replay(simulation)


if __name__ == "__main__":
    unittest.main()
