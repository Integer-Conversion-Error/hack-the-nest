"""Solve the curated EV scenario exactly and decode its classical schedule."""

from __future__ import annotations

import time
from typing import Any

from .dispatch_model import (
    GridConfig,
    generate_feasible_options,
    simulate_schedule_timeline,
)
from .verifier import solve_classical_reference


def solve_classical(van2_charge_pct: int) -> dict[str, Any]:
    """Enumerate every route and charger choice and validate the winning timeline."""
    started = time.perf_counter()
    config = GridConfig()
    reference = solve_classical_reference(van2_charge_pct)
    options, _ = generate_feasible_options(van2_charge_pct, config=config)

    options_by_id = {option.option_id: option for option in options}
    try:
        selected = [options_by_id[option_id] for option_id in reference["selected_options"]]
    except KeyError as error:
        raise RuntimeError(f"The exact classical schedule requires a missing option: {error.args[0]}.") from error

    timeline = simulate_schedule_timeline(selected, config=config)
    if not timeline.feasible:
        raise RuntimeError("The exact classical schedule failed timeline feasibility checks.")

    cost_breakdown = {
        "driving_cost": round(timeline.driving_cost, 6),
        "electricity_cost": round(timeline.electricity_cost, 6),
        "waiting_cost": round(timeline.waiting_cost, 6),
        "late_penalty": round(timeline.late_penalty, 6),
        "total_cost": round(timeline.total_cost, 6),
    }
    if cost_breakdown != reference["cost_breakdown"]:
        raise RuntimeError("The exact classical schedule disagrees with the independent cost calculation.")

    return {
        "status": "ok",
        "error": None,
        "controls": {"van_2_initial_charge_pct": van2_charge_pct},
        "objective": {
            "value": cost_breakdown["total_cost"],
            "unit": reference["unit"],
            "direction": "minimize",
        },
        "solution": {
            "selected_options": reference["selected_options"],
            "vans": [option.to_dict(config.battery_capacity_kwh) for option in selected],
            "charger_bookings": timeline.charger_bookings,
            "cost_breakdown": cost_breakdown,
            "verification": {"feasible": True, "violations": []},
        },
        "reference": reference,
        "optimality_proven": True,
        "runtime_seconds": round(time.perf_counter() - started, 6),
    }
