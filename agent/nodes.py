"""
Graph nodes for the platform risk research agent.

Implemented:
  - watchlist_check_node: entry node. Loads pre-researched vendor data
    from data/watchlist/processed/{vendor}.json when it exists and is
    fresh, skipping live search entirely. Falls through otherwise.
  - live_research_node: runs when the watchlist misses. Calls the three
    search tools (firmographic, compliance, web_search) to gather signal
    across all six dimensions.
  - framework_retrieval_node: pulls relevant excerpts from the authored
    framework corpus (book failure patterns + ControlGap), grounded in
    the specific vendor/use case/buyer context. Runs on both paths above.
  - synthesis_node: turns research_notes + framework_context into six
    scored PatternResult entries (one DeepSeek call per dimension) and
    assembles a PlatformRiskReport. evidence_review, reality_check,
    disqualifiers, red_flags, and fix_first are still placeholders —
    see synthesis_node's docstring for why those are scoped separately.
"""

import json
import os
import re
import uuid
from datetime import datetime

from openai import OpenAI

from agent.state import GraphState, PLATFORM_RISK_DIMENSIONS, PlatformRiskReport
from rag.retrieval import retrieve_framework_context
from tools.web_search import web_search
from tools.compliance_search import compliance_search
from tools.firmographic import firmographic_search

WATCHLIST_PROCESSED_DIR = "data/watchlist/processed"

# How old cached watchlist data can be before it's treated as untrustworthy
# and the agent falls back to a live search instead. Tune this based on how
# fast-moving vendor risk signal actually is for your use case.
WATCHLIST_STALENESS_DAYS = 30


