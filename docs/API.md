# API Reference

## Endpoints

All endpoints are served by `backend/app.py`, `uvicorn backend.app:app
--port 8000`.

### `POST /api/report`

Runs the full agent graph and returns a completed report. Also saves it
to `reports/{id}.json` automatically.

**Request body:**
```json
{
  "vendor_name": "Retool",
  "use_case": "internal fraud dispute tooling",
  "buyer_context": "fintech company handling customer PII"
}
```
All three fields are required; an empty `vendor_name` returns `400`.

**Response body:**
```json
{
  "report": { ...see PlatformRiskReport shape below... },
  "used_cached_watchlist": true,
  "tool_calls_made": ["watchlist_check: 'retool' hit, loaded 6 cached dimensions"],
  "saved": true
}
```

A failure inside the agent graph itself (not caught by any node's own
fallback) returns `500` with the underlying error message. This should be
rare, nearly every failure mode inside the graph has its own fallback that
degrades gracefully instead of raising.

### `GET /api/reports`

Lists every saved report, newest first. Returns metadata only, not the
full report body, so the list loads without fetching every report's
content.

```json
[
  {
    "id": "...",
    "vendor_name": "Retool",
    "use_case": "internal fraud dispute tooling",
    "verdict": "Elevated risk",
    "verdict_tone": "warn",
    "used_cached_watchlist": true,
    "saved_at": "2026-08-12T10:00:00",
    "has_alternatives": true
  }
]
```

### `GET /api/reports/{id}`

Returns one saved report in full, the exact same shape `POST /api/report`
returns, plus `vendor_name`, `use_case`, and `buyer_context` at the top
level. Returns `404` if the id doesn't exist.

### `GET /api/health`

Returns `{"status": "ok"}`. Use this to confirm the backend is actually
running before debugging anything else.


## The Report Shape (`PlatformRiskReport`)

Defined in `agent/state.py`. Every field below is present on every
completed report; list fields default to empty rather than being absent.

| Field | Type | Notes |
|---|---|---|
| `id` | string | |
| `created_at` | string | ISO timestamp |
| `subject` | string | e.g. `"Retool: internal fraud dispute tooling"` |
| `summary` | string | One-line summary of the scan |
| `verdict` | string | `"Low risk"` through `"Critical risk"` |
| `verdict_tone` | `"clear" \| "moderate" \| "warn" \| "risk"` | Drives UI color |
| `evidence_review` | object | See below |
| `reality_check` | object | See below |
| `disqualifiers` | array | Empty unless a hard buyer requirement is unmet |
| `patterns` | array of 6 | The six scored dimensions, fixed order |
| `red_flags` | array | Specific findings worth flagging |
| `fix_first` | object | `what`, `with_whom`, `question` |
| `alternatives` | array | Empty unless the verdict warranted a recommendation |

### `patterns[i]` (`PatternResult`)

```json
{
  "name": "Compliance Posture",
  "score": "Pass",
  "score_value": 2,
  "reason": "...",
  "confidence": "strong evidence"
}
```
`score_value` is `0` for Fail, `1` for Risk, `2` for Pass. `confidence` is
one of `strong evidence`, `limited evidence`, `inferred`, `no signal
found`. The six dimensions always appear in this fixed order: Data
Handling Posture, Vendor Stability, Incident History, Community Signal,
Compliance Posture, Integration Risk.

### `evidence_review`

```json
{
  "provided": true,
  "repetition": "...",
  "resolution_pattern": "...",
  "volume": "...",
  "contradiction": "..."
}
```
The four detail fields are `null` if `provided` is `false` (not enough
evidence volume to assess these patterns).

### `reality_check`

```json
{
  "performed": true,
  "findings": [
    {"source": "Trust Center", "finding": "...", "url": "https://..."}
  ],
  "contradicts_stated_framing": "...",
  "note": "..."
}
```
A finding's `url` is `null` unless that exact URL was found in the actual
research notes, it is never invented.

### `disqualifiers[i]`

```json
{
  "condition": "Buyer requires a vendor unlikely to be acquired again, but this vendor has been acquired multiple times.",
  "cost": "Risk of disruption to operations, support quality, and roadmap continuity."
}
```

### `red_flags[i]`

```json
{
  "quote": "Short, specific finding",
  "explanation": "Why it matters for this buyer"
}
```

### `alternatives[i]` (`VendorAlternative`)

```json
{
  "vendor_name": "zylo",
  "verdict": "Elevated risk",
  "verdict_tone": "warn",
  "patterns": [ ...same 6-dimension shape as above... ],
  "comparison": [
    {"dimension": "Vendor Stability", "change": "better", "original_score": "Fail", "alt_score": "Pass"}
  ],
  "why_suggested": "..."
}
```
`comparison` has one entry per dimension, `change` is `"better"`,
`"worse"`, or `"same"` relative to the rejected vendor, computed
deterministically from the two score values, not written by a model.
