"""Quantum Rings helper, copied into every workspace by the Build step. Read-only.

Solver code imports this instead of setting up Quantum Rings itself:

    from QuantumRingsLib import QuantumCircuit   # after importing qr_runtime
    import qr_runtime

    qc = QuantumCircuit(n, n)
    ...gates...
    qc.measure_all()
    counts = qr_runtime.run_counts(qc, shots=1024)   # {"0101": 37, ...}
    qr_runtime.bit("0101", 0)                        # 1 -> value measured on qubit 0

Import qr_runtime BEFORE QuantumRingsLib and before creating any circuit.
Importing it prepares the GPU libraries on Windows and activates the license;
QuantumRingsLib refuses to create a QuantumCircuit until that has happened.
"""

import os

# On Windows the GPU build needs the CUDA folder on the DLL search path.
if os.name == "nt" and os.getenv("CUDA_PATH"):
    for _sub in (r"bin\x64", "bin"):
        _cuda_bin = os.path.join(os.environ["CUDA_PATH"], _sub)
        if os.path.isdir(_cuda_bin):
            os.add_dll_directory(_cuda_bin)
            break

# Pick up QR_KEY / QR_ACCOUNT from a .env file in this folder or any parent, if present.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

from QuantumRingsLib import QuantumRingsProvider  # noqa: E402

DEFAULT_BACKEND = "scarlet_quantum_rings"

# Keep the provider alive for the life of the process: the backend crashes if it is discarded.
_provider = None
_backend = None


class QuantumRingsError(RuntimeError):
    """Quantum Rings could not be reached or refused the run."""


def get_backend():
    """Connect once and reuse the backend for every run."""
    global _provider, _backend
    if _backend is None:
        token, account = os.getenv("QR_KEY"), os.getenv("QR_ACCOUNT")
        if not token or not account:
            raise QuantumRingsError("Set QR_KEY and QR_ACCOUNT in the environment or the repository's .env file.")
        _provider = QuantumRingsProvider(token=token, name=account)
        if int(_provider.active_account().get("max_qubits", 0)) == 0:
            raise QuantumRingsError("Quantum Rings rejected the license key (0 qubits allowed).")
        _backend = _provider.get_backend(os.getenv("QR_BACKEND", DEFAULT_BACKEND))
    return _backend


# Activate the license now, so circuits can be created as soon as this module is imported.
get_backend()


def run_counts(circuit, shots: int = 1024) -> dict[str, int]:
    """Run a measured circuit and return {bitstring: count}.

    In each bitstring, qubit 0 is the RIGHTMOST character. Use bit() to read
    a qubit's value instead of indexing the string yourself.
    """
    job = get_backend().run(circuit, shots=shots, mode="sync")
    return {str(key).replace(" ", ""): int(value) for key, value in dict(job.result().get_counts()).items()}


def bit(bitstring: str, qubit: int) -> int:
    """Value (0 or 1) measured on the given qubit in a bitstring from run_counts."""
    return int(bitstring[len(bitstring) - 1 - qubit])


CIRCUIT_DIAGRAM = "circuit.png"


def save_circuit_diagram(circuit, qubit_labels=None, section_labels=None, filename: str = CIRCUIT_DIAGRAM) -> dict:
    """Save a picture of a circuit in the workspace folder and describe the circuit.

    qubit_labels: optional short name for each qubit, in qubit order, such as
    ["Ava", "Ben"] or ["van1_morning", "van1_evening"]. Shown beside each wire.

    section_labels: optional name for each circuit.barrier() in the circuit, in
    order, such as ["cost 1", "mixer 1", "measure"]. Each barrier is drawn as a
    divider with its name above it, so put a barrier BEFORE each stage and name
    it after that stage. Barriers do not change the measured results.

    Returns {"file", "qubits", "depth", "gate_counts"}; put it in results.json
    under quantum.circuit_diagram. Call it once, on the final circuit.
    """
    import matplotlib

    matplotlib.use("Agg")  # draw to a file, never open a window
    import matplotlib.pyplot as plt
    from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qasm2

    try:
        parsed = qasm2.loads(circuit.qasm(), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        labels = [str(label) for label in (qubit_labels or [])]
        if len(labels) != parsed.num_qubits or len(set(labels)) != len(labels):
            labels = [f"q{index}" for index in range(parsed.num_qubits)]
        sections = [str(label) for label in (section_labels or [])]

        # Redraw gate by gate: named wires, and each run of barriers as one named divider.
        drawing = QuantumCircuit(*(QuantumRegister(1, label) for label in labels))
        if parsed.num_clbits:
            drawing.add_register(ClassicalRegister(parsed.num_clbits, "result"))
        gates_only = parsed.copy_empty_like()
        dividers = 0
        in_divider = False
        for instruction in parsed.data:
            if instruction.operation.name == "barrier":
                if not in_divider:
                    drawing.barrier(label=sections[dividers] if dividers < len(sections) else None)
                    dividers += 1
                in_divider = True
                continue
            in_divider = False
            gates_only.append(instruction)
            drawing.append(
                instruction.operation,
                [drawing.qubits[parsed.find_bit(qubit).index] for qubit in instruction.qubits],
                [drawing.clbits[parsed.find_bit(clbit).index] for clbit in instruction.clbits],
            )
        figure = drawing.draw("mpl", fold=30, style="iqp")
        figure.savefig(filename, dpi=110, bbox_inches="tight")
        plt.close(figure)
    except Exception as error:  # every drawing problem is reported the same way
        raise QuantumRingsError(f"Could not draw the circuit: {error}") from error

    return {
        "file": filename,
        "qubits": int(parsed.num_qubits),
        "depth": int(gates_only.depth()),
        "gate_counts": {str(name): int(count) for name, count in gates_only.count_ops().items()},
    }
