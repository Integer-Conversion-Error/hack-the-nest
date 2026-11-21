# Road distance and estimated driving time

Captured in Chromium on Linux in light mode. Desktop used a 1440-pixel-wide viewport and mobile used 390 pixels. Each image shows the relevant section of the running application. Before captures use the same inputs on the base of this PR.

| View | Before | With road measurements |
| --- | --- | --- |
| Fleet itinerary, desktop | [Before](fleet-before-desktop.png) | [Distance, drive time and arrival by leg](fleet-desktop.png) |
| Fleet itinerary, mobile | [Before](fleet-before-mobile.png) | [Van totals and itinerary](fleet-mobile.png) |
| Facility map, desktop | [Assignment links](facility-before-map-desktop.png) | [Return route on Toronto streets](facility-map-desktop.png) |
| Facility map, mobile | [Assignment links](facility-before-map-mobile.png) | [Return route and trip selector](facility-map-mobile.png) |
| Facility service plan, desktop | [Outbound time](facility-before-plan-desktop.png) | [Outbound and return trips](facility-plan-desktop.png) · [Daily totals](facility-totals-desktop.png) |
| Facility service plan, mobile | [Outbound time](facility-before-plan-mobile.png) | [Outbound and return trips](facility-plan-mobile.png) |

The facility scenario has one enabled hub with capacity for 50 visits per day and a ten-minute outbound driving limit. It serves 44 visits across three zones. Its map shows the selected return drive from East to Central. OSRM returned 597.0 m and 86.1 seconds outbound with 39 geometry points, and 1,002.7 m and 133.4 seconds back with 52 points. The test compared the route response, the table entry, and the displayed trip in both directions. Daily distance and time sum the two directions for each zone and multiply by its visits.

The fleet screenshots use the [recorded Toronto OSRM fixture](../../../tests/fixtures/geographic-road-replay.json). The fixture supplies a verified example schedule and real road geometry. Van 1 makes three drives totalling 3.417 km and 7.5 estimated driving minutes. The fleet replay test uses that saved data without running a new quantum job.

## Reproduce

From `quantum-playground`, start the dashboard and run the facility check against the configured driving provider. This check requires routable Toronto coordinates, and the numbers above reflect the snapshot used for these captures.

```sh
DASHBOARD_URL=http://127.0.0.1:8000 node tests/browser/facility-road-metrics.cjs
node tests/browser/geographic-maps.cjs
```

The fleet browser regression uses local deterministic fixtures. Set `PLAYWRIGHT_MODULE` and `BROWSER_EXECUTABLE` if using a separate Chromium installation. `EVIDENCE_DIR` saves facility screenshots.
