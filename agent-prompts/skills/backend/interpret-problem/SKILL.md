---
name: interpret-problem
description: Translate a user's optimization request into a structured problem and resolve missing information that affects feasibility or the objective. Use for a new request or for interpretation repairs.
---

# Interpret a Problem

Use the original prompt, any answers the user has supplied, and the host's supported problem types. Preserve the prompt as evidence for the final GO checks.

## Extract and Check

1. Identify the entities and their stable IDs, decision variables, input values and units, objective terms and direction, and hard constraints. Distinguish a hard cutoff from a late penalty in the objective.
2. Reconcile units, ranges, and derived quantities before choosing a method. For schedules, distinguish the operating window from explicit availability for each resource.
3. Ask one focused question when an omitted or conflicting value changes feasibility, the objective, or what must be delivered. Use the CLI clarification path when available. Record minor defaults as assumptions for `PLAN.md`.
4. Match the structured problem against the capabilities supplied by the host. Report an unsupported problem with the missing capability when the available implementations cannot represent it.

## Return

- Return the structured problem and its assumptions when the inputs are sufficient. Preserve links between user terms and internal entities so the solver and the scene can use the same IDs.
- If clarification is needed, return the question and the affected objective or constraint. Resume interpretation with the user's answer.

For the dispatch example's distinction between due times and the hard shift cutoff, see the [EV example prompt](../../../../example-user-prompts/ev-fleet-dispatch-optimization.md).
