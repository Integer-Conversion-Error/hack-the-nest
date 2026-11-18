import unittest

from pipeline_live import project_result


class PipelineComparisonTests(unittest.TestCase):
    def evaluation(self, reference):
        return self.projected_result(reference)["evaluation"]

    def projected_result(self, reference):
        artifact = {"problem": {"title": "Fleet", "summary": "Dispatch one van."}, "controls": []}
        identity = {"run_id": "run", "plan_id": "plan", "attempt_id": "attempt"}
        output = {
            "status": "ok",
            "controls": {},
            "objective": {"value": 20.5, "unit": "USD"},
            "solution": {
                "verification": {"feasible": True, "violations": []},
                "cost_breakdown": {"driving": 20.5},
            },
            "simulation": {"vans": [{"events": [{"type": "deliver"}]}]},
            "quantum": {"counts": {"10": 12}},
            "reference": reference,
        }
        return project_result(artifact, output, "Dispatch one van.", identity)["result"]

    def test_projects_verified_exact_or_heuristic_reference(self):
        for optimality_proven, method, value in ((True, "Enumeration", 18.5), (False, "Greedy", 23.5)):
            with self.subTest(method=method):
                reference = {
                    "method": method,
                    "objective_value": value,
                    "feasible": True,
                    "optimality_proven": optimality_proven,
                    "selected_options": ["Van 1 direct"],
                }
                evaluation = self.evaluation(reference)
                self.assertEqual(evaluation["classical"], reference)
                self.assertEqual(evaluation["reference_value"], value)
                self.assertEqual(evaluation["gap"], 20.5 - value)

    def test_legacy_value_is_not_promoted_to_verified_baseline(self):
        evaluation = self.evaluation({"method": "Enumeration", "value": 18.5})
        self.assertNotIn("classical", evaluation)
        self.assertEqual(evaluation["reference_value"], 18.5)
        self.assertEqual(evaluation["gap"], 2.0)

    def test_lone_bound_does_not_compare_as_a_feasible_cost(self):
        evaluation = self.evaluation({"method": "Relaxation", "bound_value": 14})
        self.assertNotIn("classical", evaluation)
        self.assertNotIn("reference_value", evaluation)
        self.assertNotIn("gap", evaluation)

    def test_projects_own_replay_only_for_a_verified_classical_schedule(self):
        simulation = {"vans": [{"events": [{"type": "drive"}, {"type": "deliver"}]}]}
        reference = {
            "method": "Enumeration",
            "objective_value": 18.5,
            "feasible": True,
            "optimality_proven": True,
            "simulation": simulation,
        }
        result = self.projected_result(reference)
        self.assertEqual(result["classical_simulation"], simulation)
        self.assertNotEqual(result["classical_simulation"]["vans"], result["simulation"]["vans"])
        self.assertNotIn("counts", result["classical_simulation"])
        self.assertEqual(result["simulation"]["counts"], {"10": 12})

        self.assertNotIn("classical_simulation", self.projected_result({**reference, "feasible": False}))
