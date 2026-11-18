import base64
import copy
import hashlib
import json
import struct
import unittest
import zlib
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from circuit_images import (
    PNG_PREFIX, _curated_trace, _reconstructed_image, captured_circuit, reconstructed_circuit, valid_png_image,
)
from ev_recorded import RECORDING_DIR, recorded_handoff
from ev_reference import PROMPT, reference_handoff
from fleet_handoff import InvalidHandoff, normalize_handoff
from pipeline_live import project_result
from backend.workspace import init_workspace


def png(color=(255, 255, 255), width=2, height=2):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\0" + bytes(color) * 2) * 2
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def image(data):
    return PNG_PREFIX + base64.b64encode(data).decode("ascii")


def recorded_output(charge=35):
    if charge == 35:
        return json.loads((RECORDING_DIR / "handoff.json").read_text(encoding="utf-8"))["results"]
    report = json.loads((RECORDING_DIR / "test.json").read_text(encoding="utf-8"))
    return next(run["results"] for run in report["runs"] if run["controls"] == {"van_2_initial_charge_pct": charge})


class CircuitImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = init_workspace("A verified fleet circuit.", root=Path(self.directory.name))
        self.output = {"controls": {"charge": 35}, "quantum": {
            "qubits": 1, "circuit_diagram": {"file": "circuit.png", "qubits": 1},
        }}
        self.bytes = png()
        self.workspace.resolve("circuit.png").write_bytes(self.bytes)

    def report(self):
        return {
            "circuit_digest": hashlib.sha256(self.bytes).hexdigest(),
            "runs": [{"controls": {}, "exit_code": 0, "timed_out": False, "issues": [], "results": self.output}],
        }

    def test_inline_png_validation_rejects_remote_svg_corruption_and_oversized_dimensions(self):
        self.assertTrue(valid_png_image(image(self.bytes)))
        corrupted = bytearray(self.bytes)
        corrupted[-1] ^= 1
        for value in (
            "https://example.org/circuit.png", "data:image/svg+xml,<svg/>", PNG_PREFIX + "invalid!",
            image(b"\x89PNG\r\n\x1a\n"), image(bytes(corrupted)), image(png(width=100_000, height=100_000)),
        ):
            with self.subTest(value=value[:50]):
                self.assertFalse(valid_png_image(value))

    def test_handoff_accepts_image_only_or_existing_ascii_circuits(self):
        handoff = reference_handoff({})
        handoff["result"]["circuits"] = [
            {"title": "Circuit", "image": image(self.bytes), "description": "Captured from the worker."},
            {"drawing": "q0: --H--M--"},
        ]
        view = normalize_handoff(handoff, "reference", PROMPT)
        self.assertEqual(view["circuits"], handoff["result"]["circuits"])
        handoff["result"]["circuits"][0]["image"] = "https://example.org/circuit.png"
        with self.assertRaises(InvalidHandoff):
            normalize_handoff(handoff, "reference", PROMPT)

    def test_fresh_attempt_capture_survives_cleanup_and_never_uses_another_attempt(self):
        captured = captured_circuit(self.workspace, self.output)
        self.assertEqual(captured["image"], image(self.bytes))
        self.workspace.resolve("circuit.png").write_bytes(png((0, 100, 50)))
        alternate = captured_circuit(self.workspace, {**self.output, "controls": {"charge": 20}})
        self.assertNotEqual(captured["image"], alternate["image"])
        self.workspace.resolve("circuit.png").unlink()
        self.assertIsNone(captured_circuit(self.workspace, self.output))
        self.assertTrue(valid_png_image(alternate["image"]), "the PNG remains immutable after the attempt directory disappears")

    def test_saved_capture_matches_default_results_and_png_digest(self):
        path = self.workspace.resolve("test.json")
        path.write_text(json.dumps(self.report()), encoding="utf-8")
        self.assertIsNotNone(captured_circuit(self.workspace, self.output, saved=True))
        stale = {**self.output, "controls": {"charge": 20}}
        self.assertIsNone(captured_circuit(self.workspace, stale, saved=True))
        self.workspace.resolve("circuit.png").write_bytes(png((0, 100, 50)))
        self.assertIsNone(captured_circuit(self.workspace, self.output, saved=True))
        path.unlink()
        self.assertIsNone(captured_circuit(self.workspace, self.output, saved=True))

    def test_captured_image_never_follows_paths_or_symlinks(self):
        altered = {**self.output, "quantum": {"circuit_diagram": {"file": "../circuit.png"}}}
        self.assertIsNone(captured_circuit(self.workspace, altered))
        self.workspace.resolve("circuit.png").unlink()
        outside = Path(self.directory.name) / "outside.png"
        outside.write_bytes(self.bytes)
        (self.workspace.path / "circuit.png").symlink_to(outside)
        self.assertIsNone(captured_circuit(self.workspace, self.output))

    def test_native_projection_keeps_existing_drawing_and_captures_current_png(self):
        handoff = reference_handoff({})
        simulation = handoff["result"]["simulation"]
        output = {
            **self.output, "status": "ok", "objective": {"value": 10, "unit": "USD"},
            "solution": {"verification": {"feasible": True, "violations": []}, "cost_breakdown": {"driving": 10}},
            "simulation": simulation, "circuits": [{"drawing": "q0: --H--M--"}],
        }
        artifact = {"problem": {"title": "Fleet", "summary": "Dispatch the fleet."}, "controls": []}
        identity = {"run_id": "run", "plan_id": "plan", "attempt_id": "attempt"}
        projected = project_result(artifact, output, "Fleet", identity, workspace=self.workspace)
        self.assertEqual(projected["result"]["circuits"][0]["drawing"], "q0: --H--M--")
        self.assertEqual(projected["result"]["circuits"][1]["image"], image(self.bytes))
        self.assertEqual(project_result(artifact, output, "Fleet", identity)["result"]["circuits"], output["circuits"])

    def test_saved_workspace_reopen_exposes_only_its_matching_default_image(self):
        from workspace_runs import open_saved_run

        original = reference_handoff({})
        output = {
            "status": "ok", "controls": original["result"]["effective_values"],
            "objective": {"unit": "USD", "value": original["result"]["evaluation"]["objective_value"]},
            "solution": {"verification": {"feasible": True, "violations": []},
                         "cost_breakdown": original["result"]["evaluation"]["costs"]},
            "simulation": original["result"]["simulation"],
            "quantum": {"qubits": 5, "decision_qubits": 5, "counts": {"00000": 16},
                        "circuit_diagram": {"file": "circuit.png", "qubits": 5}},
        }
        artifact = {
            "workspace_id": self.workspace.id, "decision": "GO", "controls": original["controls"],
            "problem": {"title": "Fleet", "summary": "A verified fleet schedule."}, "results": output,
        }
        self.workspace.resolve("handoff.json").write_text(json.dumps(artifact), encoding="utf-8")
        report = self.report()
        report["runs"][0]["results"] = output
        self.workspace.resolve("test.json").write_text(json.dumps(report), encoding="utf-8")
        with patch.dict("os.environ", {"QUANTUM_WORKSPACES_ROOT": self.directory.name}):
            first = open_saved_run(self.workspace.id)
            self.assertEqual(first["circuits"][0]["image"], image(self.bytes))
            self.workspace.resolve("circuit.png").write_bytes(png((0, 100, 50)))
            self.assertEqual(open_saved_run(self.workspace.id)["circuits"], [])
        self.assertEqual(first["circuits"][0]["image"], image(self.bytes))


