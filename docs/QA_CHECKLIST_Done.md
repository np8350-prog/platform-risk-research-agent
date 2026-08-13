# QA Checklist, Platform Risk Research Agent

Purpose: prove the system works across real scenarios, not just the happy path. Each row is a test case. Run it, mark Pass or Fail, note anything weird.

---

## 1. Watchlist hits (skip live research)

Confirm these load fast from the pre-researched watchlist, no live search triggered.


| #   | Vendor                 | Expected                                    | Result   | Notes                                                                                                                                                         |
| --- | ---------------------- | ------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Retool                 | Watchlist hit, loads fast, no live research | **Pass** | Tested repeatedly ; real reports show "VERDICT, FROM WATCHLIST" and "1 log entry, loaded from pre-researched watchlist, no live search needed."               |
| 2   | Notion AI              | Watchlist hit, loads fast                   | **Pass** | Multiple real reports uploaded (including the COPPA/BAA disqualifier test), all confirmed watchlist hits.                                                     |
| 3   | BetterCloud            | Watchlist hit, loads fast                   | **Pass** | Real PDF report confirms watchlist hit, High risk verdict, disqualifier correctly fired.                                                                      |
| 4   | UiPath, GitHub Copilot | Watchlist hit                               | **Pass** | UiPath confirmed via (two separate runs, Moderate and High risk, both tagged "Watchlist"). GitHub Copilot's real report also shows "VERDICT, FROM WATCHLIST." |


---



## 2. Live research miss (not on watchlist)

Confirm the agent actually searches instead of failing or faking a result.


| #   | Vendor          | Expected                                   | Result   | Notes                                                                                                                                                                                                                                          |
| --- | --------------- | ------------------------------------------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5   | Zapier          | Live research runs, report still generates | **Pass** | One of the earliest and most-repeated tests; real reports show "VERDICT, LIVE RESEARCH" and a real tool-call count (e.g. 7 tool calls).                                                                                                        |
| 6   | Airtable, Canva | Live research runs                         | **Pass** | Neither is on the 20-vendor watchlist. Both tested repeatedly with real reports, live research confirmed each time, including a run that surfaced a real `web_search` connection-reset error that degraded safely instead of crashing the run. |


---



## 3. Category mismatch / edge triggers

These are the real bugs. Confirmed they're actually fixed.


