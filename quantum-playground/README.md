# QuantumLoop

A local dashboard for EV fleet dispatch and interactive optimization problems.

## Run from the repository root

```sh
python -m pip install -r quantum-playground/requirements.txt
python quantum-playground/server.py
```

Open **http://127.0.0.1:8000**. Use the prompt screen to start a new run or open the recorded example. With the built-in live backend, the EV example starts Van 2 at 35% charge. Move the slider to any whole percentage from 20% to 35% to launch a fresh solve and update the map when its schedule passes verification.

## Explore optimization problems

Open **Optimization problems** in the dashboard header or visit `/problems/`.
Choose facility placement, workforce scheduling, production scheduling,
timetabling, energy scheduling, project selection, or team formation. Each view
includes editable example inputs and computes a bounded classical solution in
the browser. Switching views keeps edits for the current page session.

The facility example starts with recorded Toronto road travel times. After moving
pins, recalculate travel times to request a fresh directed matrix from the
configured routing service.

## Build a situation on the map

Select **Build scenario** to place depots, chargers, and delivery stops on the
street grid. Drag a marker, use its X/Y fields, move a focused marker with the
arrow keys, or select **Move selected** and tap a new intersection. The inspector
also provides a list for selecting objects at the same location.

Assign each vehicle a home depot and any number of stops up to ten. The solver
chooses their visit order and charging schedule, including charging between
deliveries. A situation supports up to six vehicles, six depots, six single-plug
chargers with four slots each, and sixty delivery stops. The settings inspector
includes battery, shift, speed, energy, and cost inputs. Vehicles must return to
their own depot before the shift ends; delivery due times incur lateness costs.

The browser saves your current draft, including unfinished fields. **Edit
scenario** reopens the inputs from a displayed result or a builder-created saved
run. Changing only numeric settings covered by the approved solver's controls
offers **Recalculate settings**. Moving locations, changing assignments or
entities, and settings outside those controls offer **Plan new scenario**.
Both actions check a new schedule before displaying it. A failed solve leaves
the draft available for correction.

Custom scenarios require the native planning backend configured below. The
editor remains usable without credentials; the built-in fixed-example solver
and existing external backends retain their current workflows. Longer routes
use bounded candidates, so an exact classical comparison proves optimality over
those candidates rather than all possible routes. Geographic maps and road
routing are available through the **Real map** option described below.

## Use real maps and road routes

Choose **Real map** under **Map type**. Search for an address or place, pan the
map, drag a marker, or enter latitude/longitude in the inspector. Search results
can center the map or place the selected depot, charger, or delivery. The grid
and geographic editors retain separate drafts, so switching modes preserves
your work. The geographic example starts in downtown Toronto; search can move
you to another area.

Geographic runs use driving distances and estimated travel times from OSRM,
including one-way roads and unreachable pairs, rather than grid distance or a
shared driving-speed assumption. Locations must be within 100 metres of a
driveable road and are snapped to that road for the schedule. The original
selected coordinates remain available in **Edit scenario**. Battery use and
costs use the saved road distances; both solver schedules use the same saved
travel-time matrix. Road times do not include live traffic.

The host saves the routing snapshot and selected route geometry with the run.
Reopening saved results needs no routing request. Numeric settings can reuse
the approved snapshot; moving locations starts a fresh planning run. If a
provider is unavailable, the draft stays editable. Tile/search failures leave
coordinate editing available, and routing failures never substitute straight
lines or invented travel times.

