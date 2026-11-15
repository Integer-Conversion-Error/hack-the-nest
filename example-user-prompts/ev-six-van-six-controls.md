# EV Fleet Dispatch with Six Vans and Six Adjustable Settings

Find the lowest-cost dispatch for six electric vans and three chargers. Use QAOA to choose the routes and charging reservations, then visualize the verified schedule. Let me vary six practical settings and see how they affect routes, bookings, and costs.

## Street grid and deliveries

- Use a 12 × 12 grid of intersections from (0,0) to (11,11), with streets in both directions between adjacent intersections and 500 m per block. The depot is at (5,5).
- The single-plug chargers are C1 at (5,4), C2 at (6,5), and C3 at (5,6). Vans 1 and 2 use C1 if charging, Vans 3 and 4 use C2, and Vans 5 and 6 use C3.
- Every van leaves the depot at exactly 8:00 AM and returns by 10:00 AM. All six deliveries are required. A delivery can finish after its due time and incur a late penalty. Each van drives at 30 km/h and follows the shortest path between stops, taking the horizontal segments first when there is a tie.

| Van | Delivery | Intersection | Due time |
| --- | --- | --- | --- |
| V1 | D1 | (2,2) | 9:25 AM |
| V2 | D2 | (8,2) | 8:55 AM |
| V3 | D3 | (8,8) | 9:10 AM |
| V4 | D4 | (10,5) | 9:28 AM |
| V5 | D5 | (4,9) | 9:05 AM |
| V6 | D6 | (1,7) | 9:26 AM |

## Batteries and chargers

- Each van has a 20 kWh battery and consumes 0.30 kWh per kilometer. All vans except Van 2 start at 20% charge. Use Van 2's selected starting charge. The battery must stay at or above the selected reserve from departure through the return to the depot. Check feasibility at full precision.
- Each charger offers two independent 30-minute slots, 8:30–9:00 AM and 9:00–9:30 AM. A van that books a slot must arrive by the start, waits until it begins, and reserves its plug for the entire slot. Different chargers can serve vans simultaneously. A slot delivers the selected power multiplied by 0.5 hours, capped by the remaining battery capacity.
- Consider three route candidates for each van. It can go directly to its delivery and back, or make one stop at its assigned charger using one of the two slots before delivery. Prune candidates that violate the reserve, the slot arrival time, or the return deadline.

## Six adjustable settings

Expose these six controls in the approved plan and results view. All ranges include both endpoints. Accept all values that align with the step, and allow any combination of the settings.

| Key | Control | Unit | Minimum | Maximum | Step | Default |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `van_2_initial_charge_pct` | Van 2 starting charge | % | 20 | 35 | 1 | 26 |
| `minimum_reserve_pct` | Minimum battery reserve | % | 15 | 19 | 1 | 15 |
| `charger_power_kw` | Power per charger | kW | 8 | 14 | 2 | 10 |
| `delivery_service_minutes` | Service time per delivery | min | 4 | 8 | 1 | 5 |
| `waiting_cost_per_hour` | Charger waiting cost | USD/hour | 0 | 50 | 5 | 25 |
| `late_delivery_cost_per_hour` | Late delivery penalty | USD/hour | 0 | 120 | 5 | 60 |

## Solve and verify

- Minimize total fleet cost. Driving costs $0.30 per kilometer, including the return trip. Electricity costs $0.20 per kWh actually delivered. Apply the selected waiting rate from charger arrival to slot start. Apply the selected late rate to time elapsed after the due time up to delivery completion. Prorate hourly costs by minutes divided by 60.
- Costs are additive and each van's charging choice affects only its own station. Solve the three independent two-van groups using QAOA with one binary selector per feasible route option. Each group has at most five selectors. Enforce exactly one route per van and at most one van per charger and slot. Derive penalties from the current group cost bound and keep circuit calls within the worker's time budget.
- Decode the best feasible measured assignment for each charger group and combine those assignments into the fleet schedule. Enumerate all valid choices in each group to compute the exact classical reference for the same settings. Independently verify every route, reserve, time, reservation, and cost. Report the effective settings, cost components, total cost, and any gap to the reference.
- Preserve the measured counts, selected bitstring, shots, and circuit width for each group. Use the C1 group's actual counts, selected bitstring, and shot share for the main histogram, and identify C1 in the quantum method description. Use the largest circuit width and the total number of circuit runs in the run summary.

## Results to visualize

Show all six vans on the full street grid with timed driving, waits, charging, deliveries, and returns. Show each van's battery, reserved slots by charger, delivery due and completion times, and the total cost. Use the van IDs `V1` through `V6`, the charger IDs `C1` through `C3`, and the delivery IDs `D1` through `D6` in the replay.
