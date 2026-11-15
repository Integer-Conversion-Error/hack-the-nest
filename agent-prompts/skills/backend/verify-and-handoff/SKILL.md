---
name: verify-and-handoff
description: Independently check an optimization result, decide GO or NO-GO, and send a verified VisualizationHandoff or route RetryFeedback. Use after a solver attempt or when deciding whether to retry.
---

# Verify and Hand Off a Result

Use the original prompt, structured problem, `PLAN.md`, current `RunResult`, and verification evidence from the backend.

## Evaluate

1. Check the structured problem against the user's prompt, the selected method against that problem, and the implementation against the plan. Match the attempt, circuit, effective inputs, and bit mapping before using measured results.
2. Use the independent backend checks to recompute hard-constraint feasibility and the original objective from decoded decisions. Select the best feasible measured candidate using the plan's deterministic tie-break. If measurements contain no feasible candidate, report that outcome separately from proven infeasibility.
3. When an exact reference exists, compare it on the same effective input and candidate set and report the gap. Scope any optimality claim to the reference actually verified and apply the plan's quality checks.

## Route

- On GO, send the frontend agent `VisualizationHandoff` with the original prompt, verified problem and entity IDs, scene type, approved result, and validated controls.
- Use `RetryFeedback` for a repairable failure. Include the failed check, evidence, and remaining budget. Send prompt mismatches to interpretation, method or limit problems to planning, and implementation or execution defects to build.
- Allow up to 10 retries after the initial attempt within the configured deadline. Stop earlier if the failure is non-repairable or another attempt would repeat the same failure without progress. On NO-GO, return the failed check and the actionable next step.

See the [backend pipeline plan](../../../../backend-pipeline-plan.md) for EV rehearsal checks and the [architecture plan](../../../../system-architecture-plan.md) for the handoff contents.