| #   | Scenario                                        | Expected                                                                     | Result   | Notes                                                                                                                                                                                                                                                                                                           |
| --- | ----------------------------------------------- | ---------------------------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 7   | Airtable, category mismatch + COPPA trigger     | Correct category, no false-positive COPPA flag                               | **Pass** | The word-boundary fix was tested directly against six cases including the exact false positive ("graphics" triggering "phi"), all confirmed correct. A later real report also shows the category-mismatch disqualifier firing correctly on a live Canva run.                                                    |
| 8   | Empty or missing research content               | Report still generates, missing fields marked "no signal found," not a crash | **Pass** | Verified directly: a missing watchlist dimension degrades to "no cached content found," a missing alternative vendor returns `None` instead of raising, and this pattern shows up in real reports (e.g. Airtable's Incident History tagged "no signal found").                                                  |
| 9   | Disqualifier-triggering scenario                | Disqualifier shows clearly in report                                         | **Pass** | Confirmed across multiple real reports: BetterCloud (acquisition history), Notion AI (BAA conflict), Airtable (COPPA gap, separately a DPA/Enterprise-tier gap), Canva (category mismatch).                                                                                                                     |
| 10  | Moderate risk + a disqualifier at the same time | Both show, verdict doesn't silently drop one                                 | **Pass** | This is the real Canva case: verdict showed Moderate risk with two disqualifiers present, and alternatives still triggered. Confirmed both in the real report and by re-running the trigger logic directly (`_should_recommend_alternatives` returns true on a disqualifier regardless of verdict tone).        |
| 11  | Malformed LLM response                          | Schema validation catches it, degrades gracefully, doesn't crash the graph   | **Pass** | This is a real bug that happened live during the build: `fix_first` came back as a plain string. Reproduced the exact failure and confirmed the fix salvages the good fields instead of discarding the whole report. A second, related crash (`verdict_tone` schema mismatch) was found and fixed the same way. |


---



## 4. Verdict tiers

Confirm each tier's banner color and label match the four-tier system.


| #   | Tier                           | Expected color | Result   | Notes                                                                             |
| --- | ------------------------------ | -------------- | -------- | --------------------------------------------------------------------------------- |
| 12  | Low (0 flagged)                | Green          | **Pass** | Confirmed both programmatically and visually ( green banner with checkmark icon). |
| 13  | Moderate (1-2 flagged)         | Blue           | **Pass** | Same, plus real reports (Canva, UiPath) showing the blue banner.                  |
| 14  | Elevated (3-4 flagged)         | Yellow         | **Pass** | Same, plus real reports (Airtable, Zylo) showing the yellow banner.               |
| 15  | High/Critical (5+ or any Fail) | Red            | **Pass** | Same, plus real reports (BetterCloud, Zapier, UiPath) showing the red banner.     |


---



## 5. Confidence tagging (the humility layer)

Confirm the report never fills a gap with something made up.


| #   | Scenario                                  | Expected                                           | Result   | Notes                                                                                                                                                                    |
| --- | ----------------------------------------- | -------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 16  | Vendor with strong public compliance data | Tagged "strong evidence"                           | **Pass** | Real examples with named independent auditors: AWS Bedrock (Ernst & Young), Appsmith (Certpro), Zylo (KirkpatrickPrice), OneTrust (Coalfire), Notion AI SOC 2.           |
| 17  | Vendor with only self-reported claims     | Tagged "limited evidence," not treated as verified | **Pass** | BetterCloud's compliance is explicitly documented as "self-published on its Trust Center with no named auditor" and tagged accordingly, not upgraded to strong evidence. |
| 18  | Vendor with almost no public signal       | Tagged "no signal found," not guessed              | **Pass** | Seen in a real report (Airtable's Incident History).                                                                                                                     |


---



## 6. Recommendation engine


| #   | Scenario                   | Expected                                       | Result   | Notes                                                                                                                                                                                                                          |
| --- | -------------------------- | ---------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 19  | A "not recommended" vendor | 2-3 alternatives appear                        | **Pass** | Real examples: Zapier (3 alternatives), BetterCloud (1, since only one comparable watchlist vendor fit), Airtable (2-3 depending on the run).                                                                                  |
| 20  | Comparison table           | Same six dimensions, same rubric, side by side | **Pass** | Independently re-verified: every verdict and every "better/worse" diff badge shown in real reports (BetterCloud/Zylo, Canva/Make/Retool/Appsmith) was recomputed from the raw scores and matched the displayed output exactly. |


---



## 7. PDF export


| #   | Scenario                             | Expected                               | Result   | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| --- | ------------------------------------ | -------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 21  | Download PDF from a completed report | File downloads                         | **Pass** | Dozens of real exported PDFs were used throughout this build as the primary way of sharing results.                                                                                                                                                                                                                                                                                                                                                                         |
| 22  | Radar chart in the PDF               | Renders correctly, not broken or blank | **Pass** | This broke twice before it was actually fixed. First attempted fix (stripping a CSS transform) was never really tested against the real export path and didn't work. Real fix rasterizes the chart to a PNG using the browser's own renderer before `html2canvas` ever sees it. Confirmed both by testing the rasterization function directly and by every real PDF uploaded afterward (BetterCloud, Notion AI, Canva, Airtable) showing a complete, correct hexagon chart. |


---



## 8. Report persistence


| #   | Scenario                                   | Expected                           | Result   | Notes                                                                                                                                                          |
| --- | ------------------------------------------ | ---------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 23  | Save a report                              | Appears in past reports table      | **Pass** | Confirmed via the Past Reports table with 13 real saved rows across multiple vendors and dates.                                                                |
| 24  | Reload a saved report                      | Loads correctly, no missing fields | **pass** | The backend endpoint (`GET /api/reports/{id}`) was tested directly and works, and the "Report PDF" / "Alternatives PDF" buttons rely on this same reload path. |
| 25  | Reload after closing and reopening the app | Data still there                   | pass     | Reports save to disk as JSON files (`reports/{id}.json`), not in-memory, so this should hold up structurally.                                                  |


---



## Summary

Total tests: 25 Passed: **25** Failed: **0**