# EV Dispatch with Adjustable Energy Prices

Find the lowest-cost schedule for two electric vans sharing one charger. Explore how starting charge, electricity prices, and driving costs affect dispatch cost and charger bookings.

## Routes and deadlines

- Use a 6 × 6 street grid from (0,0) to (5,5) with two-way roads between adjacent intersections and 500 m per block. The depot is at (2,2). The charger is at (2,1) and has one plug.
- Van 1 delivers to (0,0) and is due at 9:30 AM. Van 2 delivers to (5,0) and is due at 9:00 AM. Both deliveries are required. A delivery can finish after its due time at a penalty.
- Both vans leave the depot at exactly 8:00 AM and must return by 10:00 AM. Drive at 30 km/h, take five minutes to complete each delivery, and follow the shortest grid path between stops.

## Battery and charging

- Each van has a 20 kWh battery and uses 0.30 kWh/km. Maintain at least 15% charge throughout the route. Van 1 starts at 20%. Use the selected starting charge for Van 2.
- The charger operates at 10 kW and is available from 8:30 to 9:00 AM and from 9:00 to 9:30 AM. Each slot is exclusive to one van. A slot adds up to 5 kWh, capped by remaining battery capacity. A van can drive directly when feasible or charge once before its delivery.

## Adjustable settings

Expose these controls in the results screen. All ranges include both endpoints.

- `van_2_initial_charge_pct` — **Van 2 starting charge**, 20–35% in 1 percentage point steps, default 35%.
- `electricity_price_per_kwh` — **Electricity price**, $0.10–$0.50/kWh in $0.10/kWh steps, default $0.20/kWh. Apply it to the energy actually delivered by the charger.
- `driving_cost_per_km` — **Driving cost**, $0.15–$0.60/km in $0.05/km steps, default $0.30/km. Apply it to all driving, including the trip back to the depot.

Minimize the sum of driving, charging electricity, waiting, and lateness costs in USD. Waiting from arrival at the charger until the booked slot starts costs $25/hour. Delivery completion after the due time costs $60/hour. Prorate both by elapsed minutes.

Allow every combination of the settings. For each run, calculate feasible direct and charging routes classically and use QAOA to choose one option per van with no overlapping bookings. Use one binary selection variable per feasible option, up to five, and report the total circuit width including auxiliary qubits. With the selected rates and route options, bound the cost of every feasible schedule and set each QUBO constraint penalty above that bound. Independently check the selected schedule and its cost, and calculate an exact classical reference over the same options using the same settings. Report the effective values and separate cost components.

Visualize both vans on the street grid with timed drives, waits, charging, deliveries, and returns. Show battery levels, booked slots, completion times, lateness, and total cost.
