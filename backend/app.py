"""
API layer so the UI can trigger the agent, persist its reports, and
reload them later.

Endpoints:
  POST /api/report          run the agent and return the result
  GET  /api/reports         list locally saved reports (local dev only)
  GET  /api/reports/{id}    fetch one locally saved report (local dev only)
  GET  /api/health          liveness check

Groundwork (the production caller) stores its own copy of every report in
Vercel Blob with 12-month retention and never reads reports/. So by
default nothing is written here. Set SAVE_LOCAL_REPORTS=true on a local
machine to keep the old behaviour for the standalone ui/index.html
(Past Reports and PDF export); files older than 12 months are deleted on
startup and on every save. It is ignored on Railway.

LangSmith/LangChain tracing is off unless ENABLE_LANGSMITH_TRACING=true is
set on a local machine. On Railway it is always off, whatever else is set.

Run with:
    uvicorn backend.app:app --reload --port 8000

Then open ui/index.html in a browser (it calls this on localhost:8000).
"""

import json
import os
import time
import uuid
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))

# Must run before LangGraph/LangChain are imported: they read these at import.
if ON_RAILWAY or os.getenv("ENABLE_LANGSMITH_TRACING") != "true":
    for _key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        os.environ[_key] = "false"

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
SAVE_LOCAL_REPORTS = not ON_RAILWAY and os.getenv("SAVE_LOCAL_REPORTS") == "true"
RETENTION_SECONDS = 365 * 24 * 60 * 60  # 12 months


def _purge_old_reports() -> None:
    """Deletes saved report files older than 12 months. Only .json files:
    the sample PDFs in reports/ are not user data."""
    if not os.path.isdir(REPORTS_DIR):
        return
    cutoff = time.time() - RETENTION_SECONDS
    for fname in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, fname)
        if fname.endswith(".json") and os.path.getmtime(path) < cutoff:
            try:
                os.remove(path)
            except OSError as e:
                print(f"Could not delete expired report {fname}: {e}")


_purge_old_reports()


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

    if not SAVE_LOCAL_REPORTS:
        payload["saved"] = False
        return payload

    try:
        _purge_old_reports()
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
