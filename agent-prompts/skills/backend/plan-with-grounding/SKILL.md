---
name: plan-with-grounding
description: Research and write PLAN.md for a validated optimization problem, including the solver method, checks, limits, and supported user controls. Use for initial planning or a planning repair.
---

# Plan with Grounding

Start with the structured problem, user assumptions, supported builders, and current resource limits.

1. Use the host's Gemini 4 Hydrogen configuration (`gemini-4-hydrogen`) with Google Search grounding to verify the facts needed for the method. Retain returned sources for material claims. If grounding is unavailable, record that status and identify which claims require verification before the plan can proceed.
2. Choose a method the available implementation can support. Describe how decisions map to circuit variables, which steps are classical, and how hard constraints and the objective will be checked on the original problem after decoding.
3. Record acceptance criteria and a reference method. For a solver that searches only a candidate set, define the reference over the same set, inputs, units, and constraints.
4. Estimate total circuit width, including auxiliaries, and use the configured time and qubit limits. Specify what to change if the estimate exceeds the budget.
5. Select controls the builder can apply. For each, describe its label, type, supported values or range, default, and recalculation mode. Use a supported bind, rebuild, or resample path and have the backend validate the definitions. Review topology or objective changes as a new plan.

Write these decisions, assumptions, resource limits, acceptance checks, and relevant sources to `PLAN.md`. Route contradictory problem inputs to interpretation and unsupported or over-budget methods back to planning.

Read the [backend pipeline plan](../../../../backend-pipeline-plan.md) for the current builder and the [architecture plan](../../../../system-architecture-plan.md) for the handoffs. [Gemini grounding documentation](https://aistudio.google.com/docs/google-search)
