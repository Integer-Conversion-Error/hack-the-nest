"""Authoritative draft checks, including compatibility with the real JS solvers."""

import copy
import json
import math
from pathlib import Path
import shutil
import subprocess
import unittest

from formulation_contracts import domain_contract, model_contracts, normalize_draft


REPO = Path(__file__).resolve().parents[2]
DOMAINS = ("facility", "workforce", "production", "timetable", "energy", "projects", "teams")


@unittest.skipUnless(shutil.which("node"), "Node is needed to compare the actual local solver input shapes.")
class FormulationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = """const ids=JSON.parse(process.argv[1]);const result={};
        for(const id of ids) result[id]=require('./quantum-playground/web/problems/'+id+'.js').seed();
        process.stdout.write(JSON.stringify(result));"""
        cls.seeds = json.loads(subprocess.check_output(["node", "-e", script, json.dumps(DOMAINS)], cwd=REPO, text=True))
        cls.seeds["facility"].pop("durations")

    def seed(self, domain):
        return copy.deepcopy(self.seeds[domain])

    def test_seven_real_editor_seeds_round_trip_detached_and_canonical(self):
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                source = self.seed(domain)
                before = copy.deepcopy(source)
                normalized = normalize_draft(domain, source)
                self.assertEqual(normalize_draft(domain, normalized), normalized)
                self.assertEqual(source, before)
                self.assertIsNot(normalized, source)
                self.assertTrue(json.dumps(normalized, allow_nan=False))

    def test_normalized_drafts_pass_actual_javascript_solvers(self):
        inputs = {domain: normalize_draft(domain, self.seed(domain)) for domain in DOMAINS}
        script = """const inputs=JSON.parse(process.argv[1]), result={};
        for(const [id,input] of Object.entries(inputs)) {
          const api=require('./quantum-playground/web/problems/'+id+'.js');
          if(id==='facility')input.durations=api.seed().durations;
          const solved=api.solve(input);result[id]=solved.status||String(solved.feasible);
        }process.stdout.write(JSON.stringify(result));"""
        result = json.loads(subprocess.check_output(["node", "-e", script, json.dumps(inputs)], cwd=REPO, text=True))
        self.assertEqual(result, {domain: "true" if domain in ("workforce", "production", "timetable") else "ok" for domain in DOMAINS})

    def test_model_guides_are_serializable_and_cannot_mutate_validation(self):
        contracts = model_contracts()
        self.assertEqual(set(contracts), set(DOMAINS))
        for domain, contract in contracts.items():
            with self.subTest(domain=domain):
                self.assertTrue(contract["title"])
                self.assertTrue(contract["guide"])
                self.assertFalse(contract["schema"]["additionalProperties"])
                self.assertTrue(json.dumps(contract))
        contracts["workforce"]["schema"]["properties"]["coverage"]["maxItems"] = 100
        self.assertEqual(domain_contract("workforce")["schema"]["properties"]["coverage"]["maxItems"], 3)
        self.assertIn("No setup times", contracts["production"]["guide"])
        self.assertIn("SOFT preference", contracts["teams"]["guide"])
        self.assertIn("Never invent coordinates", contracts["facility"]["guide"])

    def test_unknown_domains_missing_fields_and_unrecognized_constraints_fail(self):
        for domain in ("ev_fleet", "../../etc/passwd", "", None, [], {}):
            with self.subTest(domain=domain):
                with self.assertRaises(ValueError):
                    normalize_draft(domain, {})
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                bad = self.seed(domain)
                bad["unsupported_constraint"] = "Must secretly execute alert(1)"
                with self.assertRaisesRegex(ValueError, "unsupported field"):
                    normalize_draft(domain, bad)
                bad = self.seed(domain)
                del bad[next(iter(bad))]
                with self.assertRaisesRegex(ValueError, "missing"):
                    normalize_draft(domain, bad)

    def test_numeric_strings_booleans_nonfinite_and_huge_integers_are_rejected(self):
        for value in ("120", True, None, math.inf, -math.inf, math.nan, 10 ** 1000, {}, []):
            with self.subTest(value=type(value).__name__):
                source = self.seed("facility")
                source["budget"] = value
                with self.assertRaises(ValueError):
                    normalize_draft("facility", source)
        source = self.seed("production")
        source["horizon"] = 12.0
        self.assertIs(type(normalize_draft("production", source)["horizon"]), int)

    def test_plain_names_safe_ids_and_nested_unknown_fields_are_rejected(self):
        for name in ("<img src=x onerror=alert(1)>", "line\nbreak", "bad\x00name", "hidden\u202ename", "\ud800", "😀" * 21):
            with self.subTest(name=repr(name)):
                source = self.seed("workforce")
                source["workers"][0]["name"] = name
                with self.assertRaises(ValueError):
                    normalize_draft("workforce", source)
        for identifier in ("__proto__", "constructor", "W0", "W1000", "W1|W2", "W1<script>"):
            source = self.seed("workforce")
            source["workers"][0]["id"] = identifier
            with self.assertRaises(ValueError):
                normalize_draft("workforce", source)
        source = self.seed("production")
        source["jobs"][0]["operations"][0]["setup"] = 2
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            normalize_draft("production", source)

    def test_facility_forbids_model_road_data_and_preserves_hard_coverage(self):
        source = self.seed("facility")
        source.pop("requireFullCoverage", None)
        self.assertFalse(normalize_draft("facility", source)["requireFullCoverage"])
        source["requireFullCoverage"] = True
        self.assertTrue(normalize_draft("facility", source)["requireFullCoverage"])
        for key in ("durations", "snapshot", "routing", "distances", "base_url"):
            bad = copy.deepcopy(source)
            bad[key] = []
            with self.assertRaisesRegex(ValueError, "unsupported field"):
                normalize_draft("facility", bad)
        for coordinates in ([181, 0], [0, -86], [0, True], [0], [0, 0, 0]):
            bad = copy.deepcopy(source)
            bad["sites"][0]["position"] = coordinates
            with self.assertRaises(ValueError):
                normalize_draft("facility", bad)
        source["sites"][0]["position"] = [-0.0000001, 43.12345678]
        self.assertEqual(normalize_draft("facility", source)["sites"][0]["position"], [0, 43.123457])
        source["maxSites"] = len(source["sites"]) + 1
        with self.assertRaisesRegex(ValueError, "candidate hub count"):
            normalize_draft("facility", source)

    def test_fixed_axes_and_bounded_dimensions_are_enforced(self):
        cases = [
            ("workforce", lambda s: s.update(coverage=[1, 2])),
            ("workforce", lambda s: s.update(maxHours=12)),
            ("workforce", lambda s: s["workers"][0].update(available=[True, False, True, True])),
            ("production", lambda s: s["jobs"][0]["operations"][0].update(machine="Custom machine")),
            ("production", lambda s: s["jobs"][0]["operations"].append({"machine": "M1", "duration": 1})),
            ("timetable", lambda s: s.update(slotCount=4)),
            ("timetable", lambda s: s["courses"][0].update(preferred=3)),
            ("teams", lambda s: s["people"][0].update(skills=[1, 2])),
            ("facility", lambda s: s["demand"].pop()),
        ]
        for domain, mutate in cases:
            with self.subTest(domain=domain):
                source = self.seed(domain)
                mutate(source)
                with self.assertRaises(ValueError):
                    normalize_draft(domain, source)

    def test_timetable_custom_resources_and_single_operation_jobs_are_supported(self):
        source = self.seed("timetable")
        source["courses"][0].update(teacher="Professor Ahmed", cohort="Robotics club")
        normalized = normalize_draft("timetable", source)
        self.assertEqual(normalized["courses"][0]["teacher"], "Professor Ahmed")
        source = self.seed("production")
        source["jobs"][0]["operations"].pop()
        self.assertEqual(len(normalize_draft("production", source)["jobs"][0]["operations"]), 1)

    def test_energy_state_sanity_and_three_hour_time_labels(self):
        source = self.seed("energy")
        for mutation in (lambda s: s.update(initial=s["capacity"] + 1),
                         lambda s: s.update(reserve=s["initial"] + 1),
                         lambda s: s["intervals"][0].update(label="00–01"),
                         lambda s: s["intervals"].reverse()):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(source)
                mutation(bad)
                with self.assertRaises(ValueError):
                    normalize_draft("energy", bad)
        source["intervals"] = source["intervals"][:1]
        self.assertEqual(len(normalize_draft("energy", source)["intervals"]), 1)
        source["intervals"] = [{"label": f"{i * 3:02d}–{(i + 1) * 3:02d}", "load": 0, "solar": 0, "price": 0} for i in range(12)]
        self.assertEqual(normalize_draft("energy", source)["intervals"][-1]["label"], "33–36")

    def test_projects_money_and_prerequisite_references_match_funding_semantics(self):
        source = self.seed("projects")
        source["projects"][0]["cost"] = 10.01
        self.assertEqual(normalize_draft("projects", source)["projects"][0]["cost"], 10.01)
        for requires in ("P999", "P1", [], None):
            bad = copy.deepcopy(source)
            bad["projects"][0]["requires"] = requires
            with self.assertRaises(ValueError):
                normalize_draft("projects", bad)
        source["projects"][0]["requires"] = "P2"
        source["projects"][1]["requires"] = "P1"
        self.assertEqual(normalize_draft("projects", source)["projects"][1]["requires"], "P1")
        source["budget"] = 10.001
        with self.assertRaisesRegex(ValueError, "two decimal"):
            normalize_draft("projects", source)

    def test_teams_missing_pair_defaults_and_lexical_refs_are_authoritative(self):
        source = self.seed("teams")
        source["people"] = [{"id": "U2", "name": "A", "skills": [1, 1, 1]},
                             {"id": "U10", "name": "B", "skills": [1, 1, 1]}]
        source["compatibility"] = {}
        source["minimum_skill"] = 0
        self.assertEqual(normalize_draft("teams", source)["compatibility"], {"U10|U2": 5})
        for compatibility in ({"U2|U10": 8}, {"U2|U2": 8}, {"U1|U2": 8}, {"__proto__": 1}, {"U10|U2": True}):
            with self.subTest(compatibility=compatibility):
                source["compatibility"] = compatibility
                with self.assertRaises(ValueError):
                    normalize_draft("teams", source)
        source["compatibility"] = {}
        source["team_count"] = 3
        with self.assertRaisesRegex(ValueError, "at least one person"):
            normalize_draft("teams", source)

    def test_duplicate_ids_are_rejected_and_infeasible_inputs_stay_editable(self):
        for domain, collection in (("facility", "sites"), ("workforce", "workers"), ("production", "jobs"),
                                   ("timetable", "courses"), ("projects", "projects"), ("teams", "people")):
            with self.subTest(domain=domain):
                source = self.seed(domain)
                source[collection][1]["id"] = source[collection][0]["id"]
                with self.assertRaisesRegex(ValueError, "unique IDs"):
                    normalize_draft(domain, source)
        impossible = {domain: self.seed(domain) for domain in DOMAINS}
        impossible["facility"].update(budget=0, requireFullCoverage=True)
        impossible["workforce"].update(coverage=[3, 3, 3])
        impossible["production"].update(horizon=1)
        impossible["timetable"]["rooms"][0]["open"] = impossible["timetable"]["rooms"][1]["open"] = False
        impossible["energy"].update(gridLimit=0, power=0)
        impossible["projects"].update(budget=0)
        impossible["teams"].update(minimum_skill=15)
        for domain, source in impossible.items():
            with self.subTest(domain=domain):
                self.assertIsInstance(normalize_draft(domain, source), dict)


if __name__ == "__main__":
    unittest.main()