Defaults are Leaflet 1.9.4 (vendored locally), OpenStreetMap raster tiles, Photon
address search, and the OSRM driving demo. No map API key is needed. Tile views,
search queries, and route coordinates are sent to the configured providers.
The public services have no availability guarantee: the OSRM demo is for
reasonable non-commercial usage at at most one request per second, and Photon
permits reasonable usage. Server-side requests are throttled and cached; tiles
use normal browser caching and visible attribution. See the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/),
[OSRM demo policy](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server),
and [Photon terms](https://github.com/komoot/photon#demo-server).

For larger deployments, configure OSRM-compatible routing and Photon-compatible
search providers or self-host those services.
Set `QUANTUM_OSRM_URL`, `QUANTUM_GEOCODER_URL`, `QUANTUM_TILE_URL` (with
`{z}/{x}/{y}` placeholders), and `QUANTUM_TILE_ATTRIBUTION` on the server. These
must be direct HTTP(S) endpoints; browser requests cannot select backend URLs.
`QUANTUM_MAP_CACHE_DIR` selects the bounded persistent road/search cache.

The example worker uses the local QAOA simulator by default. To run it through Quantum Rings, install `QuantumRingsLib` in the same environment and set `QR_ACCOUNT` and `QR_KEY` in the repository root's ignored `.env`.

## Compare quantum and classical schedules

When a verified classical schedule is available, the dashboard shows both maps
on the same playback clock, with separate delivery counts, costs, and their gap.
The baseline identifies its method and whether optimality was proven. A feasible
classical result without that proof is labeled as a baseline, rather than an
exact optimum. Runs without a classical replay retain the single-map view.

Press **Play** to animate the schedules together, or scrub to inspect the same
instant in both maps. Each map can be inspected independently. Decorative motion
is reduced when the operating system requests reduced motion; playback remains
an explicit user action.

The checked-in example also has a standalone exact classical solver:

```sh
python -m backend.ev_dispatch --solver classical --van-2-initial-charge 22
```

It enumerates the supported route and charger-slot choices and verifies the
selected schedule independently. This classical-only command needs Python,
without the quantum simulator dependencies.

## Plan a fleet run from a prompt

Install the backend requirements in the same Python environment.

```sh
python -m pip install -r requirements.txt
```

Set `QR_ACCOUNT` and `QR_KEY` in the repository root's `.env`. Gemini is the default model provider: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), with an optional `GEMINI_MODEL` override. To use OpenRouter, set `MODEL_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, and `OPENROUTER_MODEL`. The [model provider guide](../backend/MODEL_PROVIDERS.md) covers models per stage, shared retries, and usage tracking. Restart the dashboard, enter an EV fleet problem under **Your problem**, and select **Run pipeline**. From a results or history screen, select **New problem** to return to the prompt box.

Choose a sample and select **Load sample** to fill the prompt box. Edit the text before running if you want to change the scenario.

- [Charge and energy prices](../example-user-prompts/ev-charge-and-energy-prices.md) lets you vary Van 2's starting charge, electricity price, and driving cost.
- [Waiting and late fees](../example-user-prompts/ev-waiting-and-late-delivery.md) lets you vary Van 2's starting charge, charger waiting cost, and late-delivery penalty.
- [Battery and charger limits](../example-user-prompts/ev-battery-and-charger-limits.md) lets you vary Van 2's starting charge, minimum battery reserve, and charger power.
- [Full EV dispatch scenario](../example-user-prompts/ev-fleet-dispatch-full-scenario.md) uses a 10 × 10 grid, four vans, and two chargers. Its 17 adjustable settings cover starting charges, battery and charging limits, delivery timing, and costs.
- [Six-van EV dispatch](../example-user-prompts/ev-six-van-six-controls.md) uses a 12 × 12 grid and three chargers. Its six settings cover Van 2's starting charge, reserve, charger power, service time, waiting cost, and the late-delivery penalty.

The progress view shows individual steps and findings for prompt interpretation, planning, solver build, testing, and final verification. Collapse an attempt or choose **Hide all steps** for a stage summary. When a check sends the run back, the original attempt stays in the timeline with the reason for revision. Refreshing the same tab reconnects to the run while the dashboard server is running. The dashboard opens the results as soon as the verified schedule is ready, where you can reopen the progress timeline. If the pipeline needs more details, revise the prompt using the feedback and run it again.

The settings displayed after a successful run come from the approved plan. Applying a new setting runs the solver and checks the resulting schedule before updating the map. Repeating a recently verified combination within the same live run can reuse the bounded result cache. The dashboard identifies that reuse; it does not present it as a new measurement.

Numeric sliders follow the plan's approved values or range. The live example asks the planner for a 20–35% charge range in one-point steps. Releasing the Van 2 slider runs the solver when charge is the only pending change. For grouped rates, open **Costs** and select **Apply changes** to run with all pending edits.

## Reopen a saved run

Open **Run history** and choose a workspace to see its original prompt and download the available plan, solver, and verification artifacts. **Open results** replays a verified EV schedule directly from its saved handoff. Workspaces without a compatible replay still show their prompt and available files.

By default, the CLI and dashboard use the `workspaces/` directory in their checkout. Set `QUANTUM_WORKSPACES_ROOT` to an existing workspace directory to share runs across checkouts. New runs are saved there too.

```powershell
$env:QUANTUM_WORKSPACES_ROOT = 'C:\path\to\workspaces'
python quantum-playground/server.py
```

Set `QUANTUM_BACKEND_URL` to use an external service that implements the [verified handoff contract](docs/BACKEND_CONTRACT.md).

For replay, choose **Recorded backend run** to see the [saved measurements](../example-backend-final-result), or **Exact classical reference** to see the exact schedules.

See the [development guide](docs/DEVELOPMENT.md) for playback controls and checks.
