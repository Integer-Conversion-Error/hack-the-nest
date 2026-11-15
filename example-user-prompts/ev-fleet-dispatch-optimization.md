# EV Fleet Dispatch Optimization

Find the lowest-cost charging schedule for **2 electric vans, 2 deliveries, and 1 charger**. Calculate routes classically, use QAOA to choose charging assignments, and visualize the schedule on the street grid.

## Map and Deliveries

- Use the 6 × 6 grid from (0,0) to (5,5), with two-way roads between adjacent intersections and 500 m per block.
- The depot is at (2,2). The charger is at (2,1) and has one plug.
- Van 1 has one package for (0,0), due at 9:30 AM. Van 2 has one package for (5,0), due at 9:00 AM. Both deliveries are required. Late deliveries incur a cost.
- Both vans leave the depot at exactly 8:00 AM and must return by 10:00 AM. Drive at 30 km/h and take five minutes to complete each delivery.

## Battery and Charging

- Each van has a 20 kWh battery and uses 0.30 kWh/km. Keep at least 15% charge throughout the route.
- Van 1 starts at 20%. Van 2 defaults to 35% and can start at any whole percentage from 20% to 35%, inclusive.
- The charger is available in two slots, 8:30–9:00 AM and 9:00–9:30 AM. Reserve the whole slot for one van. A 10 kW charger adds up to 5 kWh per slot, capped by available battery capacity.
- Each van can charge once before delivery or drive directly if its battery allows it. Use shortest grid paths for the resulting route.

## Cost

- Driving costs $0.30/km. Electricity costs $0.20 per kWh delivered by the charger.
- Waiting from charger arrival to the slot start costs $25/hour.
- Late delivery costs $60/hour from the due time to delivery completion.

## Solve and Visualize

- Generate feasible route and charging options for each van, including a no-charge option where feasible. Use QAOA to select one option per van and penalize overlapping charger bookings.
- Use one binary variable per feasible option, up to five selection variables for the supported inputs. Report actual circuit width, including any auxiliary qubits.
- Independently validate the selected schedule and its cost. Enumerate the same options classically to obtain the exact reference.
- Expose **Van 2 starting charge** as an integer range with minimum 20%, maximum 35%, a step of one percentage point, and a default of 35%. Recalculate the feasible options and charging schedule for each selected charge.
- Animate the selected schedule, including travel, waiting, charging, delivery, and return. Show battery levels, charger bookings, any delay, and the total cost.
