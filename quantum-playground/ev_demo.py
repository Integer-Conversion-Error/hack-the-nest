"""Deterministic EV preview, not a quantum solver or an optimal dispatch policy."""
import math
from uuid import uuid4

DEPOT = [2, 2]
CHARGERS = [[2, 1], [5, 4]]
LOCATIONS = [[0, 0], [1, 4], [2, 5], [3, 0], [4, 2], [5, 0], [5, 5], [3, 4]]
SPECS = [
    ('battery_kwh', 'Battery capacity', 10, 40, 1, 20, 'kWh'),
    ('reserve_pct', 'Minimum reserve', 5, 30, 1, 15, '%'),
    ('charging_kw', 'Station charging power', 2, 22, 1, 10, 'kW'),
    ('consumption', 'Energy consumption', .1, .6, .05, .3, 'kWh/km'),
    ('electricity_price', 'Electricity price', .05, .6, .05, .2, '$/kWh'),
    ('driving_cost', 'Driving cost', .1, 1, .05, .3, '$/km'),
    ('waiting_cost', 'Waiting penalty', 0, 60, 5, 25, '$/hour'),
    ('late_cost', 'Late delivery penalty', 0, 120, 10, 60, '$/hour'),
    ('undelivered_cost', 'Undelivered package', 0, 300, 10, 150, '$/package'),
    ('speed_kmh', 'Travel speed (assumption)', 5, 50, 1, 20, 'km/h'),
    ('service_minutes', 'Service time (assumption)', 0, 20, 1, 5, 'min'),
] + [(f'initial_{i+1}', f'Van {i+1} initial charge', 5, 100, 1, v, '%') for i, v in enumerate([20, 35, 45, 60])]

PROMPT = '''EV Fleet Dispatch Optimization
Optimize delivery routes and charging schedules for 4 electric vans, 8 deliveries, and 2 charging stations, minimizing operating costs and delays.
Map: 6 x 6 street grid, coordinates (0,0) to (5,5), 500 m per block, two-way streets.
Depot: (2,2). Chargers: (2,1), (5,4).
Deliveries: (0,0), (1,4), (2,5), (3,0), (4,2), (5,0), (5,5), (3,4).
All vans start at the depot. Battery: 20 kWh. Initial charge: 20%, 35%, 45%, 60%.
Consumption: 0.30 kWh/km. Charging power: 10 kW per station. Electricity: $0.20/kWh.
Operating hours: 8 AM to noon. Minimum battery: 15%. Charging intervals: 30 minutes.
Costs: driving $0.30/km; waiting $25/hour; late deliveries $60/hour; undelivered $150/package.
Maintain reserves, prevent charger conflicts, respect capacity and delivery deadlines.
Routes calculated classically; QAOA optimizes charging: 4 vans x 2 chargers x 4 slots = 32 binary variables; target fewer than 80 qubits including auxiliaries.
Missing inputs to clarify in live mode: travel speed, service times, capacities/package sizes, individual deadlines, return-to-depot rule, and which four charging slots fit the four-hour day.'''


def variables(overrides=None):
    overrides = overrides or {}
    if set(overrides) - {s[0] for s in SPECS}:
        raise ValueError('Unknown EV parameter.')
    result = []
    for key, label, low, high, step, default, unit in SPECS:
        value = overrides.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{label} must be between {low} and {high}.')
        result.append(dict(id=key, label=label, min=low, max=high, step=step, default=value, unit=unit))
    return result


def street_path(start, end):
    path = [list(start)]
    for axis in (0, 1):
        while path[-1][axis] != end[axis]:
            point = path[-1].copy()
            point[axis] += 1 if end[axis] > point[axis] else -1
            path.append(point)
    return path


