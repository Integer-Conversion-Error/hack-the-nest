"""Classical grid routing, battery simulation, and option generation for EV fleet dispatch."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class GridConfig:
    grid_size: int = 6
    block_length_km: float = 0.5
    depot: tuple[int, int] = (2, 2)
    charger: tuple[int, int] = (2, 1)
    num_charger_plugs: int = 1
    departure_min: float = 0.0  # 8:00 AM
    return_deadline_min: float = 120.0  # 10:00 AM
    speed_kmh: float = 30.0
    delivery_service_min: float = 5.0
    battery_capacity_kwh: float = 20.0
    energy_per_km_kwh: float = 0.30
    min_soc_pct: float = 15.0
    charger_power_kw: float = 10.0
    slot_1_window: tuple[float, float] = (30.0, 60.0)  # 8:30-9:00 AM
    slot_2_window: tuple[float, float] = (60.0, 90.0)  # 9:00-9:30 AM
    driving_cost_per_km: float = 0.30
    electricity_cost_per_kwh: float = 0.20
    waiting_cost_per_hour: float = 25.0
    late_penalty_per_hour: float = 60.0

    @property
    def min_soc_kwh(self) -> float:
        return self.battery_capacity_kwh * (self.min_soc_pct / 100.0)

    @property
    def minutes_per_block(self) -> float:
        return (self.block_length_km / self.speed_kmh) * 60.0

    @property
    def energy_per_block_kwh(self) -> float:
        return self.block_length_km * self.energy_per_km_kwh


@dataclass(frozen=True)
class VanSpec:
    van_id: int
    name: str
    destination: tuple[int, int]
    deadline_min: float
    initial_charge_pct: float

    def initial_soc_kwh(self, capacity_kwh: float) -> float:
        return capacity_kwh * (self.initial_charge_pct / 100.0)


@dataclass
class TimelineLeg:
    leg_type: str  # "drive", "wait", "charge", "deliver"
    description: str
    start_coord: tuple[int, int]
    end_coord: tuple[int, int]
    path: list[tuple[int, int]]
    start_min: float
    end_min: float
    start_time_str: str
    end_time_str: str
    distance_km: float
    energy_delta_kwh: float
    soc_start_kwh: float
    soc_end_kwh: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_coord"] = list(self.start_coord)
        data["end_coord"] = list(self.end_coord)
        data["path"] = [list(p) for p in self.path]
        return data


@dataclass
class ScheduleOption:
    qubit_index: int
    option_id: str
    van_id: int
    van_name: str
    mode: str  # "Direct", "Slot 1", "Slot 2"
    charger_slot: int | None
    feasible: bool
    infeasibility_reason: str | None
    initial_soc_kwh: float
    total_distance_km: float
    energy_charged_kwh: float
    wait_minutes: float
    late_minutes: float
    delivery_completion_min: float
    depot_return_min: float
    min_soc_kwh: float
    final_soc_kwh: float
    driving_cost: float
    electricity_cost: float
    waiting_cost: float
    late_penalty: float
    total_cost: float
    legs: list[TimelineLeg] = field(default_factory=list)

    def to_dict(self, capacity_kwh: float = 20.0) -> dict[str, Any]:
        return {
            "qubit_index": self.qubit_index,
            "option_id": self.option_id,
            "van_id": self.van_id,
            "van_name": self.van_name,
            "mode": self.mode,
            "charger_slot": self.charger_slot,
            "feasible": self.feasible,
            "initial_soc_kwh": round(self.initial_soc_kwh, 6),
            "initial_soc_pct": round(100.0 * self.initial_soc_kwh / capacity_kwh, 4),
            "total_distance_km": round(self.total_distance_km, 6),
            "energy_charged_kwh": round(self.energy_charged_kwh, 6),
            "wait_minutes": round(self.wait_minutes, 6),
            "late_minutes": round(self.late_minutes, 6),
            "delivery_completion_min": round(self.delivery_completion_min, 6),
            "delivery_completion_time": format_clock(self.delivery_completion_min),
            "depot_return_min": round(self.depot_return_min, 6),
            "depot_return_time": format_clock(self.depot_return_min),
            "min_soc_kwh": round(self.min_soc_kwh, 6),
            "min_soc_pct": round(100.0 * self.min_soc_kwh / capacity_kwh, 4),
            "final_soc_kwh": round(self.final_soc_kwh, 6),
            "final_soc_pct": round(100.0 * self.final_soc_kwh / capacity_kwh, 4),
            "driving_cost": round(self.driving_cost, 6),
            "electricity_cost": round(self.electricity_cost, 6),
            "waiting_cost": round(self.waiting_cost, 6),
            "late_penalty": round(self.late_penalty, 6),
            "total_cost": round(self.total_cost, 6),
            "legs": [leg.to_dict() for leg in self.legs],
        }


@dataclass
class TimelineResult:
    selected_options: list[ScheduleOption]
    feasible: bool
    violations: list[str]
    driving_cost: float
    electricity_cost: float
    waiting_cost: float
    late_penalty: float
    total_cost: float
    charger_bookings: dict[str, str | None]


def format_clock(minutes_from_8am: float) -> str:
    total_minutes = int(round(8 * 60 + minutes_from_8am))
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def manhattan_path(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """Return shortest Manhattan intersection sequence from start to end."""
    x, y = start
    tx, ty = end
    path = [(x, y)]
    while x != tx:
        x += 1 if tx > x else -1
        path.append((x, y))
    while y != ty:
        y += 1 if ty > y else -1
        path.append((x, y))
    return path


def _simulate_single_option(
    van: VanSpec,
    mode: str,
    config: GridConfig,
    qubit_index: int = -1,
) -> ScheduleOption:
    t = config.departure_min
    soc = van.initial_soc_kwh(config.battery_capacity_kwh)
    initial_soc = soc
    min_soc = soc
    total_dist_km = 0.0
    energy_charged = 0.0
    wait_min = 0.0
    legs: list[TimelineLeg] = []

    def add_drive_leg(src: tuple[int, int], dst: tuple[int, int], label: str) -> None:
        nonlocal t, soc, min_soc, total_dist_km
        path = manhattan_path(src, dst)
        blocks = len(path) - 1
        dist_km = blocks * config.block_length_km
        dur_min = blocks * config.minutes_per_block
        energy_used = dist_km * config.energy_per_km_kwh
        soc_start = soc
        soc_end = soc - energy_used
        legs.append(
            TimelineLeg(
                leg_type="drive",
                description=label,
                start_coord=src,
                end_coord=dst,
                path=path,
                start_min=t,
                end_min=t + dur_min,
                start_time_str=format_clock(t),
                end_time_str=format_clock(t + dur_min),
                distance_km=dist_km,
                energy_delta_kwh=-energy_used,
                soc_start_kwh=soc_start,
                soc_end_kwh=soc_end,
            )
        )
        t += dur_min
        soc = soc_end
        min_soc = min(min_soc, soc)
        total_dist_km += dist_km

    charger_slot: int | None = None
    infeasibility_reasons: list[str] = []

    if mode == "Direct":
        add_drive_leg(config.depot, van.destination, f"{van.name} drives Depot -> Delivery {van.destination}")
    elif mode in ("Slot 1", "Slot 2"):
        charger_slot = 1 if mode == "Slot 1" else 2
        slot_start, slot_end = config.slot_1_window if charger_slot == 1 else config.slot_2_window
        add_drive_leg(config.depot, config.charger, f"{van.name} drives Depot -> Charger {config.charger}")
        if t > slot_start + 1e-9:
            infeasibility_reasons.append(f"Arrives at charger at t={t:.2f} after {mode} start t={slot_start:.2f}")
        elif t < slot_start:
            wait_dur = slot_start - t
            legs.append(
                TimelineLeg(
                    leg_type="wait",
                    description=f"{van.name} waits at Charger for {mode}",
                    start_coord=config.charger,
                    end_coord=config.charger,
                    path=[config.charger],
                    start_min=t,
                    end_min=slot_start,
                    start_time_str=format_clock(t),
                    end_time_str=format_clock(slot_start),
                    distance_km=0.0,
                    energy_delta_kwh=0.0,
                    soc_start_kwh=soc,
                    soc_end_kwh=soc,
                )
            )
            wait_min += wait_dur
            t = slot_start

        slot_hours = (slot_end - slot_start) / 60.0
        max_deliverable = config.charger_power_kw * slot_hours
        energy_added = min(max_deliverable, config.battery_capacity_kwh - soc)
        soc_after = soc + energy_added
        legs.append(
            TimelineLeg(
                leg_type="charge",
                description=f"{van.name} charges in {mode}",
                start_coord=config.charger,
                end_coord=config.charger,
                path=[config.charger],
                start_min=slot_start,
                end_min=slot_end,
                start_time_str=format_clock(slot_start),
                end_time_str=format_clock(slot_end),
                distance_km=0.0,
                energy_delta_kwh=energy_added,
                soc_start_kwh=soc,
                soc_end_kwh=soc_after,
            )
        )
        t = slot_end
        soc = soc_after
        energy_charged = energy_added
        add_drive_leg(config.charger, van.destination, f"{van.name} drives Charger -> Delivery {van.destination}")
    else:
        raise ValueError(f"Unsupported schedule mode: {mode}")

    # Delivery service at customer intersection
    legs.append(
        TimelineLeg(
            leg_type="deliver",
            description=f"{van.name} completes delivery at {van.destination}",
            start_coord=van.destination,
            end_coord=van.destination,
            path=[van.destination],
            start_min=t,
            end_min=t + config.delivery_service_min,
            start_time_str=format_clock(t),
            end_time_str=format_clock(t + config.delivery_service_min),
            distance_km=0.0,
            energy_delta_kwh=0.0,
            soc_start_kwh=soc,
            soc_end_kwh=soc,
        )
    )
    t += config.delivery_service_min
    delivery_completion_min = t
    late_min = max(0.0, delivery_completion_min - van.deadline_min)

    # Return to depot
    add_drive_leg(van.destination, config.depot, f"{van.name} returns Delivery -> Depot {config.depot}")
    depot_return_min = t

    if min_soc < config.min_soc_kwh - 1e-9:
        infeasibility_reasons.append(
            f"Minimum SoC {min_soc:.3f} kWh violates {config.min_soc_kwh:.3f} kWh ({config.min_soc_pct:.0f}%) floor"
        )
    if depot_return_min > config.return_deadline_min + 1e-9:
        infeasibility_reasons.append(
            f"Depot return {format_clock(depot_return_min)} exceeds 10:00 deadline"
        )

    driving_cost = total_dist_km * config.driving_cost_per_km
    electricity_cost = energy_charged * config.electricity_cost_per_kwh
    waiting_cost = (wait_min / 60.0) * config.waiting_cost_per_hour
    late_penalty = (late_min / 60.0) * config.late_penalty_per_hour
    total_cost = driving_cost + electricity_cost + waiting_cost + late_penalty

    mode_tag = mode.replace(" ", "")
    option_id = f"Van{van.van_id}_{mode_tag}"

    return ScheduleOption(
        qubit_index=qubit_index,
        option_id=option_id,
        van_id=van.van_id,
        van_name=van.name,
        mode=mode,
        charger_slot=charger_slot,
        feasible=len(infeasibility_reasons) == 0,
        infeasibility_reason="; ".join(infeasibility_reasons) if infeasibility_reasons else None,
        initial_soc_kwh=initial_soc,
        total_distance_km=total_dist_km,
        energy_charged_kwh=energy_charged,
        wait_minutes=wait_min,
        late_minutes=late_min,
        delivery_completion_min=delivery_completion_min,
        depot_return_min=depot_return_min,
        min_soc_kwh=min_soc,
        final_soc_kwh=soc,
        driving_cost=driving_cost,
        electricity_cost=electricity_cost,
        waiting_cost=waiting_cost,
        late_penalty=late_penalty,
        total_cost=total_cost,
        legs=legs,
    )


def build_van_specs(van2_charge_pct: int) -> list[VanSpec]:
    if van2_charge_pct not in (20, 35):
        raise ValueError(f"van2_charge_pct must be 20 or 35, got {van2_charge_pct}")
    return [
        VanSpec(
            van_id=1,
            name="Van 1",
            destination=(0, 0),
            deadline_min=90.0,  # 9:30 AM
            initial_charge_pct=20.0,
        ),
        VanSpec(
            van_id=2,
            name="Van 2",
            destination=(5, 0),
            deadline_min=60.0,  # 9:00 AM
            initial_charge_pct=float(van2_charge_pct),
        ),
    ]


def generate_feasible_options(
    van2_charge_pct: int,
    config: GridConfig | None = None,
) -> tuple[list[ScheduleOption], dict[str, Any]]:
    """Enumerate candidate route/charging modes per van and prune infeasible options."""
    cfg = config or GridConfig()
    vans = build_van_specs(van2_charge_pct)
    modes = ("Direct", "Slot 1", "Slot 2")

    feasible_options: list[ScheduleOption] = []
    pruned_options: list[dict[str, Any]] = []
    van_option_indices: dict[int, list[int]] = {v.van_id: [] for v in vans}

    for van in vans:
        for mode in modes:
            candidate = _simulate_single_option(van, mode, cfg)
            if candidate.feasible:
                q_idx = len(feasible_options)
                candidate.qubit_index = q_idx
                feasible_options.append(candidate)
                van_option_indices[van.van_id].append(q_idx)
            else:
                pruned_options.append(
                    {
                        "option_id": candidate.option_id,
                        "van_id": candidate.van_id,
                        "mode": candidate.mode,
                        "reason": candidate.infeasibility_reason,
                    }
                )

    metadata: dict[str, Any] = {
        "van_2_initial_charge_pct": van2_charge_pct,
        "num_qubits": len(feasible_options),
        "van_option_indices": van_option_indices,
        "pruned_options": pruned_options,
        "config": {
            "grid_size": cfg.grid_size,
            "block_length_km": cfg.block_length_km,
            "depot": list(cfg.depot),
            "charger": list(cfg.charger),
            "min_soc_kwh": cfg.min_soc_kwh,
            "departure_time": format_clock(cfg.departure_min),
            "return_deadline_time": format_clock(cfg.return_deadline_min),
        },
    }
    return feasible_options, metadata


def simulate_schedule_timeline(
    selection: list[ScheduleOption],
    config: GridConfig | None = None,
) -> TimelineResult:
    """Validate and aggregate a combined schedule selection across both vans."""
    cfg = config or GridConfig()
    violations: list[str] = []
    van_ids = [opt.van_id for opt in selection]
    if sorted(van_ids) != [1, 2]:
        violations.append(f"Expected exactly one option for Van 1 and Van 2, got van_ids={van_ids}")

    slot_bookings: dict[str, str | None] = {"slot_1": None, "slot_2": None}
    for opt in selection:
        if not opt.feasible:
            violations.append(f"Selected option {opt.option_id} is individually infeasible: {opt.infeasibility_reason}")
        if not opt.legs or abs(opt.legs[0].start_min - cfg.departure_min) > 1e-9:
            dep_str = format_clock(opt.legs[0].start_min) if opt.legs else "N/A"
            violations.append(
                f"Option {opt.option_id} departs at {dep_str}, violating exact {format_clock(cfg.departure_min)} departure"
            )
        if opt.depot_return_min > cfg.return_deadline_min + 1e-9:
            violations.append(
                f"Option {opt.option_id} returns at {format_clock(opt.depot_return_min)}, exceeding {format_clock(cfg.return_deadline_min)} deadline"
            )
        if opt.charger_slot == 1:
            if slot_bookings["slot_1"] is not None:
                violations.append(f"Charger Slot 1 double-booked by {slot_bookings['slot_1']} and {opt.option_id}")
            else:
                slot_bookings["slot_1"] = opt.option_id
        elif opt.charger_slot == 2:
            if slot_bookings["slot_2"] is not None:
                violations.append(f"Charger Slot 2 double-booked by {slot_bookings['slot_2']} and {opt.option_id}")
            else:
                slot_bookings["slot_2"] = opt.option_id

    driving_cost = sum(opt.driving_cost for opt in selection)
    electricity_cost = sum(opt.electricity_cost for opt in selection)
    waiting_cost = sum(opt.waiting_cost for opt in selection)
    late_penalty = sum(opt.late_penalty for opt in selection)
    total_cost = driving_cost + electricity_cost + waiting_cost + late_penalty

    return TimelineResult(
        selected_options=selection,
        feasible=len(violations) == 0,
        violations=violations,
        driving_cost=driving_cost,
        electricity_cost=electricity_cost,
        waiting_cost=waiting_cost,
        late_penalty=late_penalty,
        total_cost=total_cost,
        charger_bookings=slot_bookings,
    )