def _normalize_vendor_name(name: str) -> str:
    """Turns 'AWS Bedrock' / 'GitHub Copilot' into 'aws-bedrock' / 'github-copilot',
    matching the folder naming convention used under data/watchlist/raw/."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


def _is_stale(last_updated_str: str | None, max_days: int = WATCHLIST_STALENESS_DAYS) -> bool:
    if not last_updated_str:
        # No date on record means we can't trust it — treat as stale.
        return True
    try:
        last_updated = datetime.strptime(last_updated_str, "%Y-%m-%d")
    except ValueError:
        # Unparseable date — same reasoning, don't silently trust it.
        return True
    return (datetime.utcnow() - last_updated).days > max_days


def watchlist_check_node(state: GraphState) -> dict:
    """
    Entry-point node. Returns a partial state update:
      - On a fresh watchlist hit: research_notes (loaded from cache),
        tool_calls_made (a log entry, not an actual tool call), and
        used_cached_watchlist=True so the graph can route around the
        live research loop.
      - On a miss or stale hit: only a tool_calls_made log entry
        explaining why, so the graph proceeds to live research as normal.
    """
    vendor_slug = _normalize_vendor_name(state["vendor_name"])
    processed_path = os.path.join(WATCHLIST_PROCESSED_DIR, f"{vendor_slug}.json")

    if not os.path.exists(processed_path):
        return {
            "tool_calls_made": [
                f"watchlist_check: '{vendor_slug}' not on watchlist — proceeding to live research"
            ],
        }

    try:
        with open(processed_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # A broken cache file should never crash the graph — fall through
        # to live research instead, same as any other tool failure.
        return {
            "tool_calls_made": [
                f"watchlist_check: '{vendor_slug}' cache file unreadable ({e}) — proceeding to live research"
            ],
        }

    last_updated = cached.get("last_updated")
    if _is_stale(last_updated):
        return {
            "tool_calls_made": [
                f"watchlist_check: '{vendor_slug}' found but stale "
                f"(last_updated={last_updated}, staleness limit={WATCHLIST_STALENESS_DAYS}d) "
                f"— proceeding to live research"
            ],
        }

    # Fresh hit — load every dimension's cached content into research_notes,
    # in the fixed order the report expects, so the synthesis node sees the
    # same shape it would from a live research loop.
    notes = []
    dimensions = cached.get("dimensions", {})
    for dimension_name in PLATFORM_RISK_DIMENSIONS:
        entry = dimensions.get(dimension_name)
        if not entry or not entry.get("content"):
            notes.append(f"[WATCHLIST CACHE — {dimension_name}] no cached content found.")
            continue
        notes.append(
            f"[WATCHLIST CACHE — {dimension_name}] "
            f"(confidence: {entry.get('confidence', 'no signal found')}, "
            f"source: {entry.get('source_type', 'unspecified')})\n"
            f"{entry['content']}"
        )

    return {
        "research_notes": notes,
        "tool_calls_made": [
            f"watchlist_check: '{vendor_slug}' hit — loaded {len(notes)} cached dimensions "
            f"(last_updated={last_updated}), skipped live search"
        ],
        "used_cached_watchlist": True,
    }


def live_research_node(state: GraphState) -> dict:
    """
    Runs when the vendor isn't on the watchlist (or its entry is stale).
    Gathers signal for all six dimensions using the three existing tools,
    then formats results into research_notes in the same tagged shape
    watchlist_check_node produces, so synthesis_node treats both sources
    identically.

    Coverage split, kept deliberately lean since every call here is a real
    search cost the watchlist exists to avoid:
      - firmographic_search  -> Vendor Stability   (tool already fans out
                                                      internally: funding,
                                                      founding, layoffs, size)
      - compliance_search    -> Compliance Posture (tool already fans out:
                                                      SOC2, GDPR, ISO27001,
                                                      breach disclosure)
      - one targeted web_search each for the four dimensions the two
        specialized tools don't cover: Incident History, Community Signal,
        Data Handling Posture, Integration Risk.

    Never raises. Each tool already returns [] on failure instead of
    throwing, so a dead search provider degrades to "no signal found" on
    that dimension rather than crashing the graph.
    """
    vendor = state["vendor_name"]
    tool_log = []
    notes = []

    def _format_results(results: list[dict], dimension_name: str) -> str:
        if not results:
            return f"[LIVE SEARCH — {dimension_name}] No results found for '{vendor}'."
        lines = [f"[LIVE SEARCH — {dimension_name}] {len(results)} results for '{vendor}':"]
        for r in results:
            snippet = (r.get("content") or "")[:400]
            lines.append(f"- {r.get('title', '(no title)')}: {snippet} ({r.get('url', '')})")
        return "\n".join(lines)

    # --- Vendor Stability, via the specialized firmographic tool ---
    stability_results = firmographic_search(vendor)
    tool_log.append(f"firmographic_search('{vendor}'): {len(stability_results)} results")
    notes.append(_format_results(stability_results, "Vendor Stability"))

    # --- Compliance Posture, via the specialized compliance tool ---
    compliance_results = compliance_search(vendor)
    tool_log.append(f"compliance_search('{vendor}'): {len(compliance_results)} results")
    notes.append(_format_results(compliance_results, "Compliance Posture"))

    # --- Remaining four dimensions: one targeted web_search each ---
    targeted_queries = {
        "Incident History": f"{vendor} data breach OR outage OR lawsuit OR security incident",
        "Community Signal": f"{vendor} reviews G2 OR Reddit OR Hacker News complaints",
        "Data Handling Posture": f"{vendor} data privacy policy customer data retention",
        "Integration Risk": f"{vendor} API integration data export vendor lock-in",
    }
    for dimension_name, query in targeted_queries.items():
        results = web_search(query, max_results=4)
        tool_log.append(f"web_search('{query}'): {len(results)} results")
        notes.append(_format_results(results, dimension_name))

    return {
        "research_notes": notes,
        "tool_calls_made": tool_log,
    }


def framework_retrieval_node(state: GraphState) -> dict:
    """
    Retrieves relevant excerpts from the authored framework corpus (the
    book's six failure patterns + ControlGap's four failure modes),
    grounded in this specific vendor, use case, and buyer context.

    Runs after research (cached or live) and before synthesis, so every
    dimension gets scored against the framework instead of just the
    model's general knowledge — this is what makes the risk scoring
    reflect a specific point of view instead of a generic checklist.
    """
    query = (
        f"Evaluating {state['vendor_name']} for use case: {state['use_case']}. "
        f"Buyer context: {state['buyer_context']}."
    )
    context = retrieve_framework_context(query, top_k=4)
    return {"framework_context": context}


# ---- Synthesis: turns research_notes + framework_context into scored patterns ----

DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash"

_llm: OpenAI | None = None


def _get_llm() -> OpenAI:
    global _llm
    if _llm is None:
        api_key = os.getenv("AI_GATEWAY_API_KEY")
        if not api_key:
            raise RuntimeError("AI_GATEWAY_API_KEY not set in .env")
        _llm = OpenAI(api_key=api_key, base_url="https://ai-gateway.vercel.sh/v1")
    return _llm


# Fallback used whenever the LLM call fails outright or returns something
# that doesn't parse. The graph must never crash because a model call had
# a bad day — a low-confidence pattern is the honest response, not a crash.
_FALLBACK_PATTERN = {
    "score": "Risk",
    "score_value": 1,
    "reason": "Synthesis could not produce a scored result for this dimension "
              "(model call failed or returned unparseable output). Treat this "
              "dimension as unverified, not as a clean pass.",
    "confidence": "no signal found",
}


def _find_notes_for_dimension(research_notes: list[str], dimension_name: str) -> str:
    """research_notes entries are tagged with '— {Dimension Name}]' whether
    they came from the watchlist cache or (eventually) a live search. Pull
    out just the notes relevant to this one dimension."""
    matches = [note for note in research_notes if f"— {dimension_name}]" in note]
    return "\n\n".join(matches) if matches else "No research notes available for this dimension."


def _score_dimension(
    vendor_name: str,
    use_case: str,
    buyer_context: str,
    dimension_name: str,
    dimension_notes: str,
    framework_context: str,
) -> dict:
    """One LLM call, scoped to a single dimension. Scoping it this way means
    a bad parse on one dimension doesn't take down the other five, and each
    prompt stays small enough for the model to actually engage with all of
    the research instead of skimming the back half."""
    system_prompt = (
        "You are scoring one specific risk dimension for an AI vendor evaluation. "
        "You are grounded in an authored risk framework (below) — use it to inform "
        "your judgment, not just general knowledge. "
        "Respond with ONLY a JSON object, no markdown fences, no preamble, in exactly "
        "this shape:\n"
        '{"score": "Pass" | "Risk" | "Fail", '
        '"score_value": 0 | 1 | 2 (0=Fail, 1=Risk, 2=Pass), '
        '"reason": "2-3 sentences grounded in the research notes below", '
        '"confidence": "strong evidence" | "limited evidence" | "inferred" | "no signal found"}\n\n'
        "The confidence field must reflect the actual evidence quality in the notes, "
        "not how confident you feel about your own reasoning. If the notes say a claim "
        "is a vendor's own claim with no independent confirmation, that is at most "
        "'limited evidence', never 'strong evidence'."
    )

    user_prompt = (
        f"Vendor: {vendor_name}\n"
        f"Buyer's intended use case: {use_case}\n"
        f"Buyer context: {buyer_context}\n"
        f"Dimension being scored: {dimension_name}\n\n"
        f"--- Authored framework context ---\n{framework_context or '(none retrieved)'}\n\n"
        f"--- Research notes for this dimension ---\n{dimension_notes}"
    )

    try:
        client = _get_llm()
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        # Models sometimes wrap JSON in markdown fences despite instructions — strip if present.
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        # Validate shape before trusting it.
        if (
            parsed.get("score") in ("Pass", "Risk", "Fail")
            and parsed.get("score_value") in (0, 1, 2)
            and isinstance(parsed.get("reason"), str)
            and parsed.get("confidence") in (
                "strong evidence", "limited evidence", "inferred", "no signal found"
            )
        ):
            return parsed

        print(f"synthesis: '{dimension_name}' response failed shape validation: {parsed}")
        return dict(_FALLBACK_PATTERN)

    except Exception as e:
        print(f"synthesis: '{dimension_name}' LLM call failed: {e}")
        return dict(_FALLBACK_PATTERN)


def _compute_verdict(patterns: list[dict]) -> tuple[str, str]:
    """Simple, explainable rule: worst dimension sets the verdict. A single
    Fail is enough to call the whole vendor high risk, same logic a human
    reviewer would apply — one disqualifying finding outweighs five clean ones."""
    scores = [p["score"] for p in patterns]
    if "Fail" in scores:
        return "High risk", "risk"
    if "Risk" in scores:
        return "Moderate risk", "warn"
    return "Low risk", "clear"


def synthesis_node(state: GraphState) -> dict:
    """
    Turns research_notes (from the watchlist cache or a live search — same
    shape either way) into a scored PlatformRiskReport. One LLM call per
    dimension, each grounded in the retrieved framework_context.

    evidence_review, reality_check, disqualifiers, red_flags, and fix_first
    are filled in with honest placeholders here — they get real content in
    finalize_report_node, which runs next and looks across all six scored
    dimensions at once rather than one at a time.
    """
    research_notes = state.get("research_notes", [])
    framework_context = state.get("framework_context", "")

    patterns = []
    for dimension_name in PLATFORM_RISK_DIMENSIONS:
        dimension_notes = _find_notes_for_dimension(research_notes, dimension_name)
        scored = _score_dimension(
            vendor_name=state["vendor_name"],
            use_case=state["use_case"],
            buyer_context=state["buyer_context"],
            dimension_name=dimension_name,
            dimension_notes=dimension_notes,
            framework_context=framework_context,
        )
        patterns.append({"name": dimension_name, **scored})

    verdict, verdict_tone = _compute_verdict(patterns)

    report_dict = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.utcnow().isoformat(),
        "subject": f"{state['vendor_name']} — {state['use_case']}",
        "summary": f"Automated risk scan across {len(patterns)} dimensions. "
                   f"Overall verdict: {verdict}.",
        "verdict": verdict,
        "verdict_tone": verdict_tone,
        "evidence_review": {"provided": False},
        "reality_check": {"performed": False},
        "disqualifiers": [],
        "patterns": patterns,
        "red_flags": [],
        "fix_first": {
            "what": "Not yet implemented",
            "with_whom": "Not yet implemented",
            "question": "Not yet implemented",
        },
    }

    # Validate against the real schema before handing it back — if this
    # fails, we want to know now, not when someone tries to render the report.
    validated = PlatformRiskReport(**report_dict)

    return {
        "report": validated.model_dump(),
        "ready_to_report": True,
    }


# ---- Finalize: evidence review, reality check, disqualifiers, red flags, fix-first ----

_FINALIZE_SYSTEM_PROMPT = """You are producing the closing sections of an AI vendor risk report, building on six already-scored dimensions. Respond with ONLY a JSON object, no markdown fences, no preamble, in exactly this shape:

