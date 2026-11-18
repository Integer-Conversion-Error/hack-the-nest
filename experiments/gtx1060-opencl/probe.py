"""Verify GPU circuit execution; this experiment does not change the app backend."""

from __future__ import annotations

import argparse
import cmath
import ctypes as c
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import distribution, version
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "quantum-playground")]
PYQRACK_VERSION = "2.30.6"
STATE_TOLERANCE = 1e-5
SHOTS = 2048


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def gpu_device():
    """Require one NVIDIA GPU and no other devices, so Qrack device 0 is explicit."""
    lib = c.CDLL("libOpenCL.so.1")
    ptr, uint, size = c.c_void_p, c.c_uint, c.c_size_t
    functions = {
        "clGetPlatformIDs": [uint, c.POINTER(ptr), c.POINTER(uint)],
        "clGetPlatformInfo": [ptr, uint, size, ptr, c.POINTER(size)],
        "clGetDeviceIDs": [ptr, c.c_ulong, uint, c.POINTER(ptr), c.POINTER(uint)],
        "clGetDeviceInfo": [ptr, uint, size, ptr, c.POINTER(size)],
    }
    for name, args in functions.items():
        function = getattr(lib, name)
        function.argtypes, function.restype = args, c.c_int

    def check(status, action):
        require(status == 0, f"{action} failed with OpenCL status {status}.")

    def text(function, handle, key):
        length = size()
        check(function(handle, key, 0, None, c.byref(length)), "Query OpenCL string size")
        require(0 < length.value <= 65536, "Invalid OpenCL string length.")
        value = c.create_string_buffer(length.value)
        check(function(handle, key, length.value, value, None), "Read OpenCL string")
        return value.value.decode("utf-8", errors="replace")

    def number(handle, key, kind):
        value = kind()
        check(lib.clGetDeviceInfo(handle, key, c.sizeof(value), c.byref(value), None), "Read OpenCL property")
        return value.value

    count = uint()
    check(lib.clGetPlatformIDs(0, None, c.byref(count)), "Find OpenCL platforms")
    require(count.value == 1, "This probe requires exactly one OpenCL platform; refusing ambiguous device selection.")
    platforms = (ptr * 1)()
    check(lib.clGetPlatformIDs(1, platforms, None), "Read OpenCL platform")
    handle = platforms[0]
    check(lib.clGetDeviceIDs(handle, 0xFFFFFFFF, 0, None, c.byref(count)), "Find OpenCL devices")
    require(count.value == 1, "This probe requires exactly one OpenCL device; CPU devices cannot be a fallback.")
    devices = (ptr * 1)()
    check(lib.clGetDeviceIDs(handle, 0xFFFFFFFF, 1, devices, None), "Read OpenCL device")
    device = devices[0]
    kind = number(device, 0x1000, c.c_ulong)
    vendor = text(lib.clGetDeviceInfo, device, 0x102C)
    require(kind & 4 and not kind & 2, "The OpenCL device must be a GPU, not a CPU.")
    require("nvidia" in vendor.lower(), f"This NVIDIA probe does not support vendor {vendor!r}.")
    return {
        "platform": text(lib.clGetPlatformInfo, handle, 0x0902),
        "device": text(lib.clGetDeviceInfo, device, 0x102B),
        "vendor": vendor,
        "device_type": "GPU",
        "global_memory_bytes": number(device, 0x101F, c.c_ulonglong),
        "opencl_version": text(lib.clGetDeviceInfo, device, 0x102F),
        "driver_version": text(lib.clGetDeviceInfo, device, 0x102D),
        "qrack_device_index": 0,
    }


@contextmanager
def native_logs_to_stderr():
    """Qrack writes device/JIT messages directly to stdout, below Python's stream."""
    sys.stdout.flush()
    saved = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(saved, sys.stdout.fileno())
        os.close(saved)


