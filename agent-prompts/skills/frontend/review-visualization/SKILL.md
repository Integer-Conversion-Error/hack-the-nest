---
name: review-visualization
description: Verify a Pygame scene against approved solver results through recorded replay, live recalculation, and capture review. Use after building or repairing rendering, animations, or rerun behavior.
---

# Review the Visualization

## Capture a Known Transition

1. Replay the EV example's default and changed starting-charge reference schedules through the same rendering path used for live results. Use the bookings, delivery times, and costs from the [frontend visualization plan](../../../../frontend-visualization-plan.md).
2. Capture the scene before Apply, while the new attempt is pending, during motion, and after settling. If the route and timeline are identical on rerun, capture the settled view and verify the updated settings and status.
3. Pair captures with the result JSON, run, plan, and attempt IDs, applied settings, window size, and live or recorded provenance. For a pending capture, identify both the displayed result and the pending request.

## Compare and Route

1. Check that the pending scene shows the previous assignment and settings and that the window responds. Exercise an out-of-order completion and confirm the current attempt alone can update the scene.
2. Trace each van through the route and timed stops in the matching result. Check charger occupancy, delivery completion times, battery state, and total cost. Verify that one booking can end at the same time the next begins.
3. For a rendering defect, make one targeted repair and replay the same case. If it still fails, report the failed check and retain the last verified scene. Route an invalid backend assignment or score through `RetryFeedback` with the matching result and evidence.
4. Repeat a supported change with a live run and compare its scene to its own verified result. If a live check is blocked, report what you verified through recorded replay.
