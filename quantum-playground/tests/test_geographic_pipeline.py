"""Exercise geographic preparation, verified projections, reruns and offline history.

All road geometries below are explicitly synthetic contract fixtures. No model,
quantum backend, map provider, or network request is used by these tests.
"""

import copy
import hashlib
import json
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pipeline_live import load_routing, load_scenario, pipeline_handoff, project_result

# Import the pipeline before patching its dependencies: importing it inside a
# mock context would retain mocked helper aliases for later HTTP tests.
import backend.pipeline

from backend.build_review import solver_fingerprint
from backend.files import FileToolError, delete_file, update_file
from backend.plan import PlanControl
from backend.runner import RunOutcome
from backend.semantics import SemanticReview
from backend.workspace import init_workspace
from fleet_handoff import InvalidHandoff, UnverifiedResult, normalize_handoff
from ev_live import SolverUnavailable
from geographic import geo_seed
from road_replay import capture_replay
from road_network import MapServiceError
from scenario import effective_scenario, example_scenario, scenario_digest, scenario_prompt
from test_geographic import raw_geo_result, resign_routing, routing_fixture
from test_scenario import direct_result
from workspace_runs import SavedResultUnavailable, list_saved_runs, open_saved_run, saved_run


RATE = "driving_cost_per_km"


def synthetic_road_geometry(routing, from_id, to_id):
    """A fixture-only curved line with the required snapped endpoints."""
    nodes = {item["id"]: item["snapped_position"] for item in routing["nodes"]}
    first, last = nodes[from_id], nodes[to_id]
    midpoint = [(first[0] + last[0]) / 2 + 0.00001, (first[1] + last[1]) / 2]
    return [list(first), midpoint, list(last)]


class GeographicPipelineTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict(os.environ, {"QUANTUM_WORKSPACES_ROOT": str(self.root), "MODEL_PROVIDER": "gemini"})
        environment.start()
        self.addCleanup(environment.stop)
        self.source = geo_seed()
        self.prompt = scenario_prompt(self.source)
        self.routing = routing_fixture(self.source)
        self.control = {"key": RATE, "label": "Driving cost", "type": "number", "default": 0.3,
                        "minimum": 0.1, "maximum": 1, "step": 0.1, "rerun_mode": "rebuild", "unit": "USD/km"}
        self.controls = [self.control]
        self.output = raw_geo_result(self.source, self.routing)
        self.output["controls"] = {RATE: 0.3}
        self.workspace = init_workspace(self.prompt, root=self.root, scene_type="ev_fleet")
        self.write_source(self.workspace)
        self.artifact = {"workspace_id": self.workspace.id, "decision": "GO", "controls": self.controls,
                         "problem": {"title": self.source["title"], "summary": "Dispatch these geographic deliveries."},
                         "results": self.output}
        with patch("road_replay.road_geometry", side_effect=synthetic_road_geometry):
            self.artifact["geographic_replay"] = capture_replay(self.output, self.routing, fetch=True)
        self.write_artifact(self.artifact)
        self.identity = {"run_id": "geographic-test-run", "plan_id": self.workspace.id, "attempt_id": "first"}
        self.review = SemanticReview(verdict="matches", summary="Offline fixture matches.", checks_done=[], issues=[], model="test")

    def write_source(self, workspace):
        workspace.resolve("scenario.json").write_text(json.dumps(self.source), encoding="utf-8")
        workspace.resolve("routing.json").write_text(json.dumps(self.routing), encoding="utf-8")
        metadata = workspace.meta
        metadata.update(scenario_digest=scenario_digest(self.source), routing_digest=self.routing["digest"])
        workspace.resolve("meta.json").write_text(json.dumps(metadata), encoding="utf-8")

    def write_artifact(self, artifact):
        self.workspace.resolve("handoff.json").write_text(json.dumps(artifact), encoding="utf-8")

    def previous_view(self):
        handoff = project_result(self.artifact, self.output, self.prompt, self.identity, workspace=self.workspace)
        return normalize_handoff(handoff, "live", self.prompt)

    def outcome(self, output, values):
        return RunOutcome(command="test fixture", controls=values, exit_code=0, timed_out=False, seconds=0,
                          stdout_tail="", stderr_tail="", results=output, issues=[])

    def recalculate(self, worker, rate=0.5):
        with (
            patch("backend.workspace.open_workspace", return_value=self.workspace),
            patch("backend.runner.run_solver", side_effect=worker),
            patch("backend.semantics.evaluate_semantics", return_value=self.review) as judge,
            patch("pipeline_live.planner_session", return_value=nullcontext(object())),
            patch("road_replay.road_geometry", side_effect=synthetic_road_geometry),
        ):
            result = pipeline_handoff(self.prompt, {RATE: rate}, self.previous_view(), "second")
        return result, judge

    def test_projection_binds_saved_source_routing_and_capture_without_network(self):
        with patch("road_replay.road_geometry", side_effect=AssertionError("offline projection fetched routes")) as geometry:
            projected = project_result(self.artifact, self.output, self.prompt, self.identity, workspace=self.workspace)
        geometry.assert_not_called()
        self.assertEqual(projected["schema_version"], 3)
        self.assertEqual(projected["scenario_context"]["base_digest"], scenario_digest(self.source))
        self.assertEqual(projected["scenario_context"]["routing_digest"], self.routing["digest"])
        self.assertEqual(projected["scenario_context"]["effective_scenario"], self.source)
        self.assertEqual(set(projected["scenario_context"]["bindings"]), {RATE})
        self.assertIn("path", projected["result"]["simulation"]["vans"][0]["events"][0])
        self.assertNotIn("path", self.output["simulation"]["vans"][0]["events"][0])
        normalize_handoff(projected, "live", self.prompt)

    def test_enrichment_fetches_only_selected_unique_edges_for_both_schedules(self):
        artifact = {key: value for key, value in self.artifact.items() if key != "geographic_replay"}
        unique_pairs = {(event["from_id"], event["to_id"]) for van in self.output["simulation"]["vans"]
                        for event in van["events"] if event["type"] == "drive"}
        original_bytes = self.workspace.resolve("routing.json").read_bytes()
        with patch("road_replay.road_geometry", side_effect=synthetic_road_geometry) as geometry:
            projected = project_result(artifact, self.output, self.prompt, self.identity, workspace=self.workspace,
                                       allow_geometry_fetch=True)
        self.assertEqual({tuple(call.args[1:]) for call in geometry.call_args_list}, unique_pairs)
        self.assertEqual(geometry.call_count, len(unique_pairs))
        self.assertEqual(projected["geographic_replay"]["routing_digest"], self.routing["digest"])
        self.assertEqual(self.workspace.resolve("routing.json").read_bytes(), original_bytes)
        self.assertEqual(projected["result"]["classical_simulation"], projected["geographic_replay"]["classical_simulation"])

    def test_enrichment_includes_a_different_classical_stop_order(self):
        output = copy.deepcopy(self.output)
        scene = output["reference"]["simulation"]
        van = scene["vans"][0]
        nodes = {item["id"]: item for item in self.routing["nodes"]}
        indexes = {item["id"]: i for i, item in enumerate(self.routing["nodes"])}
        deliveries = {item["id"]: item for item in self.source["deliveries"]}
        minute, battery = 0.0, van["initial_battery_kwh"]
        events = []
        route = ["P1", "D2", "D1", "P1"]
        for origin, destination in zip(route, route[1:]):
            i, j = indexes[origin], indexes[destination]
            distance = self.routing["distances_m"][i][j] / 1000
            duration = self.routing["durations_s"][i][j] / 60
            after = battery - distance * self.source["fleet"]["energy_use_kwh_per_km"]
            events.append({"type": "drive", "from_id": origin, "to_id": destination, "distance_km": distance,
                           "position": nodes[origin]["snapped_position"], "start_min": minute, "end_min": minute + duration,
                           "battery_start_kwh": battery, "battery_end_kwh": after})
            minute, battery = minute + duration, after
            if destination in deliveries:
                end = minute + deliveries[destination]["service_min"]
                events.append({"type": "deliver", "delivery_id": destination,
                               "position": nodes[destination]["snapped_position"], "start_min": minute, "end_min": end,
                               "battery_start_kwh": battery, "battery_end_kwh": battery})
                minute = end
        van["events"] = events
        output["reference"]["objective_value"] = sum(event["distance_km"] * 0.3 for vehicle in scene["vans"]
                                                      for event in vehicle["events"] if event["type"] == "drive")
        pairs = {(event["from_id"], event["to_id"]) for replay in (output["simulation"], scene)
                 for vehicle in replay["vans"] for event in vehicle["events"] if event["type"] == "drive"}
        artifact = {key: value for key, value in self.artifact.items() if key != "geographic_replay"}
        with patch("road_replay.road_geometry", side_effect=synthetic_road_geometry) as geometry:
            projected = project_result(artifact, output, self.prompt, self.identity, workspace=self.workspace,
                                       allow_geometry_fetch=True)
        self.assertEqual({tuple(call.args[1:]) for call in geometry.call_args_list}, pairs)
        self.assertEqual(geometry.call_count, len(pairs))
        normalize_handoff(projected, "live", self.prompt)
        classical_events = projected["result"]["classical_simulation"]["vans"][0]["events"]
        self.assertEqual([event["delivery_id"] for event in classical_events if event["type"] == "deliver"], ["D2", "D1"])

    def test_initial_pipeline_checks_overlays_and_saves_complete_capture(self):
        planning = SimpleNamespace(plan=SimpleNamespace(controls=[PlanControl.model_validate(self.control)]))
        problem = SimpleNamespace(title=self.source["title"], summary="Geographic dispatch")
        seen = []

        def run_pipeline(workspace, *, validate_results, **_kwargs):
            self.assertEqual(load_scenario(workspace), self.source)
            self.assertEqual(load_routing(workspace, self.source), self.routing)
            for filename in ("scenario.json", "routing.json"):
                with self.assertRaises(FileToolError):
                    update_file(workspace, filename, "{}")
            validate_results(copy.deepcopy(self.output), {})
            effective = effective_scenario(self.source, self.controls, {RATE: 0.5})
            changed = raw_geo_result(effective, self.routing)
            changed["controls"] = {RATE: 0.5}
            validate_results(changed, {RATE: 0.5})
            stale = copy.deepcopy(self.output)
            stale["controls"] = {RATE: 0.5}
            with self.assertRaisesRegex(ValueError, "objective|cost"):
                validate_results(stale, {RATE: 0.5})
            seen.append(workspace)
            return {**self.artifact, "workspace_id": workspace.id}

        with (
            patch("pipeline_live.planner_session", return_value=nullcontext(object())),
            patch("pipeline_live.prepare_routing", return_value=self.routing),
            patch("backend.plan.load_plan", return_value=planning),
            patch("backend.problem_statement.load_problem_statement", return_value=problem),
            patch("backend.pipeline.run_pipeline", side_effect=run_pipeline),
            patch("road_replay.road_geometry", side_effect=synthetic_road_geometry),
        ):
            handoff = pipeline_handoff(self.prompt, {}, None, "initial", scenario=self.source)
        self.assertEqual(len(seen), 1)
        saved = json.loads(seen[0].resolve("handoff.json").read_text())
        self.assertEqual(saved["geographic_replay"], handoff["geographic_replay"])
        self.assertEqual(handoff["schema_version"], 3)
        self.assertEqual(seen[0].meta["routing_digest"], self.routing["digest"])

    def test_numeric_rerun_preserves_matrix_and_original_saved_snapshot(self):
        original = {name: self.workspace.resolve(name).read_bytes() for name in ("scenario.json", "routing.json", "handoff.json")}
        tested = []

        def worker(attempt, values):
            self.assertNotEqual(attempt.path, self.workspace.path)
            self.assertEqual(attempt.resolve("routing.json").read_bytes(), original["routing.json"])
            before = solver_fingerprint(attempt)
            effective = effective_scenario(self.source, self.controls, values)
            output = raw_geo_result(effective, self.routing)
            output["controls"] = dict(values)
            self.assertEqual(solver_fingerprint(attempt), before)
            tested.append(attempt.path)
            return self.outcome(output, values)

        result, judge = self.recalculate(worker)
        self.assertEqual(result["scenario_context"]["effective_scenario"]["costs"]["driving_per_km"], 0.5)
        self.assertEqual(result["scenario_context"]["routing_digest"], self.routing["digest"])
        judge.assert_called_once()
        for name, data in original.items():
            self.assertEqual(self.workspace.resolve(name).read_bytes(), data)
        self.assertTrue(tested and all(not path.exists() for path in tested))
        with patch("road_replay.road_geometry", side_effect=AssertionError("history must stay offline")):
            reopened = open_saved_run(self.workspace.id)
        self.assertEqual(reopened["scenario_context"]["effective_scenario"]["costs"]["driving_per_km"], 0.3)

    def test_worker_cannot_modify_approved_road_snapshot(self):
        original = self.workspace.resolve("routing.json").read_bytes()

        def worker(attempt, values):
            attempt.resolve("routing.json").write_text("{}")
            output = raw_geo_result(effective_scenario(self.source, self.controls, values), self.routing)
            output["controls"] = dict(values)
            return self.outcome(output, values)

        with self.assertRaisesRegex(UnverifiedResult, "inputs changed"):
            self.recalculate(worker)
        self.assertEqual(self.workspace.resolve("routing.json").read_bytes(), original)
        with self.assertRaises(FileToolError):
            delete_file(self.workspace, "routing.json")

    def test_offline_saved_reopen_retains_both_captured_schedules(self):
        with patch("road_replay.road_geometry", side_effect=AssertionError("history must not fetch")) as geometry:
            opened = open_saved_run(self.workspace.id)
            detail = saved_run(self.workspace.id)
            runs = list_saved_runs()
        geometry.assert_not_called()
        self.assertEqual(opened["provenance"], "recorded")
        self.assertEqual(opened["simulation"]["map"]["routing_digest"], self.routing["digest"])
        self.assertIn("path", opened["classical_simulation"]["vans"][0]["events"][0])
        self.assertEqual(detail["scenario"], self.source)
        self.assertTrue(detail["can_open"] and detail["can_edit"])
        self.assertIn("routing", [item["key"] for item in detail["artifacts"]])
        self.assertEqual(len(runs), 1)

    def test_corrupt_capture_is_rejected_and_never_refetched_on_open(self):
        for mutation in ("digest", "interior", "endpoint", "classical", "missing"):
            with self.subTest(mutation=mutation):
                artifact = copy.deepcopy(self.artifact)
                capture = artifact["geographic_replay"]
                if mutation == "missing":
                    del artifact["geographic_replay"]
                elif mutation == "digest":
                    capture["digest"] = "0" * 64
                else:
                    scene = capture["classical_simulation" if mutation == "classical" else "simulation"]
                    event = scene["vans"][0]["events"][0]
                    if mutation in ("interior", "classical"):
                        event["path"][1][0] += 0.001
                    else:
                        event["path"][0] = [0, 0]
                        metadata = {key: value for key, value in capture.items() if key != "digest"}
                        capture["digest"] = hashlib.sha256(json.dumps(metadata, sort_keys=True,
                                                                     separators=(",", ":")).encode()).hexdigest()
                self.write_artifact(artifact)
                with patch("road_replay.road_geometry", side_effect=AssertionError("corrupt capture fetched routes")) as geometry:
                    with self.assertRaises(SavedResultUnavailable):
                        open_saved_run(self.workspace.id)
                    detail = saved_run(self.workspace.id)
                geometry.assert_not_called()
                self.assertFalse(detail["can_open"])
                self.assertTrue(detail["can_edit"])

    def test_missing_or_corrupt_routing_preserves_recovery_draft_and_history(self):
        original = self.workspace.resolve("routing.json").read_bytes()
        for mutation in ("missing", "matrix", "metadata"):
            with self.subTest(mutation=mutation):
                self.workspace.resolve("routing.json").write_bytes(original)
                metadata = self.workspace.meta
                metadata["routing_digest"] = self.routing["digest"]
                if mutation == "missing":
                    self.workspace.resolve("routing.json").unlink()
                elif mutation == "matrix":
                    changed = copy.deepcopy(self.routing)
                    changed["distances_m"][0][1] += 100
                    resign_routing(changed)
                    self.workspace.resolve("routing.json").write_text(json.dumps(changed))
                else:
                    metadata["routing_digest"] = "0" * 64
                self.workspace.resolve("meta.json").write_text(json.dumps(metadata))
                with self.assertRaises(InvalidHandoff):
                    load_routing(self.workspace, self.source)
                with patch("road_replay.road_geometry", side_effect=AssertionError("recovery fetched routes")):
                    detail = saved_run(self.workspace.id)
                    runs = list_saved_runs()
                self.assertFalse(detail["can_open"])
                self.assertTrue(detail["can_edit"])
                self.assertEqual(detail["scenario"], self.source)
                self.assertEqual(len(runs), 1)

    def test_provider_preparation_failure_preserves_editable_recovery_history(self):
        with (
            patch("pipeline_live.planner_session", return_value=nullcontext(object())),
            patch("pipeline_live.prepare_routing", side_effect=MapServiceError("Offline routing provider", 503)),
            patch("backend.pipeline.run_pipeline") as pipeline,
        ):
            with self.assertRaisesRegex(SolverUnavailable, "Offline routing provider"):
                pipeline_handoff(self.prompt, {}, None, "failed-preparation", scenario=self.source)
        pipeline.assert_not_called()
        runs = list_saved_runs()
        recovered = next(item for item in runs if item["id"] != self.workspace.id)
        self.assertFalse(recovered["can_open"])
        self.assertTrue(recovered["can_edit"])
        self.assertEqual(saved_run(recovered["id"])["scenario"], self.source)

    def test_geographic_source_identity_requires_original_prompt_and_locations(self):
        source_bytes = self.workspace.resolve("scenario.json").read_bytes()
        self.workspace.resolve("prompt.md").write_text(self.prompt + "\nChanged")
        with self.assertRaises(InvalidHandoff):
            load_scenario(self.workspace)
        self.workspace.resolve("prompt.md").write_text(self.prompt)
        changed = copy.deepcopy(self.source)
        changed["depots"][0]["position"][0] += 0.01
        self.workspace.resolve("scenario.json").write_text(json.dumps(changed))
        with self.assertRaises(InvalidHandoff):
            load_scenario(self.workspace)
        self.workspace.resolve("scenario.json").write_bytes(source_bytes)

    def test_legacy_grid_source_still_opens_without_routing_or_geometry(self):
        grid = example_scenario()
        prompt = scenario_prompt(grid)
        self.assertEqual(hashlib.sha256(prompt.encode()).hexdigest(),
                         "08799cfc0ae6007e4071f8b9e67692794a9b58a7da9a7ea5de995bf32ebd2949")
        workspace = init_workspace(prompt, root=self.root, scene_type="ev_fleet")
        workspace.resolve("scenario.json").write_text(json.dumps(grid))
        metadata = workspace.meta
        metadata["scenario_digest"] = scenario_digest(grid)
        workspace.resolve("meta.json").write_text(json.dumps(metadata))
        output = direct_result(grid)
        artifact = {"workspace_id": workspace.id, "decision": "GO", "controls": [],
                    "problem": {"title": grid["title"], "summary": "Grid fixture"}, "results": output}
        workspace.resolve("handoff.json").write_text(json.dumps(artifact))
        with patch("road_replay.road_geometry", side_effect=AssertionError("grid fetched road geometry")):
            opened = open_saved_run(workspace.id)
        self.assertIn("grid", opened["simulation"])
        self.assertNotIn("routing_digest", opened["scenario_context"])
        self.assertEqual(opened["scenario_context"]["effective_scenario"], grid)


if __name__ == "__main__":
    unittest.main()
