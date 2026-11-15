# Backend Pipeline Plan

## Init

1. Create a run workspace with `backend/workspace.py`.
2. Parse the prompt into groups of eligible options, an additive objective, and conflicts. Ask when missing information affects feasibility or the score.
   - (e.g. for the EV prompt, each van's feasible routes and charger slots are its options. Options that use the same plug at the same time conflict.) [EV prompt](example-user-prompts/ev-fleet-dispatch-optimization.md)

## Plan

1. Use Gemini Hydrogen for grounded research, planning, implementation, and orchestration.
2. Support an additive objective and one eligible option per group. Express pairwise constraints as conflicts, including exclusive use of a shared resource.
3. Write `PLAN.md` with the objective, constraints, allowed controls, and qubit limit.

## Build

1. Translate eligible options and conflicts into the reusable QAOA model. Reject inputs that exceed the run's qubit budget.
2. Run the reviewed solver in a bounded Python worker. Record measured candidates and execution evidence.
3. Rebuild affected options under the same plan when the user applies a supported change (e.g. for the EV prompt, changing van 2's starting charge from 35% to 20% changes which routes are feasible).

## Test

1. Decode measured candidates. Independently verify one eligible option per group, no conflicts, and the objective value.
2. Return the best verified candidate in `RunResult`. Compare it with an exact reference when enumeration fits the budget. Report when no sample passes verification.
   - (e.g. for the EV prompt, the exact reference cost is $15.78 at 35% and $58.37 at 20%.)

## Final Go/No-Go

1. Confirm the problem matches the prompt and the result meets the plan. On **GO**, send the verified result and allowed controls to the frontend agent.
2. On a repairable failure, return to the stage that can fix it. Default to 120 seconds per worker and 30 minutes for the initial run, including up to 10 retries after the first attempt. Stop earlier if a retry cannot make progress.
