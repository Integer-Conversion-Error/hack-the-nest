"""Circuit backends for the EV dispatch solver."""

from __future__ import annotations

import os
from math import cos, sin, sqrt

import numpy as np


def bit(bitstring: str, qubit: int) -> int:
    """Read a little-endian qubit from a most-significant-bit-first string."""
    return int(bitstring[-1 - qubit])


def quantum_rings_configured() -> bool:
    return bool(os.getenv("QR_KEY") and os.getenv("QR_ACCOUNT"))


class StatevectorCircuit:
    """Simulate just the gates used by the EV dispatch QAOA circuit."""

    def __init__(self, width: int):
        self.width = width
        self.indices = np.arange(1 << width)
        self.state = np.zeros(1 << width, dtype=np.complex128)
        self.state[0] = 1
        self.measured = False

    def _apply_single_qubit(
        self, qubit: int, a: complex, b: complex, c: complex, d: complex
    ) -> None:
        mask = 1 << qubit
        zero = self.indices[(self.indices & mask) == 0]
        one = zero | mask
        state_zero, state_one = self.state[zero], self.state[one]
        self.state[zero] = a * state_zero + b * state_one
        self.state[one] = c * state_zero + d * state_one

    def h(self, qubit: int) -> None:
        amplitude = 1 / sqrt(2)
        self._apply_single_qubit(qubit, amplitude, amplitude, amplitude, -amplitude)

    def x(self, qubit: int) -> None:
        self._apply_single_qubit(qubit, 0, 1, 1, 0)

    def rz(self, angle: float, qubit: int) -> None:
        phase = np.exp(0.5j * angle)
        self._apply_single_qubit(qubit, phase.conjugate(), 0, 0, phase)

    def rx(self, angle: float, qubit: int) -> None:
        diagonal = cos(angle / 2)
        off_diagonal = -1j * sin(angle / 2)
        self._apply_single_qubit(qubit, diagonal, off_diagonal, off_diagonal, diagonal)

    def cx(self, control: int, target: int) -> None:
        control_mask, target_mask = 1 << control, 1 << target
        zero = self.indices[(self.indices & (control_mask | target_mask)) == control_mask]
        one = zero | target_mask
        self.state[zero], self.state[one] = self.state[one], self.state[zero]

    def measure_all(self) -> None:
        self.measured = True

    def sample(self, shots: int, rng: np.random.Generator) -> dict[str, int]:
        if not self.measured:
            raise ValueError("The circuit must end with measurement.")
        probabilities = np.abs(self.state) ** 2
        counts = rng.multinomial(shots, probabilities / probabilities.sum())
        return {
            format(index, f"0{self.width}b"): int(count)
            for index, count in enumerate(counts)
            if count
        }


class LocalBackend:
    engine = "numpy-statevector"

    def __init__(self) -> None:
        self.rng = np.random.default_rng()

    def circuit(self, width: int) -> StatevectorCircuit:
        return StatevectorCircuit(width)

    def run_counts(self, circuit: StatevectorCircuit, shots: int) -> dict[str, int]:
        return circuit.sample(shots, self.rng)


class QuantumRingsBackend:
    engine = "quantum-rings"

    def __init__(self) -> None:
        # The existing runtime configures the SDK before any circuits are created.
        from backend.templates import qr_runtime
        from QuantumRingsLib import QuantumCircuit

        self._quantum_circuit = QuantumCircuit
        self._run_counts = qr_runtime.run_counts

    def circuit(self, width: int):
        return self._quantum_circuit(width, width)

    def run_counts(self, circuit, shots: int) -> dict[str, int]:
        return self._run_counts(circuit, shots)
