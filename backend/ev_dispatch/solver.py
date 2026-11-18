"""Solve and independently verify one EV dispatch scenario."""

from __future__ import annotations

import time
from typing import Any

from .classical import solve_classical
from .dispatch_model import GridConfig, generate_feasible_options, simulate_schedule_timeline
from .qaoa import Backend, solve_qaoa
from .qubo_ising import build_qubo, qubo_to_ising, verify_ising_equivalence
from .verifier import verify_solution


def solve(van2_charge_pct: int, backend: Backend) -> dict[str, Any]:
    """Return the workspace results schema using fresh measurements for this scenario."""
    started = time.perf_counter()
    config = GridConfig()
    options, metadata = generate_feasible_options(van2_charge_pct, config=config)

    qubo, offset = build_qubo(options)
    ising_offset, h, interactions = qubo_to_ising(qubo, offset)
    ising_error = verify_ising_equivalence(qubo, offset, ising_offset, h, interactions)
    measured = solve_qaoa(backend, options, ising_offset, h, interactions)

    selected = measured.selected_options
    timeline = simulate_schedule_timeline(selected, config=config)
    if not timeline.feasible:
        raise RuntimeError("The sampled schedule failed timeline feasibility checks.")

    classical = solve_classical(van2_charge_pct)
    reference = classical["reference"]
    verification = verify_solution(
        selected_decisions=[(option.van_id, option.mode, option.total_cost) for option in selected],
        van2_charge_pct=van2_charge_pct,
        decision_qubits=measured.n_qubits,
        auxiliary_qubits=0,
        reference=reference,
        qubo_matrix=qubo,
        qubo_const=offset,
    )
    if not verification["feasible"]:
        raise RuntimeError("The sampled schedule failed independent verification.")
    reference["matches_quantum_solution"] = verification["matches_classical_optimum"]

    total_cost = round(timeline.total_cost, 6)
    return {
        "status": "ok",
        "error": None,
        "controls": {"van_2_initial_charge_pct": van2_charge_pct},
        "objective": {"value": total_cost, "unit": "USD", "direction": "minimize"},
        "solution": {
            "selected_options": [option.option_id for option in selected],
            "vans": [option.to_dict(config.battery_capacity_kwh) for option in selected],
            "charger_bookings": timeline.charger_bookings,
            "cost_breakdown": {
                "driving_cost": round(timeline.driving_cost, 6),
                "electricity_cost": round(timeline.electricity_cost, 6),
                "waiting_cost": round(timeline.waiting_cost, 6),
                "late_penalty": round(timeline.late_penalty, 6),
                "total_cost": total_cost,
            },
            "pruned_infeasible_options": metadata["pruned_options"],
            "verification": verification,
        },
        "reference": reference,
        "classical": {
            "method": reference["method"],
            "objective": classical["objective"],
            "solution": classical["solution"],
            "optimality_proven": classical["optimality_proven"],
            "runtime_seconds": classical["runtime_seconds"],
        },
        "quantum": {
            "engine": backend.engine,
            "method": measured.method,
            "qubits": measured.n_qubits,
            "decision_qubits": measured.n_qubits,
            "auxiliary_qubits": 0,
            "p_layers": measured.p_layers,
            "shots": measured.shots,
            "circuit_runs": measured.circuit_runs,
            "counts": measured.counts,
            "best_bitstring": measured.best_bitstring,
            "best_bitstring_share": round(measured.best_bitstring_share, 6),
            "feasible_shot_share": round(measured.feasible_shot_share, 6),
            "raw_most_frequent_bitstring": measured.raw_most_frequent_bitstring,
            "raw_most_frequent_share": round(measured.raw_most_frequent_share, 6),
            "optimal_gammas": [round(value, 8) for value in measured.optimal_gammas],
            "optimal_betas": [round(value, 8) for value in measured.optimal_betas],
            "optimal_ising_energy": round(measured.optimal_ising_energy, 6),
            "endianness_check_passed": measured.endianness_check_passed,
            "ising_equivalence_max_error": ising_error,
            "qubit_mapping": {f"q{option.qubit_index}": option.option_id for option in options},
        },
        "runtime_seconds": round(time.perf_counter() - started, 4),
    }
