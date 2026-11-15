# EV Fleet Dispatch Solver Plan

Gemini 4 Hydrogen proposed a penalty-QUBO QAOA design and approved it on the second planning attempt. This build handoff summarizes that design and adds a checked penalty bound and exact reference results for the two supported inputs. [EV prompt](../example-user-prompts/ev-fleet-dispatch-optimization.md)

## Build

1. Read the workspace's `problem.json` from Init and `plan.json` from Plan. Use `plan.selected_solver` to find the corresponding entry in `plan.solver_examples`. Read its `classical_steps`, `quantum_steps`, `encoding`, `build_steps`, and `verification` before writing the problem-specific solver.
2. For each van, calculate the shortest grid path for a direct route and for each charging slot before delivery. Reject options that violate the 3 kWh battery reserve, miss the slot start, or return after 10:00 AM. Use half-open charging intervals so consecutive bookings can share an endpoint.
3. Score each option for distance, electricity delivered, charger waiting time, and delivery lateness. Use exact costs in units of 1/60 USD. Round only the displayed dollar amounts.
4. Build the QUBO below. Each feasible option has its own binary variable. A selected pair conflicts if both options reserve the same charger slot.

```text
Q(x) = sum(option_cost[i] * x[i])
     + P * sum_over_vans((sum_of_van_option_bits - 1)^2)
     + P * sum_over_conflicting_pairs(x[i] * x[j])

P = 1 + sum_over_vans(largest_option_cost_for_van)
```

The option costs are nonnegative, so an invalid assignment with one or more violations costs more in the QUBO than any feasible assignment. This encoding uses five decision qubits at 35% and four at 20%, with zero auxiliary qubits.

5. Convert the QUBO to a gate-based QAOA circuit using `x = (1 - Z) / 2`. Bind the cost and mixer angles and sample through Quantum Rings. Start with a bounded angle search and record the submitted width, angles, shot count, and measured outcomes. The repository's smoke script shows the provider and execution calls. [Quantum Rings smoke script](../first_circuit.py) · [Quantum Rings QAOA guide](https://www.quantumrings.com/doc/maxcut.html)

## Verify

1. Decode all observed bitstrings using the recorded qubit-to-option mapping. Filter for one option per van and non-overlapping bookings. Recompute route timing, energy, and cost independently, then choose the lowest-cost feasible measured assignment. Report a failure when sampling yields no feasible assignment. Keep the exact reference and measured result separate.
2. Enumerate the same option combinations to check the exact reference. For this fixture also enumerate the binary encoding and verify that its minimum corresponds to a feasible schedule with the exact cost.

| Van 2 starting charge | Feasible options by van | Exact bookings | Exact cost |
| --- | --- | --- | --- |
| 35% | 2 and 3 | Van 1 charges 08:30–09:00. Van 2 drives directly. | 947 units ($15.78) |
| 20% | 2 and 2 | Van 2 charges 08:30–09:00. Van 1 charges 09:00–09:30. | 3502 units ($58.37) |

## Recalculate

Expose `van_2_initial_charge_pct` with allowed values 35 and 20, default 35. Rebuild the feasible options, circuit, and reference after Apply. Return the selected timed route for each van, battery states, charger bookings, and cost so the frontend can animate the schedule.

The planning run and classical reference calculations were verified. Build still needs to execute the circuit and check the measured results on Quantum Rings.
