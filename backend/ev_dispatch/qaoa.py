"""Optimize and measure the two-layer EV dispatch QAOA circuit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .dispatch_model import ScheduleOption
from .qubo_ising import evaluate_ising_from_bits
from .runtime import LocalBackend, QuantumRingsBackend, bit

Backend = LocalBackend | QuantumRingsBackend


@dataclass
class QAOAResult:
    method: str
    n_qubits: int
    p_layers: int
    shots: int
    circuit_runs: int
    endianness_check_passed: bool
    endianness_details: dict[str, Any]
    optimal_gammas: list[float]
    optimal_betas: list[float]
    optimal_ising_energy: float
    counts: dict[str, int]
    best_bitstring: str
    best_bitstring_share: float
    feasible_shot_share: float
    raw_most_frequent_bitstring: str
    raw_most_frequent_share: float
    selected_options: list[ScheduleOption]
    selected_cost: float


def endianness_check(backend: Backend) -> tuple[bool, dict[str, Any]]:
    """Run a 2-qubit X(0) smoke test to verify the qubit-to-bitstring mapping."""
    qc = backend.circuit(2)
    qc.x(0)
    qc.measure_all()
    counts = backend.run_counts(qc, shots=64)
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("Quantum Rings endianness smoke test returned empty or zero counts.")

    top_bs = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0].zfill(2)
    q0_val = bit(top_bs, 0)
    q1_val = bit(top_bs, 1)
    passed = (q0_val == 1) and (q1_val == 0)
    if not passed:
        raise RuntimeError(
            f"Endianness smoke test failed: expected q0=1, q1=0 for bitstring '{top_bs}', "
            f"got q0={q0_val}, q1={q1_val}."
        )
    return passed, {
        "counts": counts,
        "dominant_bitstring": top_bs,
        "q0_bit": q0_val,
        "q1_bit": q1_val,
    }


def build_qaoa_circuit(
    backend: Backend,
    n_qubits: int,
    h: np.ndarray,
    J: dict[tuple[int, int], float],
    gammas: list[float],
    betas: list[float],
):
    """Build a p-layer QAOA circuit with explicit cx-rz-cx ZZ decompositions and rx mixers."""
    if len(gammas) != len(betas):
        raise ValueError("gammas and betas must have the same length.")

    qc = backend.circuit(n_qubits)
    for q in range(n_qubits):
        qc.h(q)

    for gamma, beta in zip(gammas, betas):
        g = float(gamma)
        b = float(beta)

        # Cost unitary: exp(-i * gamma * H_C)
        for a in range(n_qubits):
            h_a = float(h[a])
            if abs(h_a) > 1e-12:
                qc.rz(float(2.0 * g * h_a), a)

        for (q1, q2), j_val in J.items():
            if abs(j_val) > 1e-12:
                qc.cx(q1, q2)
                qc.rz(float(2.0 * g * j_val), q2)
                qc.cx(q1, q2)

        # Transverse-field X mixer: exp(-i * beta * sum X_q)
        for q in range(n_qubits):
            qc.rx(float(2.0 * b), q)

    qc.measure_all()
    return qc


def expected_ising_energy(
    counts: dict[str, int],
    n_qubits: int,
    ising_const: float,
    h: np.ndarray,
    J: dict[tuple[int, int], float],
) -> float:
    """Compute <H_C> from measured counts (supports raw shot counts or percentage counts)."""
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("Quantum Rings returned empty counts during QAOA evaluation.")

    energy = 0.0
    for raw_bs, count in counts.items():
        bs = raw_bs.zfill(n_qubits)
        bits = [bit(bs, q) for q in range(n_qubits)]
        e_bs = evaluate_ising_from_bits(bits, ising_const, h, J)
        energy += (count / total) * e_bs
    return float(energy)


def is_feasible_bitstring(bits: list[int], options: list[ScheduleOption]) -> tuple[bool, float]:
    """Check one-hot per van and charger slot exclusivity; return (feasible, unpenalized_cost)."""
    van_counts: dict[int, int] = {}
    slot_counts: dict[int, int] = {}
    selected_cost = 0.0

    for opt, bit_val in zip(options, bits):
        if bit_val == 1:
            van_counts[opt.van_id] = van_counts.get(opt.van_id, 0) + 1
            if opt.charger_slot is not None:
                slot_counts[opt.charger_slot] = slot_counts.get(opt.charger_slot, 0) + 1
            selected_cost += opt.total_cost

    van_ids = sorted({opt.van_id for opt in options})
    one_hot_ok = all(van_counts.get(vid, 0) == 1 for vid in van_ids)
    slot_ok = all(cnt <= 1 for cnt in slot_counts.values())
    return (one_hot_ok and slot_ok), float(selected_cost)


def filter_and_select_best(
    counts: dict[str, int],
    options: list[ScheduleOption],
) -> tuple[str, float, float, str, float, list[ScheduleOption], float]:
    """Filter measured bitstrings to feasible ones and pick lowest unpenalized cost."""
    n_qubits = len(options)
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("Measured counts dictionary is empty or sums to zero.")

    raw_top_bs, raw_top_cnt = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    raw_top_bs = raw_top_bs.zfill(n_qubits)
    raw_top_share = raw_top_cnt / total

    feasible_candidates: list[tuple[float, int, str, list[int]]] = []
    feasible_count_sum = 0

    for raw_bs, count in counts.items():
        if count <= 0:
            continue
        bs = raw_bs.zfill(n_qubits)
        if len(bs) != n_qubits:
            continue
        bits = [bit(bs, q) for q in range(n_qubits)]
        feasible, cost = is_feasible_bitstring(bits, options)
        if feasible:
            feasible_count_sum += count
            # Sort key: lowest dollar cost, then highest shot count, then lexicographical bitstring
            feasible_candidates.append((round(cost, 9), -count, bs, bits))

    if not feasible_candidates:
        raise RuntimeError(
            "No feasible bitstring was sampled in the final QAOA measurement counts "
            f"(raw top bitstring='{raw_top_bs}', share={raw_top_share:.3f})."
        )

    feasible_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, neg_best_cnt, best_bs, best_bits = feasible_candidates[0]
    best_share = (-neg_best_cnt) / total
    feasible_share = feasible_count_sum / total
    selected = [opt for opt, b in zip(options, best_bits) if b == 1]
    selected_cost = sum(opt.total_cost for opt in selected)

    return (
        best_bs,
        float(best_share),
        float(feasible_share),
        raw_top_bs,
        float(raw_top_share),
        selected,
        float(selected_cost),
    )


def solve_qaoa(
    backend: Backend,
    options: list[ScheduleOption],
    ising_const: float,
    h: np.ndarray,
    J: dict[tuple[int, int], float],
    p_layers: int = 2,
    shots: int = 2048,
    opt_shots: int = 1024,
    max_cobyla_iters: int = 45,
) -> QAOAResult:
    """Run endianness check, COBYLA QAOA optimization, and final high-shot sampling."""
    n_qubits = len(options)
    circuit_runs = 0

    # 1. Runtime SDK smoke test
    end_passed, end_details = endianness_check(backend)
    circuit_runs += 1

    # Scale gamma parameters by the largest Ising coefficient so COBYLA variables are O(1)
    max_j = max((abs(v) for v in J.values()), default=0.0)
    max_h = float(np.max(np.abs(h))) if len(h) > 0 else 0.0
    coeff_scale = max(max_h, max_j, 1.0)

    best_state: dict[str, Any] = {
        "energy": float("inf"),
        "u": None,
        "betas": None,
    }

    def eval_scaled(u_vec: np.ndarray, beta_vec: np.ndarray, eval_shots: int) -> float:
        nonlocal circuit_runs
        gammas = [float(u / coeff_scale) for u in u_vec]
        betas = [float(b) for b in beta_vec]
        qc = build_qaoa_circuit(backend, n_qubits, h, J, gammas, betas)
        counts = backend.run_counts(qc, shots=eval_shots)
        circuit_runs += 1
        energy = expected_ising_energy(counts, n_qubits, ising_const, h, J)
        if energy < best_state["energy"]:
            best_state["energy"] = energy
            best_state["u"] = list(u_vec)
            best_state["betas"] = list(beta_vec)
        return energy

    # 2. Quick coarse multi-start initialization in the energy-minimizing quadrant
    # Since |+>^n is the ground state of -sum X_q, exp(-i*beta*X) requires beta < 0 for gamma > 0.
    initial_seeds = [
        (np.array([0.35, 0.70][:p_layers]), np.array([-0.55, -0.30][:p_layers])),
        (np.array([0.60, 1.10][:p_layers]), np.array([-0.40, -0.20][:p_layers])),
        (np.array([0.25, 0.50][:p_layers]), np.array([-0.70, -0.35][:p_layers])),
        (np.array([0.85, 1.30][:p_layers]), np.array([-0.30, -0.15][:p_layers])),
    ]
    if p_layers > 2:
        initial_seeds = [
            (np.linspace(0.3, 0.9, p_layers), -np.linspace(0.65, 0.25, p_layers)),
            (np.linspace(0.5, 1.2, p_layers), -np.linspace(0.50, 0.20, p_layers)),
        ]

    best_seed_energy = float("inf")
    best_x0 = np.concatenate(initial_seeds[0])
    for u_init, b_init in initial_seeds:
        e_init = eval_scaled(u_init, b_init, eval_shots=512)
        if e_init < best_seed_energy:
            best_seed_energy = e_init
            best_x0 = np.concatenate([u_init, b_init])

    # 3. Classical COBYLA optimization with explicit evaluation cap
    eval_counter = {"n": 0}

    def cobyla_obj(x: np.ndarray) -> float:
        if eval_counter["n"] >= max_cobyla_iters:
            return float(best_state["energy"] + 1000.0)
        eval_counter["n"] += 1
        u_vec = x[:p_layers]
        b_vec = x[p_layers:]
        return eval_scaled(u_vec, b_vec, eval_shots=opt_shots)

    minimize(
        cobyla_obj,
        best_x0,
        method="COBYLA",
        options={"maxiter": max_cobyla_iters, "rhobeg": 0.25},
    )

    opt_gammas = [float(u / coeff_scale) for u in best_state["u"]]
    opt_betas = [float(b) for b in best_state["betas"]]

    # 4. Final high-shot measurement at optimal angles
    final_qc = build_qaoa_circuit(backend, n_qubits, h, J, opt_gammas, opt_betas)
    final_counts = backend.run_counts(final_qc, shots=shots)
    circuit_runs += 1
    final_energy = expected_ising_energy(final_counts, n_qubits, ising_const, h, J)

    (
        best_bs,
        best_share,
        feasible_share,
        raw_top_bs,
        raw_top_share,
        selected_options,
        selected_cost,
    ) = filter_and_select_best(final_counts, options)

    return QAOAResult(
        method="Post-filtered one-hot penalty QAOA",
        n_qubits=n_qubits,
        p_layers=p_layers,
        shots=shots,
        circuit_runs=circuit_runs,
        endianness_check_passed=end_passed,
        endianness_details=end_details,
        optimal_gammas=opt_gammas,
        optimal_betas=opt_betas,
        optimal_ising_energy=float(final_energy),
        counts=final_counts,
        best_bitstring=best_bs,
        best_bitstring_share=best_share,
        feasible_shot_share=feasible_share,
        raw_most_frequent_bitstring=raw_top_bs,
        raw_most_frequent_share=raw_top_share,
        selected_options=selected_options,
        selected_cost=selected_cost,
    )
