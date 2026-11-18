import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class LocalEvSolverTests(unittest.TestCase):
    def test_charge_range_produces_verified_schedules_from_local_samples(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"QR_KEY", "QR_ACCOUNT", "QR_BACKEND", "QUANTUM_BACKEND_URL"}
        }
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        scenarios = (
            (20, 4, 58.366667),
            (22, 4, 58.366667),
            (23, 5, 15.783333),
            (35, 5, 15.783333),
        )
        for charge, qubits, reference_cost in scenarios:
            with self.subTest(charge=charge):
                completed = subprocess.run(
                    [
                        sys.executable, "-m", "backend.ev_dispatch",
                        "--van-2-initial-charge", str(charge), "--engine", "local",
                    ],
                    cwd=REPO_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["controls"], {"van_2_initial_charge_pct": charge})
                self.assertEqual(result["objective"]["unit"], "USD")
                self.assertAlmostEqual(result["reference"]["objective_value"], reference_cost, places=6)
                classical = result["classical"]
                self.assertTrue(classical["optimality_proven"])
                self.assertEqual(classical["objective"], {
                    "value": reference_cost, "unit": "USD", "direction": "minimize",
                })
                self.assertEqual(classical["solution"]["verification"], {"feasible": True, "violations": []})
                self.assertEqual(classical["solution"]["vans"][1]["initial_soc_pct"], charge)
                self.assert_measured_solution(result["quantum"], result["solution"], qubits)
                self.assert_verified_schedule(result, charge)

    def assert_measured_solution(self, quantum, solution, qubits):
        self.assertEqual(quantum["engine"], "numpy-statevector")
        self.assertEqual(quantum["qubits"], qubits)
        self.assertEqual(quantum["shots"], 2048)
        self.assertEqual(sum(quantum["counts"].values()), 2048)
        self.assertGreater(quantum["circuit_runs"], 1)
        self.assertTrue(quantum["endianness_check_passed"])
        for bitstring, count in quantum["counts"].items():
            self.assertEqual(len(bitstring), qubits)
            self.assertLessEqual(set(bitstring), {"0", "1"})
            self.assertGreater(count, 0)

        best = quantum["best_bitstring"]
        self.assertGreater(quantum["counts"][best], 0)
        mapping = quantum["qubit_mapping"]
        decoded = [mapping[f"q{qubit}"] for qubit, bit in enumerate(reversed(best)) if bit == "1"]
        self.assertEqual(decoded, solution["selected_options"])

    def assert_verified_schedule(self, result, charge):
        solution = result["solution"]
        verification = solution["verification"]
        total = result["objective"]["value"]
        reference = result["reference"]["objective_value"]
        self.assertTrue(verification["feasible"])
        self.assertEqual(verification["violations"], [])
        self.assertAlmostEqual(verification["independent_cost_breakdown"]["total_cost"], total, places=6)
        self.assertAlmostEqual(verification["optimality_gap_usd"], total - reference, places=6)
        self.assertGreaterEqual(total + 1e-6, reference)

        vans = {van["van_id"]: van for van in solution["vans"]}
        self.assertEqual(set(vans), {1, 2})
        self.assertEqual(vans[1]["initial_soc_kwh"], 4)
        self.assertEqual(vans[2]["initial_soc_kwh"], 20 * charge / 100)
        booked_slots = [van["charger_slot"] for van in vans.values() if van["charger_slot"] is not None]
        self.assertEqual(len(booked_slots), len(set(booked_slots)))
        if charge == 20:
            self.assertEqual(set(booked_slots), {1, 2})

        for van_id, destination in ((1, [0, 0]), (2, [5, 0])):
            legs = vans[van_id]["legs"]
            self.assertEqual(legs[0]["start_coord"], [2, 2])
            self.assertEqual(legs[0]["start_min"], 0)
            self.assertEqual(legs[-1]["end_coord"], [2, 2])
            self.assertLessEqual(legs[-1]["end_min"], 120)
            deliveries = [leg for leg in legs if leg["leg_type"] == "deliver"]
            self.assertEqual([leg["end_coord"] for leg in deliveries], [destination])
            self.assertGreaterEqual(min(leg["soc_end_kwh"] for leg in legs), 3 - 1e-7)


if __name__ == "__main__":
    unittest.main()
