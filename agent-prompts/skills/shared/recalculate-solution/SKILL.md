---
name: recalculate-solution
description: Recalculate an approved optimization solution after the user applies a supported control change and deliver the verified result to the active scene.
---

# Recalculate Solution

Start with the approved plan, validated control definitions, the last verified result, and the values the user applied.

1. Validate the requested fields and values against the approved controls. If the graph topology, objective form, or another input falls outside the approved plan, route the change to the backend agent for planning. Return a field-specific error for an invalid value.
2. Send a `RunRequest` under the existing run and plan with a fresh attempt ID and the applied values. Use the backend-approved recalculation mode. If an input changes which options are feasible, regenerate those options, build the circuit for the new set, and recompute the reference over the same set.
3. Keep pending values separate from the displayed result and keep the scene responsive. Show the previous verified scene with its effective values until a new result passes the backend checks.
4. Match the returned `RunResult` to the current run, plan, and attempt while the attempt is still pending. Check that the effective values match what was accepted. Apply the new assignment, score, and settings together. Animate only changed assignments.
5. Close the attempt on success, failure, or timeout. Ignore completions for superseded or closed attempts. If the current attempt fails or times out, show its error with the last verified scene. Route an invalid result to the backend agent with the attempt and failed check.

In the [EV example](../../../../example-user-prompts/ev-fleet-dispatch-optimization.md), changing van 2's starting charge from 35% to 20% keeps the approved plan and makes its direct route infeasible. Rebuild the charging options and verify the newly selected schedule before updating the scene.
