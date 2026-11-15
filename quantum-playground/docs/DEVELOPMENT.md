# EV Fleet — Quantum Playground

A clean localhost dashboard with the original prompt and a plain-language problem statement in a sidebar, a large interactive Pygame map, and reserved circuit drawing slots below it. Built in this Git checkout; no public hosting.

## Run in WSL

```bash
source /mnt/c/Users/azmae/OneDrive/HackTheNest/qr-gpu/bin/activate
cd /mnt/c/Users/azmae/OneDrive/HackTheNest/hack-the-nest/quantum-playground
python server.py
```

Open http://localhost:8000. If the previous server is running, stop it with Ctrl+C and restart it. Refresh the page after updating. Demo mode needs no packages or API key.

1. The EV example is prefilled and its preview automatically loads and begins playing.
2. Pause or scrub the clock. At 1×, one real second represents one simulated minute; 4× and 12× accelerate that rate.
3. Select a van card to highlight its routes. Watch battery levels, charger occupancy, and completed deliveries.
4. Hover timeline blocks for event details; click one to jump to its start.
5. Drag sliders to update automatically. The map, batteries, timeline, costs and constraint checks refresh while preserving your replay time and selected van. A short debounce combines rapid edits, and superseded responses cannot overwrite newer settings. The slider stays mounted so dragging is uninterrupted.
6. Inspect cost totals, constraints and assumptions. Export the complete run as JSON.

## Page layout

- **Original prompt:** editable input. The submitted text is also retained under **Prompt used for this plan**, so later draft edits do not erase the source of the displayed result.
- **Problem statement:** a short plain-language description returned with the plan. Demo mode labels its fixed EV statement as an example; **Use Gemini** processes a new prompt through the connected backend.
- **Fleet visualizer:** the largest area, using Pygame as the only visualizer. Click objects and use the in-frame playback controls.
- **Circuit drawings:** three waiting slots below the map. Real `circuits[].drawing` text replaces them when returned by the live backend. Demo schematics are not shown as real circuits.
- Only battery capacity, reserve and station power sliders are shown initially. Starting charges and other inputs expand on demand. Costs, schedules, assumptions, measurements and notes remain available under **Results & technical details**. Constraint issues stay visible above that section.

## Demo scope

`ev_demo.py` creates a deterministic classical preview with fixed delivery assignments, Manhattan street paths, and greedy charging reservations. It does not optimize the plan, parse prompt changes, call Gemini, or execute QAOA. The main circuit area stays empty until actual backend drawings arrive; no quantum measurement counts are fabricated.

The demo explicitly assumes: speed 20 km/h, five-minute service, two equal-size packages per van, all deadlines at 11 AM, return by noon, initially free chargers, four charging slots from 8–10 AM, and constant charging up to capacity. Speed and service assumptions are adjustable under **More settings**. Vans below 50% initial charge, or whose direct tour would breach the selected reserve, are assigned an available charging slot if one exists. Constraint checks flag infeasible previews rather than pretending the heuristic can solve every setting.

Expand **Starting battery levels** and try raising Van 1's initial charge to 80% to remove its charging detour, reducing travel speed to shift its delivery timeline, increasing charging power to change its post-charge battery, or changing electricity price to see cost differences. Delivery assignments remain fixed in the demo; price sliders change costs rather than inventing optimized routes. The change summary compares the current schedule cost with the previous displayed plan. Turn off **Update as I adjust sliders** for manual updates.

The default schedule serves all eight deliveries and passes demo reserve, capacity, charger conflict, deadline, and return checks. This does not establish global optimality or quantum advantage.

## Connect Gemini and your team's backend

```bash
source /mnt/c/Users/azmae/OneDrive/HackTheNest/qr-gpu/bin/activate
python -m pip install -r requirements.txt
read -rsp 'Gemini API key: ' GEMINI_API_KEY; echo
export GEMINI_API_KEY
export GEMINI_MODEL='gemini-2.5-flash' # Use a tool-capable model available in your account.
export QUANTUM_BACKEND_URL='http://192.168.1.50:8001'
python server.py
```

Select **Gemini + quantum backend** and generate a live plan once. Subsequent slider edits automatically request updated results after a 900 ms debounce; demo updates use 180 ms. Each live update invokes Gemini and the backend, so it has their processing latency and API usage. Switching modes cancels pending display updates. The API key stays in the server environment. `agent.py` interprets the prompt, requests the `/api/solve` tool and `/api/evaluate` tool, and explains the result. Evaluation is enforced even if the agent omits it. Clarification questions appear in the notes; edit the prompt and resubmit. Each submission starts a new agent request. On the first live request only sliders you changed are explicit overrides; live solver-returned controls are used on subsequent runs.

Give your teammates [BACKEND_CONTRACT.md](BACKEND_CONTRACT.md). It specifies real circuit drawings, timed van events, battery endpoints, evaluation, and costs. The website is not a quantum solver. The live backend and API key are not included, so live execution still needs integration testing. SDK reference: https://googleapis.github.io/python-genai/ . Feedback is stored in server memory, not automatically sent into another agent run; up to 100 runs are retained until restart.