@unittest.skipUnless(find_spec("numpy") and find_spec("scipy"), "The local solver dependencies are required.")
class ReconstructedCircuitTests(unittest.TestCase):
    def test_actual_solver_builder_reconstructs_control_specific_gates_and_measurements(self):
        circuits = []
        for charge, width in ((35, 5), (20, 4)):
            output = recorded_output(charge)
            quantum = output["quantum"]
            mapping = tuple((f"q{index}", quantum["qubit_mapping"][f"q{index}"]) for index in range(width))
            trace = _curated_trace(charge, tuple(quantum["optimal_gammas"]), tuple(quantum["optimal_betas"]), mapping)
            self.assertEqual(trace.width, width)
            self.assertEqual(trace.operations[:width], [("H", (index,), None) for index in range(width)])
            self.assertEqual(trace.operations[-width:], [("M", (index,), None) for index in range(width)])
            self.assertTrue(any(gate == "CX" for gate, _, _ in trace.operations))
            self.assertTrue(any(gate == "Rz" for gate, _, _ in trace.operations))
            circuits.append(trace.operations)
        self.assertNotEqual(*circuits)

    def test_unknown_or_missing_parameters_never_produce_a_plausible_generic_circuit(self):
        for mutate in (
            lambda quantum: quantum.pop("optimal_gammas"),
            lambda quantum: quantum.update(optimal_betas=[float("nan"), 0]),
            lambda quantum: quantum.update(p_layers=1000),
            lambda quantum: quantum["qubit_mapping"].update(q0="unrelated variable"),
        ):
            output = copy.deepcopy(recorded_output())
            mutate(output["quantum"])
            self.assertIsNone(reconstructed_circuit(output, "recorded"))

    @unittest.skipUnless(find_spec("pygame"), "Pygame is required to render circuit PNGs.")
    def test_recorded_pngs_are_valid_parameter_matched_and_cached(self):
        import circuit_images

        _reconstructed_image.cache_clear()
        with patch("circuit_images._render_trace", wraps=circuit_images._render_trace) as renderer:
            first = reconstructed_circuit(recorded_output(), "recorded")
            again = reconstructed_circuit(recorded_output(), "recorded")
            self.assertEqual(first, again)
            self.assertEqual(renderer.call_count, 1)
        self.assertTrue(valid_png_image(first["image"]))
        self.assertIn("Reconstructed from recorded parameters", first["description"])
        alternate = reconstructed_circuit(recorded_output(20), "recorded")
        self.assertTrue(valid_png_image(alternate["image"]))
        self.assertNotEqual(first["image"], alternate["image"])
        projected = recorded_handoff({})["result"]["circuits"]
        self.assertEqual(projected, [first])


if __name__ == "__main__":
    unittest.main()
