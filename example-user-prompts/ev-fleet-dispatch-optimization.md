### EV Fleet Dispatch Optimization

Optimize delivery routes and charging schedules for **4 electric vans, 8 deliveries, and 2 charging stations**, minimizing operating costs and delays.

### Map

- 6 × 6 street grid (36 intersections), 500 m per block.
- Two-way roads; coordinates range from (0,0) to (5,5).
- Depot: (2,2)
- Chargers: (2,1), (5,4)
- Deliveries: (0,0), (1,4), (2,5), (3,0), (4,2), (5,0), (5,5), (3,4)
- All vans start at the depot.

### Default Parameters

- Battery: 20 kWh per van
- Initial charge: 20%, 35%, 45%, 60%
- Consumption: 0.30 kWh/km
- Charging power: 10 kW per station
- Electricity: $0.20/kWh
- Operating hours: 8 AM–12 PM
- Minimum battery: 15%
- Charging intervals: 30 minutes

### Optimization Objectives

Minimize:

- Driving: $0.30/km
- Electricity: $0.20/kWh
- Waiting: $25/hour
- Late deliveries: $60/hour
- Undelivered packages: $150/package

**Constraints:** Maintain battery reserves, prevent charger conflicts, respect vehicle capacity and delivery deadlines.

### Quantum Optimization

- Routes calculated classically.
- QAOA optimizes charging assignments.
- 4 vans × 2 chargers × 4 time slots = **32 binary variables (basic 32-qubit formulation)**.
- Target: Under 80 qubits, including any auxiliary variables.

**Goal:** Find the lowest-cost feasible dispatch and charging schedule, visualized on the street grid.
