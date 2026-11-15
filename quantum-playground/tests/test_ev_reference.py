import json
import unittest

from ev_reference import CONTROLS, CONTROL_KEY, PROMPT, reference_handoff


class ReferenceHandoffTests(unittest.TestCase):
    def test_default_replay_identifies_classical_reference(self):
        handoff = json.loads(json.dumps(reference_handoff({})))
        result = handoff["result"]
        self.assertEqual(handoff["schema_version"], 1)
        self.assertEqual(handoff["scene_type"], "ev_fleet")
        self.assertEqual(handoff["problem"]["original_prompt"], PROMPT)
        self.assertEqual(handoff["controls"], CONTROLS)
        self.assertEqual(CONTROLS[0]["allowed_values"], [35, 20])
        self.assertEqual(CONTROLS[0]["rerun_mode"], "rebuild")
        for key in ("run_id", "plan_id", "attempt_id"):
            self.assertEqual(handoff[key], result[key])
        self.assertEqual(result["effective_values"], {CONTROL_KEY: 35})
        self.assertEqual(result["status"], "reference")
        self.assertEqual(result["provenance"], "classical_reference")
        self.assertEqual(result["evaluation"]["delivered"], 2)
        self.assertEqual(result["quantum"], {"binary_variables": 5, "actual_qubits": None})
        self.assertEqual(result["circuits"], [])
        self.assertEqual(result["simulation"]["counts"], {})

    def test_recalculation_replays_expected_bookings_delivery_and_cost(self):
        cases = (
            (35, {"V1": (30, 60)}, {"D1": 68, "D2": 10}, 947, 29, {"D1": 0, "D2": 0}),
            (20, {"V1": (60, 90), "V2": (30, 60)}, {"D1": 98, "D2": 69}, 3502, 88, {"D1": 8, "D2": 9}),
        )
        previous = None
        for setting, expected_bookings, expected_deliveries, units, waiting, late in cases:
            with self.subTest(setting=setting):
                result = reference_handoff({CONTROL_KEY: setting}, previous)["result"]
                if previous is not None:
                    self.assertEqual(result["run_id"], previous["run_id"])
                    self.assertEqual(result["plan_id"], previous["plan_id"])
                    self.assertNotEqual(result["attempt_id"], previous["attempt_id"])
                self.assertEqual(result["effective_values"], {CONTROL_KEY: setting})
                self.assertEqual(result["quantum"]["binary_variables"], 5 if setting == 35 else 4)
                previous = result

                bookings = {}
                deliveries = {}
                for van in result["simulation"]["vans"]:
                    for event in van["events"]:
                        if event["type"] == "charge":
                            bookings[van["id"]] = (event["start_min"], event["end_min"])
                        if event["type"] == "deliver":
                            deliveries[event["delivery_id"]] = event["end_min"]
                self.assertEqual(bookings, expected_bookings)
                self.assertEqual(deliveries, expected_deliveries)

                evaluation = result["evaluation"]
                self.assertTrue(evaluation["feasible"])
                self.assertEqual(evaluation["objective_units_60"], units)
                self.assertAlmostEqual(evaluation["objective_value"], units / 60)
                self.assertAlmostEqual(evaluation["objective_value"], sum(evaluation["costs"].values()))
                self.assertAlmostEqual(evaluation["costs"]["waiting"], waiting * 25 / 60)
                self.assertEqual(evaluation["costs"]["late"], sum(late.values()))
                self.assertEqual(evaluation["late_minutes"], late)

    def test_timed_routes_have_continuous_paths_and_batteries(self):
        for setting in (35, 20):
            with self.subTest(setting=setting):
                sim = reference_handoff({CONTROL_KEY: setting})["result"]["simulation"]
                for van in sim["vans"]:
                    previous_end = 0
                    position = sim["depot"]
                    energy = van["initial_battery_kwh"]
                    for event in van["events"]:
                        self.assertEqual(event["start_min"], previous_end)
                        self.assertGreater(event["end_min"], previous_end)
                        self.assertEqual(event["position"], position)
                        self.assertAlmostEqual(event["battery_start_kwh"], energy)
                        if event["type"] == "drive":
                            path = event["path"]
                            self.assertEqual(path[0], position)
                            self.assertEqual(event["end_min"] - event["start_min"], len(path) - 1)
                            for start, end in zip(path, path[1:]):
                                self.assertEqual(sum(abs(x - y) for x, y in zip(start, end)), 1)
                            position = path[-1]
                            energy -= (len(path) - 1) * 0.15
                        if event["type"] == "charge":
                            energy += 5
                        self.assertAlmostEqual(energy, event["battery_end_kwh"])
                        self.assertGreaterEqual(energy, 3)
                        self.assertLessEqual(energy, 20)
                        previous_end = event["end_min"]
                    self.assertEqual(position, sim["depot"])
                    self.assertLessEqual(previous_end, sim["duration_min"])

    def test_invalid_controls_are_rejected(self):
        for values in ({CONTROL_KEY: 30}, {CONTROL_KEY: "35"}, {CONTROL_KEY: 35.0}, {CONTROL_KEY: True}, {"initial_2": 35}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, CONTROL_KEY):
                reference_handoff(values)


if __name__ == "__main__":
    unittest.main()
