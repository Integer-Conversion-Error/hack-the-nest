import unittest

from ev_reference import PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, applied_values, normalize_controls, normalize_handoff


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
                  "rerun_mode": "rebuild", "unit": None, "group": None}
        controls = normalize_controls([legacy])
        self.assertEqual(applied_values(controls, {"charge": 35}, {"charge": 20.0}), {"charge": 20})
        self.assertIs(type(applied_values(controls, {"charge": 35}, {"charge": 20.0})["charge"]), int)
        self.assertNotIn("group", controls[0])
        self.assertEqual(controls[0]["unit"], "")

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


if __name__ == "__main__":
    unittest.main()