## Built-in Pygame view

The **Pygame frames** tab renders the current run directly in the local server through `pygame_renderer.py`. Pygame 2.6.1 is installed in the existing `qr-gpu` environment. Run the server with that environment's Python as shown above. No second renderer process or desktop window is needed.

The browser sends the current run ID, replay minute, and selected van to `/api/pygame-frame`. The server draws an offscreen Pygame surface and returns a PNG. Play/pause, scrubbing, van selection, and slider-generated plans are synchronized throughout the visualizer. The tab requests frames sequentially at up to twenty frames/second during playback (subject to rendering and network latency); a paused, unchanged frame is reused. Closing the page stops frame requests.

If Pygame is missing, the tab displays setup instructions rather than waiting indefinitely. In an activated server environment, install with `python -m pip install pygame` and restart the server. The archived `archive/pygame_bridge.py` upload utility and `/api/frame` endpoint remain available for separate integrations; the built-in tab no longer depends on uploaded frames.

## Interacting inside Pygame

- Click a moving van or its battery card to select it and highlight its route. Click again to deselect.
- Click a charger to inspect its occupancy and reservations, or a delivery to inspect its assigned van, completion time and deadline. An inspection panel appears below the frame and updates during playback.
- Click the in-frame Play/Pause, Restart, -5/+5 minute, playback speed and Clear focus buttons.
- Click or drag the in-frame time bar to seek. Seeking pauses playback, and the website clock, map, timeline and battery cards follow the same time.
- Click the image to focus it, then use Space to play/pause, Left/Right to seek five minutes (Shift for fifteen), Home/End to jump to the day boundaries, V/C/D to cycle through vans/chargers/deliveries, and Escape to clear focus.
- Existing sliders still recalculate the schedule and preserve replay position. These controls explore the returned plan; they do not manually overwrite solver assignments.

The server sends hit regions alongside each PNG. Browser pointer coordinates are adjusted for image scaling and letterboxing, so clicks correspond to the displayed frame rather than assuming a fixed screen size. No desktop window or additional process is launched.

## Checks

```bash
python -m unittest discover -s tests -v
PYTHON=python3 node tests/test_replay.cjs
node tests/test_updates.cjs
node tests/test_pygame_input.cjs
PYTHON=python3 node tests/test_slider_ui.cjs
node --check web/app.js
```

Tests cover schedule costs, street travel, batteries, charger conflicts, missed and late deliveries, capacity, invalid input, local HTTP assets, replay interpolation and time boundaries. Mocked agent tests check tool ordering, slider overrides, mandatory evaluation, and clarification without backend execution. Update tests cover debouncing, serialized requests, stale-response rejection, cancellation, and failure recovery. A DOM harness checks automatic startup, slider requests, preserved controls, replay state, selection, and manual mode; it does not verify browser rendering. No browser automation or live Gemini/quantum credentials are installed in this checkout.

Real Pygame tests verify PNG encoding and dimensions, replay-dependent frames, and HTTP retrieval using the stored run ID. These were run in qr-gpu.

## Pygame visual design

The Pygame view uses a dark digital-twin map with paved streets, lane markings, crosswalks, low-profile buildings and planted blocks. Metallic EV delivery trucks have glass cabs, cargo markings, headlights and tail lights; their orientation follows the route. Selected routes and trucks use subtle illumination. Delivery and charging markers, battery cards and playback controls share the same telemetry style.

The map uses an 840 x 620 logical layout, while the browser requests 1x, 2x or 3x pixel output based on the fitted image size and screen density: 840 x 620, 1680 x 1240, or 2520 x 1860. Frames at 2x and 3x are drawn natively at that resolution instead of enlarging a small image. At 1x, the renderer still draws at 2x before downsampling for anti-aliasing. Resolution is capped at 3x to bound rendering cost. Resize and screen-density changes trigger a fresh frame even while paused. Fonts are cached across frames under the renderer lock. A warm eight-frame benchmark in qr-gpu averaged about 26 ms per frame, compared with about 200 ms before font caching. The browser requests frames sequentially at a target of up to 20 updates/sec while playing, without building a request backlog; actual throughput depends on the machine and HTTP/image decoding latency. Paused unchanged frames are reused.

All assets are drawn in code. Existing click regions, keyboard navigation and slider changes remain connected. The map is schematic: horizontal and vertical display spacing differ to fit the frame; physical distances still use the backend's 500 m blocks.

The simplified desktop layout was checked in headless Edge with the actual qr-gpu Pygame renderer. UI tests verify prompt retention, separate problem statements, waiting slots and real circuit rendering.

Frame interaction metadata remains in logical coordinates at every resolution, so truck clicks, inspection and time scrubbing use the same hit regions. High-resolution PNGs take more rendering and transfer time; playback keeps one request in flight rather than queuing frames. Resolution and HTTP tests verify all three image sizes and confirm that hit targets stay unchanged.
