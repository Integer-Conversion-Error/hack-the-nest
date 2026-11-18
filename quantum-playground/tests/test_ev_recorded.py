import json
import unittest
from unittest.mock import patch

from ev_recorded import RECORDED_PROMPT, RECORDING_DIR, project_ev_result, recorded_handoff
from fleet_handoff import InvalidHandoff, validate_replay
from backend.ev_dispatch.classical import solve_classical
from backend.ev_dispatch.dispatch_model import generate_feasible_options, simulate_schedule_timeline
from backend.ev_dispatch.verifier import independent_evaluate_pair


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

        classical = result["classical_simulation"]
        self.assertEqual(validate_replay(classical), 2)
        self.assertEqual(classical["counts"], {})
        self.assertEqual([van["option_id"] for van in classical["vans"]], output["reference"]["selected_options"])
        self.assertEqual(classical["vans"][1]["initial_battery_kwh"], simulation["vans"][1]["initial_battery_kwh"])

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

    def test_recorded_measurements_remain_playable_if_exact_routes_cannot_be_reconstructed(self):
        output = read_recording("handoff.json")["results"]
        output["reference"]["selected_options"] = ["Van1_Slot2", "Van2_Direct"]

        with patch("ev_recorded._recording", return_value=(output, "handoff.json", "Final GO handoff")):
            result = recorded_handoff({})["result"]

        self.assertNotIn("classical_simulation", result)
        self.assertEqual(result["simulation"]["counts"], output["quantum"]["counts"])

    def test_live_projects_the_provided_exact_schedule_beside_a_different_sampled_schedule(self):
        output = read_recording("handoff.json")["results"]
        output["classical"] = solve_classical(35)
        sampled = independent_evaluate_pair("Slot 2", "Direct", 35)
        self.assertTrue(sampled["feasible"])
        options, _ = generate_feasible_options(35)
        selected = [option for option in options if option.option_id in sampled["selected_options"]]
        timeline = simulate_schedule_timeline(selected)
        costs = {key: round(value, 6) for key, value in sampled["cost_breakdown"].items()}
        output["solution"].update(
            vans=[option.to_dict() for option in selected],
            selected_options=sampled["selected_options"],
            charger_bookings=timeline.charger_bookings,
            cost_breakdown=costs,
        )
        output["solution"]["verification"].update(
            optimality_gap_usd=20.5,
            matches_classical_optimum=False,
            independent_cost_breakdown=costs,
        )
        output["objective"]["value"] = costs["total_cost"]
        output["reference"]["matches_quantum_solution"] = False
        # The historical run measured this feasible assignment as bitstring 00110.
        quantum = output["quantum"]
        quantum["best_bitstring"] = "00110"
        quantum["best_bitstring_share"] = round(quantum["counts"]["00110"] / quantum["shots"], 6)
        identity = {"run_id": "run", "plan_id": "plan", "attempt_id": "attempt"}
        with patch("ev_recorded.solve_classical", side_effect=AssertionError("Use the provided live result.")):
            result = project_ev_result(output, identity, provenance="live", explanation="Verified.")["result"]

        measured, classical = result["simulation"], result["classical_simulation"]
        self.assertEqual(validate_replay(measured), 2)
        self.assertEqual(validate_replay(classical), 2)
        self.assertEqual(measured["vans"][0]["option_id"], "Van1_Slot2")
        self.assertEqual(classical["vans"][0]["option_id"], "Van1_Slot1")
        measured_charge = next(event for event in measured["vans"][0]["events"] if event["type"] == "charge")
        classical_charge = next(event for event in classical["vans"][0]["events"] if event["type"] == "charge")
        self.assertEqual(measured_charge["start_min"], 60)
        self.assertEqual(classical_charge["start_min"], 30)
        self.assertGreater(measured["counts"]["00110"], 0)
        self.assertEqual(classical["counts"], {})
        self.assertEqual(result["evaluation"]["gap"], 20.5)


if __name__ == "__main__":
    unittest.main()
