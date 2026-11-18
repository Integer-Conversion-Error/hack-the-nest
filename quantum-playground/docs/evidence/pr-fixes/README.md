# PR review fixes

Linux Chromium, light mode. These captures use the running dashboard and real
HTTP pipeline-progress endpoints with fixture provider credentials and a stubbed
planner. No model API calls were made and no real credentials appear in the
images. The fixture deliberately stops after reporting its progress detail.

| State | Before | After |
| --- | --- | --- |
| Desktop, 1360 × 950, expanded failed attempt | [Unmasked OpenRouter fixture key](before-desktop.png) | [Both fixture keys redacted](after-desktop.png) |
| Mobile, 390 × 844, expanded failed attempt | — | [Both fixture keys redacted](after-mobile.png) |

The HTTP regression `test_pipeline_input_failure_remains_visible_in_its_stage`
verifies that both Gemini and OpenRouter credential values are redacted while
the original failed stage and clarification message remain available.

Two additional backend fixes have no distinct visual state. Circuit-image
tests verify that malformed optional `test.json` metadata omits the image while
the saved result and history remain available. Recalculation-integrity tests
verify that source/data changes during worker execution or semantic review
reject the result and preserve the original workspace; unchanged inputs still
produce a verified result. These regression results are the direct evidence
for those fixes.