{
  "evidence_review": {
    "provided": true|false,
    "repetition": "string or null",
    "resolution_pattern": "string or null",
    "volume": "string or null",
    "contradiction": "string or null"
  },
  "reality_check": {
    "performed": true|false,
    "findings": [{"source": "string", "finding": "string", "url": "string or null"}],
    "contradicts_stated_framing": "string or null",
    "note": "string or null"
  },
  "disqualifiers": [{"condition": "string", "cost": "string"}],
  "red_flags": [{"quote": "string", "explanation": "string"}],
  "fix_first": {"what": "string", "with_whom": "string", "question": "string"}
}

Rules:
- evidence_review looks ACROSS all six dimensions' research, not one at a time: is evidence repeated verbatim across sources without new substantiation (repetition)? When issues are raised, is there a documented pattern for how they get resolved (resolution_pattern)? Is there a meaningful volume of evidence, or is most of it thin (volume)? Do any sources contradict each other (contradiction)? If notes are too thin to assess this, set provided=false and leave the rest null.
- reality_check independently checks whether the vendor's own claims hold up against what was actually found. Only include a URL in a finding if that exact URL literally appears in the research notes below — never invent or guess a URL. If you can't verify a URL, set it to null.
- disqualifiers should ONLY be included if the buyer's stated use case or context creates a hard requirement the vendor demonstrably does not meet (e.g., buyer explicitly handles PHI and no BAA is available). If no such hard conflict exists, return an empty list — do not manufacture disqualifiers to seem thorough.
- red_flags must be grounded in specific findings from the research notes, not general risk commentary. Keep each quote under 15 words and phrase it in your own words rather than copying source text verbatim.
- fix_first names the single most important next step for the buyer before adopting this vendor — a specific, concrete action, not a generic "do more diligence."
- If the research notes are too thin to responsibly fill a section, say so honestly (empty list, or provided/performed=false) rather than inventing content."""


def _truncate(text: str, max_chars: int = 800) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + " [...truncated]"


def finalize_report_node(state: GraphState) -> dict:
    """
    Fills in evidence_review, reality_check, disqualifiers, red_flags, and
    fix_first — the parts of the report that look across all six already-
    scored dimensions together, rather than one dimension at a time like
    synthesis_node does.

    On any failure, returns {} (no state change), so the report keeps the
    honest placeholders from synthesis_node instead of crashing or silently
    losing the six real dimension scores that already succeeded.
    """
    report = state.get("report")
    if not report:
        print("finalize_report_node: no report in state to finalize — skipping")
        return {}

    research_notes = state.get("research_notes", [])
    framework_context = state.get("framework_context", "")

    # Truncated per-note to keep this single call's prompt a reasonable
    # size — the six PatternResult reasons already carry the distilled
    # signal; the truncated raw notes are here mainly so reality_check
    # can point to real URLs instead of paraphrasing without a source.
    truncated_notes = "\n\n".join(_truncate(n) for n in research_notes)

    user_prompt = (
        f"Vendor: {state['vendor_name']}\n"
        f"Buyer's intended use case: {state['use_case']}\n"
        f"Buyer context: {state['buyer_context']}\n\n"
        f"--- Authored framework context ---\n{framework_context or '(none retrieved)'}\n\n"
        f"--- Scored dimensions ---\n{json.dumps(report['patterns'], indent=2)}\n\n"
        f"--- Research notes (truncated per dimension) ---\n{truncated_notes}"
    )

    try:
        client = _get_llm()
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": _FINALIZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        updated_report = {
            **report,
            "evidence_review": parsed["evidence_review"],
            "reality_check": parsed["reality_check"],
            "disqualifiers": parsed["disqualifiers"],
            "red_flags": parsed["red_flags"],
            "fix_first": parsed["fix_first"],
        }

        # Validate before handing back — a malformed field here should
        # fall through to the except block below, not corrupt the report.
        validated = PlatformRiskReport(**updated_report)
        return {"report": validated.model_dump()}

    except Exception as e:
        print(f"finalize_report_node failed, keeping placeholder sections: {e}")
        return {}
