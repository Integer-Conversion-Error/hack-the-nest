---
name: render-pygame-scene
description: Build or update a local Pygame scene from an approved visualization handoff or verified recalculation result. Use when mapping problem entities, solver assignments, and supported controls to the visible scene.
---

# Render the Pygame Scene

## Validate the Input

1. Start with the approved `VisualizationHandoff`. For an update, use the verified `RunResult` for the current attempt under that plan.
2. Match the run, plan, and attempt IDs and the effective inputs to the result being displayed. Check that every assignment refers to a known entity and has a supported action or destination.
3. Route missing or inconsistent result data to the backend with the relevant IDs and evidence. Report an unsupported scene type when there is no mapping for the approved problem.

## Map the Result to the Scene

1. Keep entity identities stable between frames and reruns. Derive each pose, destination, or action from the decoded assignment. Use animation to show how the entity carries out that assignment.
2. Show the problem, the backend-approved controls and defaults, **Apply and recalculate**, the effective settings, and the verified outcome. Label a replay as recorded.
3. For the EV example, draw the street grid, depot, charger, vans, and delivery stops. Animate each van from the verified route and timeline. Show charger bookings, delivery completion times, lateness, and cost. Use battery state values supplied in the verified result.
4. Keep the display responsive while recalculating. Use the last verified scene as the pending view. Update the assignment, score, and applied settings together when the current attempt succeeds.

For the EV reference schedules and capture cases, see the [frontend visualization plan](../../../../frontend-visualization-plan.md).
