# Fleet Lab development

From the repository root, install the frontend requirements and start `python quantum-playground/server.py`. The server listens on loopback at port 8000 by default. Set `PORT` to use a different port.

## Replay

- **Recorded backend run** starts at 35% Van 2 charge with the final GO output in `handoff.json`. Choosing 20% loads the successful test output from `test.json`. Both settings replay their own submitted schedule and histogram.
- **Exact reference** shows the offline classical schedules for the same two inputs.
- **Live backend** starts a built-in EV solve when you change Van 2's starting charge. The existing map stays visible during the solve. With `QUANTUM_BACKEND_URL` set, the dashboard sends the original prompt and control changes to the external service instead.
- Click a van to highlight its route. Click the charger for bookings or a delivery for its completion time and deadline. The timeline shows drive, wait, charge, and delivery events. Click a block to seek to its start.
- Click the map to focus it. Use Space to play or pause, Left/Right to seek five minutes, Shift with an arrow to seek fifteen minutes, Home/End for the operating-window boundaries, V/C/D to cycle through vans, chargers, or deliveries, and Escape to clear selection.
- View costs and circuit evidence in the result details or download the displayed result as JSON.

After each verified response, the browser draws the street map and animates the result using its timed events. The native playback bar, vehicle cards, and timeline share the same playhead. Playback uses the loaded schedule and makes no frame requests. The server still offers the Pygame frame endpoint for callers that need it.

## Checks

Run these from `quantum-playground` with Pygame installed in the active Python environment. Set `PYTHON` to that interpreter's path for Node if `python` resolves to a different one.

```sh
python -m unittest discover -s tests -v
node --test tests/*.cjs
```

The Python suite uses a local stub backend to exercise the live wire contract and rejection paths. It also checks reference cost, schedule continuity, and actual Pygame PNG output. The Node suite exercises typed controls, stale responses, and browser playback.