def evaluate(sim, p):
    """Check the preview's reserves, station conflicts, deliveries, and return times."""
    violations, reservations, completed = [], [], {}
    costs = dict(driving=0., electricity=0., waiting=0., late=0., undelivered=0.)
    for van in sim['vans']:
        if len(van['delivery_ids']) > 2:
            violations.append(f"{van['id']}: exceeds two-package demo capacity")
        energy = van['initial_battery_kwh']
        if energy < p['battery_kwh'] * p['reserve_pct'] / 100 - 1e-7:
            violations.append(f"{van['id']}: starts below reserve")
        end = 0
        position = DEPOT
        for event in van['events']:
            if event['start_min'] < end - 1e-7:
                violations.append(f"{van['id']}: overlapping actions")
            if event['position'] != position:
                violations.append(f"{van['id']}: discontinuous route")
            end = event['end_min']
            if event['type'] == 'drive':
                distance = .5 * (len(event['path']) - 1)
                energy -= distance * p['consumption']
                position = event['path'][-1]
                costs['driving'] += distance * p['driving_cost']
            elif event['type'] == 'charge':
                energy += event['energy_kwh']
                costs['electricity'] += event['energy_kwh'] * p['electricity_price']
                reservations.append((event['charger_id'], event['start_min'], end, van['id']))
            elif event['type'] == 'wait':
                costs['waiting'] += (end - event['start_min']) / 60 * p['waiting_cost']
            elif event['type'] == 'deliver' and end <= 240:
                completed[event['delivery_id']] = end
                costs['late'] += max(0, end - 180) / 60 * p['late_cost']
            if energy < p['battery_kwh'] * p['reserve_pct'] / 100 - 1e-7:
                violations.append(f"{van['id']}: battery reserve violated")
            if energy > p['battery_kwh'] + 1e-7:
                violations.append(f"{van['id']}: battery capacity exceeded")
        if position != DEPOT or end > 240:
            violations.append(f"{van['id']}: did not return to depot by noon")
    for i, (station, start, end, van) in enumerate(reservations):
        for other_station, other_start, other_end, other_van in reservations[i+1:]:
            if station == other_station and max(start, other_start) < min(end, other_end):
                violations.append(f'{station}: conflict between {van} and {other_van}')
    for delivery in sim['deliveries']:
        arrival = completed.get(delivery['id'])
        if arrival is None:
            violations.append(f"{delivery['id']}: undelivered by noon")
        elif arrival > delivery['deadline_min']:
            violations.append(f"{delivery['id']}: late delivery")
    costs['undelivered'] = (8 - len(completed)) * p['undelivered_cost']
    costs = {k: round(v, 2) for k, v in costs.items()}
    return dict(feasible=not violations, objective_value=round(sum(costs.values()), 2),
                costs=costs, violations=sorted(set(violations)),
                optimality_proven=False, delivered=len(completed), evaluation_scope='Demo assumptions and deterministic preview schedule')


