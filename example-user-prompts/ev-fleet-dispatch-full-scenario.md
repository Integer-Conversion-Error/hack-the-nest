# EV Fleet Dispatch with Four Vans and 17 Adjustable Settings

Find the lowest-cost schedule for four electric delivery vans using two chargers. Use QAOA to select routes and charging reservations, then visualize the verified schedule. Let me change the battery, timing, and cost settings together and recalculate the dispatch.

## Street grid and shift

- Use a 10 × 10 grid of intersections from (0,0) to (9,9), with two-way streets between adjacent intersections and 500 m per block. The depot is at (4,4). The single-plug chargers are C1 at (4,3) and C2 at (5,4).
- Van 1 delivers to D1 at (1,1), Van 2 to D2 at (7,2), Van 3 to D3 at (2,8), and Van 4 to D4 at (9,7). All four deliveries are required. A due time marks when lateness starts, and delivery completion may occur after the due time.
- All four vans leave the depot at exactly 8:00 AM and must return by 10:00 AM. Use the selected speed and service time for all vans. Follow a shortest grid path between stops, moving horizontally first when a tie is possible. Preserve fractional travel times in the schedule.

## Routes and charging

- Each van has a 20 kWh battery. Apply its own starting charge, the shared energy use per kilometer, and the shared minimum reserve. The reserve must hold throughout the route, including the final return to the depot. Keep full battery precision when checking feasibility.
- For each van consider a direct round trip and a round trip with one charging visit before delivery. If they charge, Vans 1 and 2 use C1 and Vans 3 and 4 use C2. Each charger has two 30-minute slots, 8:30–9:00 AM and 9:00–9:30 AM. A van must reach its charger by the slot start, waits there until the slot starts, and occupies the whole slot. Both chargers use the selected power. A slot delivers that power multiplied by 0.5 hours, capped by available battery capacity.
- Keep up to three feasible options per van, one direct option and one per slot at its charger. Select exactly one route per van and book at most one van per charger in each slot. C1 and C2 can charge vans at the same time.

## Adjustable settings

Expose all 17 settings in the approved plan and the results view using the keys below. All ranges include both endpoints, and each value must align with its step from the minimum. The due-time controls are elapsed minutes from 8:00 AM. Use the same set of selected values throughout each run and allow any combination of the listed settings.

### Battery and charging

| Key | Control | Unit | Minimum | Maximum | Step | Default |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `van_1_initial_charge_pct` | Van 1 starting charge | % | 20 | 25 | 1 | 20 |
| `van_2_initial_charge_pct` | Van 2 starting charge | % | 20 | 35 | 1 | 35 |
| `van_3_initial_charge_pct` | Van 3 starting charge | % | 20 | 34 | 1 | 24 |
| `van_4_initial_charge_pct` | Van 4 starting charge | % | 20 | 35 | 1 | 26 |
| `minimum_reserve_pct` | Minimum battery reserve | % | 15 | 19 | 1 | 15 |
| `energy_use_kwh_per_km` | Energy use | kWh/km | 0.25 | 0.35 | 0.05 | 0.30 |
| `charger_power_kw` | Charger power | kW | 8 | 14 | 2 | 10 |

### Timing

| Key | Control | Unit | Minimum | Maximum | Step | Default |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `driving_speed_km_per_hour` | Driving speed | km/h | 25 | 40 | 5 | 30 |
| `delivery_service_minutes` | Service time per delivery | min | 4 | 8 | 1 | 5 |
| `van_1_delivery_due_min` | Van 1 delivery due | min from 8:00 AM | 80 | 100 | 5 | 90 |
| `van_2_delivery_due_min` | Van 2 delivery due | min from 8:00 AM | 50 | 75 | 5 | 60 |
| `van_3_delivery_due_min` | Van 3 delivery due | min from 8:00 AM | 70 | 105 | 5 | 90 |
| `van_4_delivery_due_min` | Van 4 delivery due | min from 8:00 AM | 80 | 110 | 5 | 95 |

### Costs

| Key | Control | Unit | Minimum | Maximum | Step | Default |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `electricity_price_per_kwh` | Electricity price | USD/kWh | 0.10 | 0.50 | 0.10 | 0.20 |
| `driving_cost_per_km` | Driving cost | USD/km | 0.15 | 0.60 | 0.05 | 0.30 |
| `waiting_cost_per_hour` | Charger waiting cost | USD/hour | 0 | 50 | 5 | 25 |
| `late_delivery_cost_per_hour` | Late delivery penalty | USD/hour | 0 | 120 | 5 | 60 |

## Objective and verification

- Minimize total cost in USD. Charge for all driven kilometers, including return trips, and the energy actually delivered by the charger. Count waiting from charger arrival to slot start and lateness from the due time to delivery completion. Multiply each hourly rate by its elapsed minutes divided by 60.
- Generate feasible route options classically and use QAOA to choose a schedule. Use one binary variable per route option, at most 12 selection variables, and report the actual circuit width including any auxiliary qubits. Derive the QUBO constraint penalties from the selected settings and feasible schedule cost bound so invalid assignments cannot win. Keep the circuit evaluations within the worker's time budget.
- Independently verify the decoded schedule, its battery trajectory, charger reservations, delivery completions, return times, and cost using the same effective settings. Enumerate the same route options to obtain an exact classical reference. Equivalent schedules may tie. Report the effective values, each cost component, and any gap to the reference.

## Results to visualize

Show all four vans on the street grid with timed driving, waiting, charging, delivery, and return events. Include battery levels, the selected reserve, each booked slot at both chargers, energy added, due times, delivery completion times, lateness, and total cost. Use the names **Van 1** through **Van 4** and the replay IDs `V1` through `V4`.
