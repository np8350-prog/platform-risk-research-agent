"""
API layer so the UI can trigger the agent, persist its reports, and
reload them later.

Endpoints:
  POST /api/report          run the agent, save the result to reports/
  GET  /api/reports         list saved reports (metadata only, newest first)
  GET  /api/reports/{id}    fetch one saved report in full
  GET  /api/health          liveness check

Every completed report gets written to reports/{id}.json. This is the
same reports/ folder the project structure already set aside for sample
reports — the UI just keeps it filled automatically instead of someone
saving files there by hand.

Run with:
    uvicorn backend.app:app --reload --port 8000

Then open ui/index.html in a browser (it calls this on localhost:8000).
"""

import json
import os
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.graph import build_graph

app = FastAPI(title="Platform Risk Research Agent API")

# Wide open for local dev — the UI is a static HTML file opened directly
# in a browser, not served from this same origin, so CORS has to allow it.
# Tighten this before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_graph = build_graph()

REPORTS_DIR = "reports"


class ReportRequest(BaseModel):
    vendor_name: str
    use_case: str
    buyer_context: str


def _save_report(vendor_name: str, use_case: str, buyer_context: str, payload: dict) -> str:
    """Writes the full API response to reports/{id}.json, keyed by the
    report's own id (already generated in synthesis_node), so a saved
    file's filename always matches the id the UI links to."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_id = payload["report"]["id"]

    record = {
        "id": report_id,
        "vendor_name": vendor_name,
        "use_case": use_case,
        "buyer_context": buyer_context,
        "saved_at": datetime.utcnow().isoformat(),
        **payload,
    }

    path = os.path.join(REPORTS_DIR, f"{report_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return report_id


@app.post("/api/report")
def create_report(req: ReportRequest):
    if not req.vendor_name.strip():
        raise HTTPException(status_code=400, detail="vendor_name is required")

    vendor_name = req.vendor_name.strip()
    use_case = req.use_case.strip()
    buyer_context = req.buyer_context.strip()

    initial_state = {
        "vendor_name": vendor_name,
        "use_case": use_case,
        "buyer_context": buyer_context,
        "research_notes": [],
        "tool_calls_made": [],
    }

    try:
        result = _graph.invoke(initial_state)
    except Exception as e:
        # A failure here means something upstream broke in a way none of
        # the node-level fallbacks caught — surface it plainly rather than
        # returning a fake report.
        raise HTTPException(status_code=500, detail=f"Agent run failed: {e}")

    payload = {
        "report": result.get("report"),
        "used_cached_watchlist": result.get("used_cached_watchlist", False),
        "tool_calls_made": result.get("tool_calls_made", []),
    }

    try:
        report_id = _save_report(vendor_name, use_case, buyer_context, payload)
        payload["saved"] = True
        payload["report"]["id"] = report_id
    except OSError as e:
        # Saving is a nice-to-have, not the point of the run — if the disk
        # write fails, the person still gets their report back, just a
        # warning that it wasn't persisted.
        print(f"Failed to save report to {REPORTS_DIR}/: {e}")
        payload["saved"] = False

    return payload


@app.get("/api/reports")
def list_reports():
    """Metadata only — id, vendor, verdict, timestamp — so the UI can
    render a picklist without loading every report's full body."""
    if not os.path.isdir(REPORTS_DIR):
        return []

    summaries = []
    for fname in os.listdir(REPORTS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(REPORTS_DIR, fname), "r", encoding="utf-8") as f:
                record = json.load(f)
            summaries.append({
                "id": record["id"],
                "vendor_name": record.get("vendor_name", "Unknown"),
                "use_case": record.get("use_case", ""),
                "verdict": record["report"]["verdict"],
                "verdict_tone": record["report"]["verdict_tone"],
                "used_cached_watchlist": record.get("used_cached_watchlist", False),
                "saved_at": record.get("saved_at", ""),
                "has_alternatives": bool(record["report"].get("alternatives")),
            })
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"Skipping unreadable report file {fname}: {e}")
            continue

    summaries.sort(key=lambda r: r["saved_at"], reverse=True)
    return summaries


@app.get("/api/reports/{report_id}")
def get_report(report_id: str):
    path = os.path.join(REPORTS_DIR, f"{report_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Report not found")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
def health():
    return {"status": "ok"}
