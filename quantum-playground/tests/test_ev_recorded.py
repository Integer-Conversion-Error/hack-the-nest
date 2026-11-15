import json
import unittest
from unittest.mock import patch

from ev_recorded import RECORDED_PROMPT, RECORDING_DIR, recorded_handoff
from fleet_handoff import InvalidHandoff, validate_replay


CONTROL = "van_2_initial_charge_pct"


def read_recording(filename):
    return json.loads((RECORDING_DIR / filename).read_text(encoding="utf-8"))


class RecordedHandoffTests(unittest.TestCase):
    def assert_matches_backend(self, result, output):
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["provenance"], "recorded")
        self.assertEqual(result["effective_values"], output["controls"])
        simulation = result["simulation"]
        self.assertEqual(validate_replay(simulation), 2)
        self.assertEqual(simulation["counts"], output["quantum"]["counts"])
        self.assertEqual(sum(simulation["counts"].values()), result["quantum"]["shots"])
        self.assertEqual(result["quantum"]["actual_qubits"], output["quantum"]["qubits"])
        self.assertEqual(result["quantum"]["best_bitstring"], output["quantum"]["best_bitstring"])
        evaluation = result["evaluation"]
        self.assertEqual(evaluation["selected_options"], output["solution"]["selected_options"])
        self.assertEqual(evaluation["objective_value"], output["objective"]["value"])
        self.assertEqual(evaluation["reference_value"], output["reference"]["objective_value"])
        self.assertEqual(evaluation["gap"], output["solution"]["verification"]["optimality_gap_usd"])

        for van, option in zip(simulation["vans"], output["solution"]["vans"], strict=True):
            self.assertEqual(van["option_id"], option["option_id"])
            self.assertEqual(van["initial_battery_kwh"], option["initial_soc_kwh"])
            for event, leg in zip(van["events"], option["legs"], strict=True):
                self.assertEqual(event["type"], leg["leg_type"])
                self.assertEqual(event["position"], leg["start_coord"])
                self.assertEqual(event["start_min"], leg["start_min"])
                self.assertEqual(event["end_min"], leg["end_min"])
                self.assertEqual(event["battery_start_kwh"], leg["soc_start_kwh"])
                self.assertEqual(event["battery_end_kwh"], leg["soc_end_kwh"])
                if leg["leg_type"] == "drive":
                    self.assertEqual(event["path"], leg["path"])

    def test_default_uses_the_final_go_handoff(self):
        handoff = recorded_handoff({})
        backend = read_recording("handoff.json")
        result = handoff["result"]
        self.assertEqual(handoff["problem"]["original_prompt"], RECORDED_PROMPT)
        self.assertEqual(result["source"]["file"], "handoff.json")
        self.assertEqual(result["source"]["workspace_id"], backend["workspace_id"])
        self.assert_matches_backend(result, backend["results"])

        simulation = result["simulation"]
        self.assertEqual(simulation["grid"], {"width": 6, "height": 6, "block_km": 0.5})
        self.assertEqual(simulation["depot"], [2, 2])
        self.assertEqual(simulation["chargers"][0]["position"], [2, 1])
        self.assertEqual(simulation["start_hour"], 8)
        self.assertEqual(simulation["duration_min"], 120)
        self.assertEqual(simulation["deliveries"], [
            {"id": "D1", "position": [0, 0], "deadline_min": 90},
            {"id": "D2", "position": [5, 0], "deadline_min": 60},
        ])
        self.assertEqual(simulation["counts"]["00101"], 310)
        self.assertEqual(result["quantum"]["actual_qubits"], 5)
        self.assertEqual(result["evaluation"]["objective_value"], 15.783333)

    def test_alternative_uses_the_verified_test_run(self):
        initial = recorded_handoff({})
        handoff = recorded_handoff({CONTROL: 20}, initial)
        backend = next(run["results"] for run in read_recording("test.json")["runs"] if run["controls"] == {CONTROL: 20})
        result = handoff["result"]
        self.assertEqual(result["source"]["file"], "test.json")
        self.assert_matches_backend(result, backend)

        self.assertEqual(handoff["run_id"], initial["run_id"])
        self.assertEqual(handoff["plan_id"], initial["plan_id"])
        self.assertNotEqual(handoff["attempt_id"], initial["attempt_id"])
        self.assertEqual(result["simulation"]["counts"]["0110"], 491)
        self.assertEqual(result["quantum"]["actual_qubits"], 4)
        self.assertEqual(result["evaluation"]["objective_value"], 58.366667)
        self.assertEqual(result["evaluation"]["late_minutes"], {"D1": 8, "D2": 9})
        self.assertEqual(result["simulation"]["chargers"][0]["slots"], [
            {"id": "slot_1", "start_min": 30, "end_min": 60, "option_id": "Van2_Slot1"},
            {"id": "slot_2", "start_min": 60, "end_min": 90, "option_id": "Van1_Slot2"},
        ])

    def test_rejects_an_unverified_test_recording(self):
        test = read_recording("test.json")
        test["semantics"]["verdict"] = "mismatch"
        with patch("ev_recorded._read_json", return_value=test):
            with self.assertRaises(InvalidHandoff):
                recorded_handoff({CONTROL: 20})

    def test_rejects_a_value_without_a_recording(self):
        with self.assertRaises(ValueError):
            recorded_handoff({CONTROL: 30})


if __name__ == "__main__":
    unittest.main()