def demo(prompt, overrides):
    specs = variables(overrides)
    p = {v['id']: v['default'] for v in specs}
    sim = dict(kind='ev_fleet', grid=dict(width=6, height=6, block_km=.5), depot=DEPOT,
               chargers=[dict(id=f'C{i+1}', position=pos) for i, pos in enumerate(CHARGERS)],
               deliveries=[dict(id=f'D{i+1}', position=pos, deadline_min=180) for i, pos in enumerate(LOCATIONS)],
               start_hour=8, duration_min=240, charging_slot_count=4, slot_minutes=30,
               battery_capacity_kwh=p['battery_kwh'], reserve_pct=p['reserve_pct'], vans=[], counts={})
    # Fixed delivery assignment and greedy charging, solely for interface development.
    assignments = [[0, 3], [1, 2], [4, 5], [7, 6]]
    slots = set()
    for index, assignment in enumerate(assignments):
        van = dict(id=f'V{index+1}', initial_battery_kwh=p['battery_kwh']*p[f'initial_{index+1}']/100,
                   delivery_ids=[f'D{i+1}' for i in assignment], events=[])
        time, pos, battery = 0., DEPOT.copy(), van['initial_battery_kwh']

        def event(kind, duration, **extra):
            nonlocal time, battery
            end_energy = extra.pop('end_energy', battery)
            van['events'].append(dict(type=kind, start_min=time, end_min=time+duration,
                                     position=pos.copy(), battery_start_kwh=battery,
                                     battery_end_kwh=end_energy, **extra))
            time += duration
            battery = end_energy

        def drive(destination):
            nonlocal pos
            path = street_path(pos, destination)
            distance = .5 * (len(path)-1)
            if distance:
                event('drive', distance/p['speed_kmh']*60, path=path,
                      end_energy=battery-distance*p['consumption'])
            pos = list(destination)

        tour = [DEPOT] + [LOCATIONS[d] for d in assignment] + [DEPOT]
        route_km = sum(.5*(len(street_path(a, b))-1) for a, b in zip(tour, tour[1:]))
        needs_reserve = battery - route_km*p['consumption'] < p['battery_kwh']*p['reserve_pct']/100
        if p[f'initial_{index+1}'] < 50 or needs_reserve:
            options = []
            for station, destination in enumerate(CHARGERS):
                distance = .5*(len(street_path(pos, destination))-1)
                arrival = distance/p['speed_kmh']*60
                for slot in range(4):
                    if (station, slot) not in slots and slot*30 >= arrival:
                        options.append((slot*30, distance, station, slot))
            if options:
                start, _, station, slot = min(options)
                slots.add((station, slot))
                drive(CHARGERS[station])
                if start > time:
                    event('wait', start-time, charger_id=f'C{station+1}')
                energy = min(p['charging_kw']*.5, max(0, p['battery_kwh']-battery))
                event('charge', 30, charger_id=f'C{station+1}', energy_kwh=energy,
                      end_energy=battery+energy)
        for delivery in assignment:
            drive(LOCATIONS[delivery])
            event('deliver', p['service_minutes'], delivery_id=f'D{delivery+1}')
        drive(DEPOT)
        sim['vans'].append(van)
    ev = evaluate(sim, p)
    assumptions = ['Demo uses a fixed delivery assignment and greedy charging; no Gemini or QAOA execution.',
                   f"Travel speed {p['speed_kmh']:g} km/h; service time {p['service_minutes']:g} minutes per delivery.",
                   'Capacity: two equal-size packages per van. All delivery deadlines: 11 AM. Return to depot by noon.',
                   'Four charging slots: 8–10 AM; operations continue until noon. Chargers initially free.',
                   'Constant charging power up to battery capacity; no charging losses or traffic.']
    return dict(run_id=str(uuid4()), mode='demo', status='completed' if ev['feasible'] else 'no_go',
                problem=dict(title='EV Fleet Dispatch', statement='Plan routes and charging for four electric vans making eight deliveries. Keep costs and delays low, maintain enough battery, and avoid booking a charger for two vans at once.', original_prompt=prompt),
                variables=specs, simulation=sim, evaluation=ev, assumptions=assumptions,
                quantum=dict(binary_variables=32, qubit_target=80, actual_qubits=None),
                circuits=[dict(title='Charging decision mapping · schematic', drawing='x[v, c, t] ∈ {0,1}\n4 vans × 2 chargers × 4 slots\n= 32 binary decisions\n\nActual QAOA circuit: awaiting backend'),
                          dict(title='QAOA structure · conceptual', drawing='|+> preparation\n     ↓\nCost unitary U(C, γ)\n     ↓\nMixer unitary U(B, β)\n     ↓\nMeasure charging assignments\n\nSchematic only · not an executed circuit')],
                explanation=f"Deterministic preview: {ev['delivered']}/8 deliveries completed by noon. Schedule cost: ${ev['objective_value']:.2f}. "
                            + ('The preview passes the listed demo checks. ' if ev['feasible'] else 'This preview violates constraints; inspect the issues below. ')
                            + 'This is not an optimized quantum result. Your prompt is retained but not parsed in demo mode.',
                events=['Loaded EV fixture', 'Built preview schedule', 'Checked constraints', 'Ready for playback'])
