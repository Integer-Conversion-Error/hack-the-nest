"""Independent schedule verifier and exact classical reference solver.

Recomputes Manhattan distances, timestamps, battery SoC trajectories,
constraint satisfaction, and itemized dollar costs directly from raw problem
statement constants, without importing dispatch_model or qubo_ising.
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np


RAW_PROBLEM = {
    "grid_bounds": (0, 5),
    "block_length_km": 0.5,
    "depot": (2, 2),
    "charger": (2, 1),
    "num_plugs": 1,
    "van1_dest": (0, 0),
    "van1_deadline_min": 90.0,   # 9:30 AM (minutes after 8:00 AM)
    "van1_init_pct": 20.0,
    "van2_dest": (5, 0),
    "van2_deadline_min": 60.0,   # 9:00 AM
    "departure_min": 0.0,        # 8:00 AM
    "return_deadline_min": 120.0,  # 10:00 AM
    "speed_kmh": 30.0,
    "delivery_service_min": 5.0,
    "battery_capacity_kwh": 20.0,
    "energy_per_km_kwh": 0.30,
    "min_soc_pct": 15.0,
    "slot_windows": {
        1: (30.0, 60.0),         # 8:30-9:00 AM
        2: (60.0, 90.0),         # 9:00-9:30 AM
    },
    "charger_power_kw": 10.0,
    "driving_cost_per_km": 0.30,
    "electricity_cost_per_kwh": 0.20,
    "waiting_cost_per_hour": 25.0,
    "late_penalty_per_hour": 60.0,
}


def _manhattan_blocks(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _independent_eval_van(van_id: int, mode: str, van2_charge_pct: int) -> dict[str, Any]:
    p = RAW_PROBLEM
    depot = p["depot"]
    charger = p["charger"]
    min_soc_kwh = p["battery_capacity_kwh"] * (p["min_soc_pct"] / 100.0)
    min_per_block = (p["block_length_km"] / p["speed_kmh"]) * 60.0

    if van_id == 1:
        dest = p["van1_dest"]
        deadline = p["van1_deadline_min"]
        init_pct = p["van1_init_pct"]
    elif van_id == 2:
        dest = p["van2_dest"]
        deadline = p["van2_deadline_min"]
        init_pct = float(van2_charge_pct)
    else:
        raise ValueError(f"Unknown van_id={van_id}")

    t = p["departure_min"]
    soc = p["battery_capacity_kwh"] * (init_pct / 100.0)
    min_soc = soc
    total_blocks = 0
    wait_min = 0.0
    charged_kwh = 0.0
    slot_num: int | None = None
    violations: list[str] = []

    def drive(src: tuple[int, int], dst: tuple[int, int]) -> None:
        nonlocal t, soc, min_soc, total_blocks
        blocks = _manhattan_blocks(src, dst)
        dist_km = blocks * p["block_length_km"]
        t += blocks * min_per_block
        soc -= dist_km * p["energy_per_km_kwh"]
        min_soc = min(min_soc, soc)
        total_blocks += blocks

    if mode == "Direct":
        drive(depot, dest)
    elif mode in ("Slot 1", "Slot 2"):
        slot_num = 1 if mode == "Slot 1" else 2
        s_start, s_end = p["slot_windows"][slot_num]
        drive(depot, charger)
        if t > s_start + 1e-9:
            violations.append(f"Van {van_id} arrives at charger at t={t:.2f} after {mode} start {s_start:.2f}")
        elif t < s_start:
            wait_min += s_start - t
            t = s_start
        max_slot_kwh = p["charger_power_kw"] * ((s_end - s_start) / 60.0)
        energy_added = min(max_slot_kwh, p["battery_capacity_kwh"] - soc)
        soc += energy_added
        charged_kwh += energy_added
        t = s_end
        drive(charger, dest)
    else:
        raise ValueError(f"Invalid mode '{mode}'")

    # Delivery service
    t += p["delivery_service_min"]
    delivery_completion = t
    late_min = max(0.0, delivery_completion - deadline)

    # Return to depot
    drive(dest, depot)
    depot_return = t

    if min_soc < min_soc_kwh - 1e-9:
        violations.append(f"Van {van_id} ({mode}) min SoC {min_soc:.4f} kWh < {min_soc_kwh:.4f} kWh floor")
    if depot_return > p["return_deadline_min"] + 1e-9:
        violations.append(f"Van {van_id} ({mode}) depot return {depot_return:.2f} min > {p['return_deadline_min']:.2f} min")

    total_km = total_blocks * p["block_length_km"]
    driving_cost = total_km * p["driving_cost_per_km"]
    electricity_cost = charged_kwh * p["electricity_cost_per_kwh"]
    waiting_cost = (wait_min / 60.0) * p["waiting_cost_per_hour"]
    late_penalty = (late_min / 60.0) * p["late_penalty_per_hour"]
    total_cost = driving_cost + electricity_cost + waiting_cost + late_penalty

    return {
        "van_id": van_id,
        "mode": mode,
        "option_id": f"Van{van_id}_{mode.replace(' ', '')}",
        "charger_slot": slot_num,
        "feasible": len(violations) == 0,
        "violations": violations,
        "min_soc_kwh": min_soc,
        "final_soc_kwh": soc,
        "depot_return_min": depot_return,
        "delivery_completion_min": delivery_completion,
        "wait_minutes": wait_min,
        "late_minutes": late_min,
        "energy_charged_kwh": charged_kwh,
        "total_km": total_km,
        "driving_cost": driving_cost,
        "electricity_cost": electricity_cost,
        "waiting_cost": waiting_cost,
        "late_penalty": late_penalty,
        "total_cost": total_cost,
    }


def independent_evaluate_pair(van1_mode: str, van2_mode: str, van2_charge_pct: int) -> dict[str, Any]:
    """Independently check feasibility and total cost of a (Van 1, Van 2) mode pair."""
    v1 = _independent_eval_van(1, van1_mode, van2_charge_pct)
    v2 = _independent_eval_van(2, van2_mode, van2_charge_pct)
    violations = list(v1["violations"]) + list(v2["violations"])

    if v1["charger_slot"] is not None and v1["charger_slot"] == v2["charger_slot"]:
        violations.append(f"Charger Slot {v1['charger_slot']} conflict between Van 1 and Van 2")

    driving_cost = v1["driving_cost"] + v2["driving_cost"]
    electricity_cost = v1["electricity_cost"] + v2["electricity_cost"]
    waiting_cost = v1["waiting_cost"] + v2["waiting_cost"]
    late_penalty = v1["late_penalty"] + v2["late_penalty"]
    total_cost = driving_cost + electricity_cost + waiting_cost + late_penalty

    return {
        "feasible": len(violations) == 0,
        "violations": violations,
        "van_1": v1,
        "van_2": v2,
        "selected_options": [v1["option_id"], v2["option_id"]],
        "cost_breakdown": {
            "driving_cost": driving_cost,
            "electricity_cost": electricity_cost,
            "waiting_cost": waiting_cost,
            "late_penalty": late_penalty,
            "total_cost": total_cost,
        },
        "total_cost": total_cost,
    }


def solve_classical_reference(van2_charge_pct: int) -> dict[str, Any]:
    """Exact classical enumeration over all candidate mode pairs from raw problem data."""
    if van2_charge_pct not in (20, 35):
        raise ValueError(f"van2_charge_pct must be 20 or 35, got {van2_charge_pct}")

    modes = ("Direct", "Slot 1", "Slot 2")
    feasible_pairs: list[dict[str, Any]] = []
    for m1 in modes:
        for m2 in modes:
            res = independent_evaluate_pair(m1, m2, van2_charge_pct)
            if res["feasible"]:
                feasible_pairs.append(res)

    if not feasible_pairs:
        raise RuntimeError(f"No feasible classical schedule found for van2_charge_pct={van2_charge_pct}")

    feasible_pairs.sort(key=lambda r: r["total_cost"])
    best = feasible_pairs[0]

    return {
        "method": "Exact classical enumeration over all candidate route-and-charging pairs",
        "objective_value": round(best["total_cost"], 6),
        "unit": "USD",
        "selected_options": best["selected_options"],
        "selected_modes": {
            "van_1": best["van_1"]["mode"],
            "van_2": best["van_2"]["mode"],
        },
        "cost_breakdown": {k: round(v, 6) for k, v in best["cost_breakdown"].items()},
        "feasible_pair_count": len(feasible_pairs),
        "all_feasible_pairs": [
            {
                "selected_options": p["selected_options"],
                "total_cost": round(p["total_cost"], 6),
            }
            for p in feasible_pairs
        ],
    }


def verify_solution(
    selected_decisions: list[tuple[int, str, float]],
    van2_charge_pct: int,
    decision_qubits: int,
    auxiliary_qubits: int,
    reference: dict[str, Any],
    qubo_matrix: np.ndarray | None = None,
    qubo_const: float | None = None,
) -> dict[str, Any]:
    """Independently verify decoded quantum decisions against constraints, QUBO ground state, and reference."""
    by_van = {vid: (mode, reported_cost) for vid, mode, reported_cost in selected_decisions}
    if set(by_van.keys()) != {1, 2} or len(selected_decisions) != 2:
        return {
            "feasible": False,
            "violations": [f"One-hot check failed: decoded decisions={selected_decisions}"],
            "matches_classical_optimum": False,
        }

    m1, rep1 = by_van[1]
    m2, rep2 = by_van[2]
    recheck = independent_evaluate_pair(m1, m2, van2_charge_pct)

    reported_total = rep1 + rep2
    cost_discrepancy = abs(recheck["total_cost"] - reported_total)
    violations = list(recheck["violations"])
    if cost_discrepancy > 1e-6:
        violations.append(
            f"Cost mismatch: solver reported ${reported_total:.6f}, independent verifier computed ${recheck['total_cost']:.6f}"
        )

    qubo_sweep: dict[str, Any] | None = None
    if qubo_matrix is not None and qubo_const is not None:
        n = qubo_matrix.shape[0]
        min_qubo_val = float("inf")
        min_qubo_bits: list[tuple[int, ...]] = []
        for bits in itertools.product((0, 1), repeat=n):
            x = np.array(bits, dtype=float)
            val = float(x @ qubo_matrix @ x + qubo_const)
            if val < min_qubo_val - 1e-9:
                min_qubo_val = val
                min_qubo_bits = [bits]
            elif abs(val - min_qubo_val) <= 1e-9:
                min_qubo_bits.append(bits)
        qubo_sweep = {
            "states_evaluated": 2 ** n,
            "min_qubo_energy": round(min_qubo_val, 6),
            "matches_reference_optimum": abs(min_qubo_val - reference["objective_value"]) < 1e-5,
            "ground_state_count": len(min_qubo_bits),
        }

    optimality_gap = recheck["total_cost"] - reference["objective_value"]
    matches_optimum = recheck["feasible"] and abs(optimality_gap) < 1e-5

    return {
        "feasible": len(violations) == 0,
        "violations": violations,
        "independent_cost_breakdown": {k: round(v, 6) for k, v in recheck["cost_breakdown"].items()},
        "cost_discrepancy_usd": round(cost_discrepancy, 9),
        "optimality_gap_usd": round(optimality_gap, 6),
        "matches_classical_optimum": matches_optimum,
        "circuit_width": {
            "total_qubits": decision_qubits + auxiliary_qubits,
            "decision_qubits": decision_qubits,
            "auxiliary_qubits": auxiliary_qubits,
        },
        "qubo_exhaustive_sweep": qubo_sweep,
    }
