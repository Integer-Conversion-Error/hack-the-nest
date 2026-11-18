# Fleet Lab development

From the repository root, install the frontend requirements and start `python quantum-playground/server.py`. The server listens on loopback at port 8000 by default. Set `PORT` to use a different port.

## Replay

- **Recorded backend run** starts at 35% Van 2 charge with the final GO output in `handoff.json`. Choosing 20% loads the successful test output from `test.json`. Both settings replay their own submitted schedule and histogram.
- **Exact reference** shows the offline classical schedules for the same two inputs.
- **Live backend** can recalculate every whole Van 2 starting charge from 20% to 35% for the live example. The existing map stays visible during the solve. With `QUANTUM_BACKEND_URL` set, the dashboard sends the original prompt and control changes to the external service instead.
- Click a van to highlight its route. Click the charger for bookings or a delivery for its completion time and deadline. The timeline shows drive, wait, charge, and delivery events. Click a block to seek to its start.
- Click the map to focus it. Use Space to play or pause, Left/Right to seek five minutes, Shift with an arrow to seek fifteen minutes, Home/End for the operating-window boundaries, V/C/D to cycle through vans, chargers, or deliveries, and Escape to clear selection.
- Inspect quantum circuit images in the left sidebar. Select a preview for a scrollable full-size diagram and PNG download; Escape or Close returns focus to that preview. Legacy text drawings remain available when no image is supplied. View costs in the result details or download the displayed result as JSON.

After each verified response, the browser draws the street map and animates the result using its timed events. The native playback bar, vehicle cards, and timeline share the same playhead. Playback uses the loaded schedule and makes no frame requests. The server still offers the Pygame frame endpoint for callers that need it.

## Checks

Run these from `quantum-playground` with Pygame installed in the active Python environment. Set `PYTHON` to that interpreter's path for Node if `python` resolves to a different one.

```sh
python -m unittest discover -s tests -v
node --test tests/*.cjs
```

The Python suite uses a local stub backend to exercise the live wire contract and rejection paths. It also checks reference cost, schedule continuity, and actual Pygame PNG output. The Node suite exercises typed controls, stale responses, and browser playback.

## Agent loop progress

The progress screen keeps the five-stage overview visible above the activity
history. It shows the current step, completed stages, and why a check sent work
back to an earlier stage. A return clears obsolete downstream completion states.
Completed attempts start collapsed; active attempts and revision findings start
open. Manual disclosure choices survive polling, including keyboard focus.

The optional browser check serves the real frontend assets with deterministic
progress responses. It checks desktop, 390px mobile, and 320px mobile layouts,
keyboard disclosure controls, polling, and recovery from a failed attempt:

```sh
# Requires Playwright and its Chromium browser; no model API credentials needed.
node tests/browser/pipeline-progress.cjs
```

Set `PLAYWRIGHT_MODULE` or `BROWSER_EXECUTABLE` when using an existing installation.
Set `EVIDENCE_DIR` to an absolute directory to save screenshots. See the
[before-and-after captures](evidence/agent-loop/README.md).

## Quantum/classical replay

When both verified replays are available, the maps share one clock and scrubber;
each map has its own inspection targets. Costs, feasibility, and proven-optimum
status remain factual values. Play is always explicit. Reduced-motion mode
suppresses decorative transitions, status pulses, and vehicle trails while
preserving manual playback and scrubbing.

With the dashboard running, the optional comparison browser check exercises the
actual recorded/reference routes and, when available, the built-in local solver
and verified-result cache:

```sh
DASHBOARD_URL=http://127.0.0.1:8000 node tests/browser/fleet-comparison.cjs
```

It needs Playwright with Chromium, and accepts the same browser/evidence settings
as the progress check. See the [comparison screenshots and playback recording](evidence/fleet-comparison/README.md).

The same browser check validates the left circuit sidebar, image loading,
full-size viewing, keyboard dismissal, PNG download, and image updates when
recorded controls change. Set `SKIP_VIDEO=1` for screenshot-only capture. See the
[circuit sidebar screenshots](evidence/circuit-sidebar/README.md).
