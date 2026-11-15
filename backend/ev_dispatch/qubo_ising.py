"""QUBO construction, Ising Hamiltonian mapping, and exhaustive equivalence verification."""

from __future__ import annotations

import itertools
from typing import Iterable

import numpy as np

from .dispatch_model import ScheduleOption


def build_qubo(
    options: list[ScheduleOption],
    p_onehot: float = 150.0,
    p_conflict: float = 150.0,
) -> tuple[np.ndarray, float]:
    """Assemble the symmetric QUBO matrix Q and constant offset for E(x) = x^T Q x + const."""
    n = len(options)
    Q = np.zeros((n, n), dtype=float)
    const = 0.0

    # Linear dispatch cost terms
    for opt in options:
        Q[opt.qubit_index, opt.qubit_index] += float(opt.total_cost)

    # Per-van one-hot constraints: p_onehot * (sum_{k in van} x_k - 1)^2
    van_groups: dict[int, list[int]] = {}
    for opt in options:
        van_groups.setdefault(opt.van_id, []).append(opt.qubit_index)

    for indices in van_groups.values():
        const += float(p_onehot)
        for idx in indices:
            Q[idx, idx] -= float(p_onehot)
        for a, b in itertools.combinations(indices, 2):
            Q[a, b] += float(p_onehot)
            Q[b, a] += float(p_onehot)

    # Single-plug charger slot exclusivity: p_conflict * x_{1,s} * x_{2,s}
    for a_opt, b_opt in itertools.combinations(options, 2):
        if (
            a_opt.van_id != b_opt.van_id
            and a_opt.charger_slot is not None
            and a_opt.charger_slot == b_opt.charger_slot
        ):
            i, j = a_opt.qubit_index, b_opt.qubit_index
            Q[i, j] += 0.5 * float(p_conflict)
            Q[j, i] += 0.5 * float(p_conflict)

    return Q, const


def qubo_to_ising(Q: np.ndarray, const: float) -> tuple[float, np.ndarray, dict[tuple[int, int], float]]:
    """Map x in {0,1}^n to z in {+1,-1}^n via x = (1 - z) / 2.

    Returns (ising_const, h, J) such that:
        x^T Q x + const == ising_const + sum_a h[a] * z[a] + sum_{a<b} J[(a,b)] * z[a] * z[b]
    """
    n = Q.shape[0]
    ising_const = float(const)
    h = np.zeros(n, dtype=float)
    J: dict[tuple[int, int], float] = {}

    for a in range(n):
        q_aa = float(Q[a, a])
        ising_const += 0.5 * q_aa
        h[a] -= 0.5 * q_aa

    for a in range(n):
        for b in range(a + 1, n):
            w_ab = float(Q[a, b] + Q[b, a])
            if abs(w_ab) > 0.0:
                ising_const += 0.25 * w_ab
                h[a] -= 0.25 * w_ab
                h[b] -= 0.25 * w_ab
                j_val = 0.25 * w_ab
                if abs(j_val) > 1e-12:
                    J[(a, b)] = j_val

    return ising_const, h, J


def evaluate_ising_from_bits(
    bits: Iterable[int],
    ising_const: float,
    h: np.ndarray,
    J: dict[tuple[int, int], float],
) -> float:
    """Compute <x|H_C|x> from binary bits x_q in {0, 1}."""
    z = [1.0 - 2.0 * int(b) for b in bits]
    energy = float(ising_const)
    for a, h_a in enumerate(h):
        energy += float(h_a) * z[a]
    for (a, b), j_ab in J.items():
        energy += float(j_ab) * z[a] * z[b]
    return energy


def verify_ising_equivalence(
    Q: np.ndarray,
    const: float,
    ising_const: float,
    h: np.ndarray,
    J: dict[tuple[int, int], float],
    tol: float = 1e-9,
) -> float:
    """Exhaustively assert QUBO == Ising across all 2^n bitstrings and return max absolute error."""
    n = Q.shape[0]
    max_error = 0.0
    for bits in itertools.product((0, 1), repeat=n):
        x = np.array(bits, dtype=float)
        qubo_val = float(x @ Q @ x + const)
        ising_val = evaluate_ising_from_bits(bits, ising_const, h, J)
        err = abs(qubo_val - ising_val)
        if err > max_error:
            max_error = err
    if max_error > tol:
        raise AssertionError(f"QUBO-to-Ising equivalence failed: max_error={max_error:.3e} > tol={tol:.3e}")
    return max_error
