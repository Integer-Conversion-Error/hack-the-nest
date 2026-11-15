# Fleet Lab development

From the repository root, install the frontend requirements and start `python quantum-playground/server.py`. The server listens on loopback at port 8000 by default. Set `PORT` to use a different port.

## Replay

- The reference uses the committed prompt, two vans, one charger, two deliveries, and the 08:00–10:00 operating window. Van 2's starting charge is the only approved control. Apply switches between the recorded exact classical references.
- **Live backend** sends the original prompt to the configured backend. After the backend returns a verified plan, **Apply and recalculate** uses its approved controls and plan identity. While a request is pending, the visible cost, applied settings, and map describe the last accepted result.
- Click a van to highlight its route. Click the charger for bookings or a delivery for its completion time and deadline. The timeline shows drive, wait, charge, and delivery events. Click a block to seek to its start.
- Click the frame to focus it. Use Space to play or pause, Left/Right to seek five minutes, Shift with an arrow to seek fifteen minutes, Home/End for the operating-window boundaries, V/C/D to cycle through vans, chargers, or deliveries, and Escape to clear selection.
- View costs and circuit evidence in the result details or download the displayed result as JSON.

The browser asks the local server for Pygame frames using an opaque view ID, replay minute, and selected van. The frame header provides hit regions at a fixed 840 × 620 logical size. The browser requests 1×, 2×, or 3× pixels for its display. It keeps one frame request in flight and reuses a paused frame until its inputs change.

## Checks

Run these from `quantum-playground` with Pygame installed in the active Python environment. Set `PYTHON` to that interpreter's path for Node if `python` resolves to a different one.

```sh
python -m unittest discover -s tests -v
node --test tests/*.cjs
```

The Python suite uses a local stub backend to exercise the live wire contract and rejection paths. It also checks reference cost, schedule continuity, and actual Pygame PNG output. The Node suite exercises approved controls, stale responses, and playback interaction.
