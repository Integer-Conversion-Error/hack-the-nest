# System Architecture Plan

## Init and Plan

1. Read the user prompt in the CLI and reuse `init_workspace` to save it. Ask for missing details that affect feasibility or the objective. [Workspace code](backend/workspace.py)
2. Have Gemini 4 Hydrogen research, plan, implement, and orchestrate. Save the validated method and controls in `PLAN.md`. [Agent loop](agent-loop.md)
3. Send compatible problems to the same solver. Each decision has eligible options with scores and any pairwise conflicts. Use explicit options or a reviewed adapter that can derive them. [Backend plan](backend-pipeline-plan.md)

## Run and Handoff

1. Call `run_solver` through the Python host. The host issues run, plan, and attempt IDs and runs the worker under the configured limits.
2. Verify the measured solution in Python. On final GO, pass the verified choices, controls, and scene data to the frontend agent. Open a local Pygame window. [Frontend plan](frontend-visualization-plan.md)

Use JSON v1 with the same run, plan, and attempt IDs across the handoff.

- `RunRequest` carries the plan reference, applied values, and limits.
- `RunResult` carries status, effective values, selected options, score, and live or recorded provenance. Include the reference and gap when computed, or the failure stage and message on error.
- `VisualizationHandoff` carries the problem, approved result, control definitions, and scene type.
- `RetryFeedback` carries the failed check, evidence, and remaining budget.

## Recalculate

1. Let the user apply values for a validated control. Reuse the approved method with a fresh attempt and recompute any affected options and reference. Send changes outside the plan back to planning.
2. Update the window only from a verified result for the current pending attempt. Keep the last verified result visible during execution or after a failure.

## Check the Flow

1. Run the initial solve and a supported change. Compare each live result with its exact reference when available, and check that the scene follows the selected solution (e.g. for the EV prompt, start van 2 at 35% and apply 20% to check the updated charging schedule). [EV prompt](example-user-prompts/ev-fleet-dispatch-optimization.md)
2. Force a timeout and a late completion. Verify that the error is visible and the late result leaves the active scene intact.
