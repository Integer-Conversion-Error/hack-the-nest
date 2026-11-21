# Optimization problem visualizations

Captured in Chromium on Linux, in light mode. Desktop uses a 1440 × 1000 viewport and mobile uses 390 × 844. Captures extend vertically to include the full visualization. The first row shows the dashboard before the problem library was added. Each other row shows a running interactive view with its example inputs and computed classical result.

| View | Desktop | Mobile |
| --- | --- | --- |
| Previous fleet dashboard | [Before](before-desktop.png) | [Before](before-mobile.png) |
| Facility placement and demand coverage | [Map](facility-desktop.png) | [Map](facility-mobile.png) |
| Employee shift assignments | [Roster](workforce-desktop.png) | [Roster](workforce-mobile.png) |
| Job sequences on shared machines | [Gantt chart](production-desktop.png) | [Gantt chart](production-mobile.png) |
| Classes, rooms and time slots | [Timetable](timetable-desktop.png) | [Timetable](timetable-mobile.png) |
| Electricity and battery dispatch | [Energy charts](energy-desktop.png) | [Energy charts](energy-mobile.png) |
| Funded and deferred projects | [Project comparison](projects-desktop.png) | [Project comparison](projects-mobile.png) |
| Team assignments and skills | [Team comparison](teams-desktop.png) | [Team comparison](teams-mobile.png) |

In a separate [ten-candidate Toronto run](facility-ten-sites-desktop.png), we set a ten-minute driving limit, an opening budget of CAD 200 per day, and a maximum of three hubs with capacity for 18 visits per hub. After fetching actual OSRM times, the solver opened three hubs at CAD 150 per day and served all 44 visits.

With the dashboard running, execute `node tests/browser/optimization-views.cjs` from `quantum-playground`. Set `DASHBOARD_URL` if the server is not at `http://127.0.0.1:8000`. Use `EVIDENCE_DIR=docs/evidence/optimization-views` to capture the current views. `PLAYWRIGHT_MODULE` and `BROWSER_EXECUTABLE` can identify an external Playwright and Chromium installation. The browser check edits all seven problems at 1440, 390 and 320 pixels wide and exercises navigation between the library and fleet dashboard.