def run_probe():
    require(version("pyqrack") == PYQRACK_VERSION,
            f"Use pyqrack=={PYQRACK_VERSION}; the GPU-only constructor uses its pinned native API.")
    device = gpu_device()
    native_library = Path(distribution("pyqrack").locate_file(
        "pyqrack/qrack_system/qrack_lib/libqrack_pinvoke.so"
    )).resolve()
    require(native_library.is_file(), "The pinned PyQrack wheel's native library is missing.")
    # This must happen before importing pyqrack, which initializes its native library.
    os.environ.update(
        QRACK_OCL_DEFAULT_DEVICE="0", QRACK_QPAGER_DEVICES="0", QRACK_QUNITMULTI_DEVICES="0",
        PYQRACK_SHARED_LIB_PATH=str(native_library), QRACK_FPPOW="5",
    )
    import numpy as np
    from pyqrack import QrackSimulator
    from pyqrack.qrack_system import Qrack
    require(Path(Qrack.qrack_lib._name).resolve() == native_library and Qrack.fppow == 5,
            "Qrack did not load the pinned wheel's single-precision native library.")

    from backend.ev_dispatch.classical import solve_classical
    from backend.ev_dispatch.dispatch_model import generate_feasible_options
    from backend.ev_dispatch.qaoa import filter_and_select_best
    from backend.ev_dispatch.runtime import StatevectorCircuit
    from circuit_images import _curated_trace
    from ev_recorded import RECORDING_DIR

    @contextmanager
    def gpu_sim(width):
        require(type(width) is int and 1 <= width <= 5, "Probe allocation is limited to five qubits.")
        sim = QrackSimulator.__new__(QrackSimulator)
        sim.sid, sim.is_pure_stabilizer = None, False
        try:
            # init_count_type: width, tensor network, multi-device decomposition,
            # decomposition, stabilizer, decision tree, paging, noise, CPU/GPU
            # hybrid, GPU, host memory, sparse. Only GPU is enabled. The public
            # constructor hardcodes CPU/GPU hybrid, so it cannot prove GPU use.
            sim.sid = Qrack.qrack_lib.init_count_type(
                width, False, False, False, False, False, False, False, False, True, False, False
            )
            sim._throw_if_error()
            sim.seed(1234)
            yield sim
        finally:
            if sim.sid is not None:
                Qrack.qrack_lib.destroy(sim.sid)
                sim.sid = None

    def sample(sim, width, shots):
        counts = Counter(sim.measure_shots(list(range(width)), shots))
        require(sum(counts.values()) == shots, "Shot count disagrees with the request.")
        require(all(0 <= bits < 1 << width for bits in counts), "Measured bitstring exceeds the circuit width.")
        return {format(bits, f"0{width}b"): count for bits, count in sorted(counts.items())}

    with gpu_sim(2) as gpu:
        gpu.h(0)
        gpu.mcx([0], 1)
        bell = sample(gpu, 2, 1024)
    require(set(bell) == {"00", "11"} and 350 < bell["00"] < 674,
            f"Bell-state check failed: {bell}.")
    with gpu_sim(2) as gpu:
        gpu.x(0)
        endianness = sample(gpu, 2, 64)
    require(endianness == {"01": 64}, f"Qubit-0 bit order check failed: {endianness}.")
    print("PASS: GPU-only Bell-state and qubit-order checks.", file=sys.stderr)

    circuits = []
    for charge in (35, 20):
        source = RECORDING_DIR / ("handoff.json" if charge == 35 else "test.json")
        recording = json.loads(source.read_text(encoding="utf-8"))
        output = recording["results"] if charge == 35 else next(
            run["results"] for run in recording["runs"]
            if run["controls"] == {"van_2_initial_charge_pct": charge}
        )
        quantum = output["quantum"]
        width = quantum["qubits"]
        require(width == (5 if charge == 35 else 4), "The recorded demo circuit width changed.")
        mapping = tuple((f"q{i}", quantum["qubit_mapping"][f"q{i}"]) for i in range(width))
        trace = _curated_trace(charge, tuple(quantum["optimal_gammas"]), tuple(quantum["optimal_betas"]), mapping)
        require(len(trace.operations) <= 512, "The recorded circuit exceeds the probe's gate limit.")
        require(trace.operations[-width:] == [("M", (q,), None) for q in range(width)],
                "The probe requires final measurements on every qubit.")
        reference = StatevectorCircuit(width)
        with gpu_sim(width) as gpu:
            for gate, qubits, angle in trace.operations:
                if gate == "H":
                    gpu.h(qubits[0])
                    reference.h(qubits[0])
                elif gate == "Rz":
                    gpu.mtrx([cmath.exp(-0.5j * angle), 0j, 0j, cmath.exp(0.5j * angle)], qubits[0])
                    reference.rz(angle, qubits[0])
                elif gate == "Rx":
                    diagonal, off_diagonal = math.cos(angle / 2), -1j * math.sin(angle / 2)
                    gpu.mtrx([diagonal, off_diagonal, off_diagonal, diagonal], qubits[0])
                    reference.rx(angle, qubits[0])
                elif gate == "CX":
                    gpu.mcx([qubits[0]], qubits[1])
                    reference.cx(*qubits)
                elif gate != "M":
                    raise RuntimeError(f"Unsupported recorded gate {gate!r}.")
            state = np.asarray(gpu.out_ket())
            require(np.isfinite(state).all(), "The GPU returned non-finite amplitudes.")
            # Qrack may choose an unobservable global phase; align it first.
            phase = np.angle(np.vdot(reference.state, state))
            error = float(np.max(np.abs(state * np.exp(-1j * phase) - reference.state)))
            require(error < STATE_TOLERANCE, f"GPU state disagrees with NumPy: maximum error {error}.")
            counts = sample(gpu, width, SHOTS)
        options, _ = generate_feasible_options(charge)
        bits, _, feasible_share, _, _, selected, cost = filter_and_select_best(counts, options)
        classical = solve_classical(charge)
        expected = classical["objective"]["value"]
        require(classical["optimality_proven"] and classical["solution"]["verification"]["feasible"],
                "The independent classical solution did not prove a feasible optimum.")
        require(math.isclose(cost, expected, rel_tol=0, abs_tol=1e-5),
                f"Sampled GPU solution costs {cost}; the classical optimum is {expected}.")
        circuits.append({
            "charge_percent": charge, "qubits": width, "gates_including_measurements": len(trace.operations),
            "shots": SHOTS, "counts": counts, "parameter_source": str(source.relative_to(REPO)),
            "max_state_error": error, "state_tolerance": STATE_TOLERANCE, "best_bitstring": bits,
            "feasible_shot_share": feasible_share, "selected_options": [option.option_id for option in selected],
            "cost_usd": cost, "classical_cost_usd": expected, "matches_classical_optimum": True,
        })
        print(f"PASS: {width}-qubit EV circuit at {charge}% charge, {SHOTS} GPU shots, cost ${cost:.6f}.",
              file=sys.stderr)

    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_revision": revision, "system": platform.platform(), "python": platform.python_version(),
        "packages": {name: version(name) for name in ("pyqrack", "numpy", "scipy", "python-dotenv")},
        "native_library": {
            "source": "pyqrack wheel", "precision": "single",
            "sha256": hashlib.sha256(native_library.read_bytes()).hexdigest(),
        },
        "engine": "qrack-opencl", "opencl": device, "cpu_fallback": False,
        "bell": {"shots": 1024, "counts": bell}, "endianness": {"shots": 64, "counts": endianness},
        "ev_circuits": circuits,
        "scope": "GPU execution of circuits reconstructed from recorded parameters; no optimizer rerun, "
                 "app backend integration, or performance claim. CPU calculations independently validate "
                 "the statevector and exact classical optimum.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Also save successful JSON evidence to this file.")
    args = parser.parse_args()
    try:
        with native_logs_to_stderr():
            result = run_probe()
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        sys.stdout.write(payload)
        return 0
    except Exception as error:
        print(f"GPU probe failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
