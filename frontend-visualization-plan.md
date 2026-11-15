# Frontend Visualization and Feedback Plan

## Init

1. On **GO**, open one local Pygame window from the approved `VisualizationHandoff`.
2. Show the verified result, its effective inputs, and whether it came from a live run or recorded replay.

## Render

1. Use the `choice_catalog` view for compatible optimization problems. List each decision group's selected option and show a resource timeline when the result contains timed bookings.
2. Show the objective value, its units, and the reference and gap when available.
3. Use a dedicated view when the result has the data it needs (e.g. for the EV prompt, use `ev_dispatch` to animate verified routes on the 6 × 6 grid and show battery levels, charger bookings, delivery times, lateness, and cost). [EV prompt](example-user-prompts/ev-fleet-dispatch-optimization.md)

## Recalculate

1. Build controls from the validated definitions. Show **Apply and recalculate** when controls are available.
2. On Apply, send the requested inputs to the same solver under the approved plan with a fresh attempt ID. Keep one pending attempt and continue showing the last verified result while the worker runs.
3. Apply the whole verified result only if the run, plan, attempt, and effective inputs match the open request. On failure, show the error beside the previous result. Close attempts when they finish or time out and ignore late completions.

## Test

1. Replay exact reference results through the regular display path when available.
   - (e.g. for the EV prompt at 35%, van 1 charges 08:30–09:00 and delivers at 09:08. Van 2 delivers at 08:10 without charging. Total cost is $15.78.)
   - (e.g. for the EV prompt at 20%, van 2 charges 08:30–09:00 and delivers at 09:09. Van 1 charges 09:00–09:30 and delivers at 09:38. Total cost is $58.37.)
2. Apply a supported change in a live Quantum Rings run and compare the view to the returned result (e.g. for the EV prompt, lower van 2's starting charge from 35% to 20%).

## Capture and Review

1. Capture before Apply, while pending, during motion, and after settling. Link each frame to its result JSON, with the displayed and pending attempt IDs, input values, and source.
2. Check that the window stays responsive and that displayed actions and scores match the result (e.g. for the EV prompt, verify the routes, bookings, battery levels, and cost). Exercise a timeout and a late completion.
3. For a display defect, make one repair and replay the same case. Report any remaining mismatch. Send invalid solver data to the backend with `RetryFeedback`.

[Backend pipeline](backend-pipeline-plan.md) · [System architecture](system-architecture-plan.md)
