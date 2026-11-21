# Describe a problem

These screenshots show Chromium on Linux in light mode. Desktop used a 1440 × 1000 viewport and mobile used 390 × 844. The review and facility captures include the full page. The before views come from the optimization library in PR #9.

| State | Desktop | Mobile |
| --- | --- | --- |
| Dashboard entry | [Before](dashboard-before-desktop.png) · [Describe a problem link](dashboard-desktop.png) | |
| Original project editor | [Before](before-desktop.png) | [Before](before-mobile.png) |
| Drafting and clarification | [Project description](describe-desktop.png) | [Facility coverage question](clarification-mobile.png) |
| Proposed project inputs and assumptions | [Review](review-desktop.png) | [Review](review-mobile.png) |
| Accepted scenario | [Facility map and infeasibility result](facility-desktop.png) | [Project selection result](accepted-mobile.png) |

The configured OpenRouter model converted the USD 50,000 example into three project records. Accepting the draft ran an exact local search across eight subsets. It selected the lab upgrade and garden for 85 points at USD 45,000. In a second live request, the model asked whether every demand zone had to be served. The follow-up answer produced a facility draft with full coverage required.

For repeatable screenshot capture, the browser replayed those recorded formulation replies. The facility editor fetched a real directed OSRM travel-time matrix for the four supplied locations. The solver demonstrated that the single hub could serve 30 of the 44 visits per day. Clearing **Require full demand coverage** produced a feasible partial plan with the same assignments.

## Repeat the browser checks

Start the dashboard and run these commands from `quantum-playground` with Playwright and Chromium available.

```sh
node tests/browser/describe-problem.cjs
node tests/browser/optimization-views.cjs
```

Set `DASHBOARD_URL` to check another running server. `PLAYWRIGHT_MODULE` and `BROWSER_EXECUTABLE` can point to an existing browser installation. The first check substitutes only the formulation replies, so it can exercise provider errors, clarification, approval and cancellation without an API key. Both checks run at 1440, 390 and 320 pixels wide.
