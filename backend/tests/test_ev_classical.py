import unittest
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.ev_dispatch.classical import solve_classical
from backend.ev_dispatch.dispatch_model import generate_feasible_options


class ClassicalEvDispatchTests(unittest.TestCase):
    def test_classical_cli_runs_without_site_packages_or_quantum_credentials(self):
        completed = subprocess.run(
            [sys.executable, "-S", "-m", "backend.ev_dispatch", "--solver", "classical", "--van-2-initial-charge", "22"],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["objective"]["value"], 58.366667)
        self.assertTrue(result["optimality_proven"])
        self.assertNotIn("quantum", result)

    def test_exact_plan_changes_at_the_direct_route_battery_threshold(self):
        cases = (
            (22, 58.366667, ["Van1_Slot2", "Van2_Slot1"], {"slot_1": "Van2_Slot1", "slot_2": "Van1_Slot2"}),
            (23, 15.783333, ["Van1_Slot1", "Van2_Direct"], {"slot_1": "Van1_Slot1", "slot_2": None}),
        )

        for charge, total, option_ids, bookings in cases:
            with self.subTest(charge=charge):
                result = solve_classical(charge)

                self.assertEqual(result["controls"], {"van_2_initial_charge_pct": charge})
                self.assertEqual(result["objective"], {"value": total, "unit": "USD", "direction": "minimize"})
                self.assertTrue(result["optimality_proven"])
                solution = result["solution"]
                self.assertEqual(solution["verification"], {"feasible": True, "violations": []})
                self.assertEqual(solution["selected_options"], option_ids)
                self.assertEqual(solution["charger_bookings"], bookings)
                self.assertEqual([van["option_id"] for van in solution["vans"]], option_ids)
                self.assertEqual(solution["vans"][1]["initial_soc_pct"], charge)
                for van in solution["vans"]:
                    self.assertGreaterEqual(van["min_soc_pct"], 15)
                    self.assertEqual(van["legs"][-1]["end_coord"], [2, 2])

    def test_decoded_schedule_cost_is_checked_against_independent_calculation(self):
        options, metadata = generate_feasible_options(35)
        options = [
            replace(option, driving_cost=option.driving_cost + 1)
            if option.option_id == "Van2_Direct" else option
            for option in options
        ]

        with patch("backend.ev_dispatch.classical.generate_feasible_options", return_value=(options, metadata)):
            with self.assertRaisesRegex(RuntimeError, "independent cost calculation"):
                solve_classical(35)
