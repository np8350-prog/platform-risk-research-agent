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
    disqualifiers, red_flags, and fix_first are still placeholders,
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
        # No date on record means we can't trust it, treat as stale.
        return True
    try:
        last_updated = datetime.strptime(last_updated_str, "%Y-%m-%d")
    except ValueError:
        # Unparseable date, same reasoning, don't silently trust it.
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
                f"watchlist_check: '{vendor_slug}' not on watchlist, proceeding to live research"
            ],
        }

    try:
        with open(processed_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # A broken cache file should never crash the graph, fall through
        # to live research instead, same as any other tool failure.
        return {
            "tool_calls_made": [
                f"watchlist_check: '{vendor_slug}' cache file unreadable ({e}), proceeding to live research"
            ],
        }

    last_updated = cached.get("last_updated")
    if _is_stale(last_updated):
        return {
            "tool_calls_made": [
                f"watchlist_check: '{vendor_slug}' found but stale "
                f"(last_updated={last_updated}, staleness limit={WATCHLIST_STALENESS_DAYS}d) "
                f", proceeding to live research"
            ],
        }

    # Fresh hit: load every dimension's cached content into research_notes,
    # in the fixed order the report expects, so the synthesis node sees the
    # same shape it would from a live research loop.
    notes = []
    dimensions = cached.get("dimensions", {})
    for dimension_name in PLATFORM_RISK_DIMENSIONS:
        entry = dimensions.get(dimension_name)
        if not entry or not entry.get("content"):
            notes.append(f"[WATCHLIST CACHE: {dimension_name}] no cached content found.")
            continue
        notes.append(
            f"[WATCHLIST CACHE: {dimension_name}] "
            f"(confidence: {entry.get('confidence', 'no signal found')}, "
            f"source: {entry.get('source_type', 'unspecified')})\n"
            f"{entry['content']}"
        )

    return {
        "research_notes": notes,
        "tool_calls_made": [
            f"watchlist_check: '{vendor_slug}' hit, loaded {len(notes)} cached dimensions "
            f"(last_updated={last_updated}), skipped live search"
        ],
        "used_cached_watchlist": True,
    }


# Buyer-context signals that mean a specific compliance artifact matters,
# not just the generic SOC2/GDPR/ISO sweep compliance_search already does.
# Without this, a buyer context mentioning minors, PHI, or payment card
# data gets no dedicated research at all: the fixed COMPLIANCE_MARKERS list
# in compliance_search.py has no COPPA, BAA, or PCI-specific query, so the
# model ends up with zero evidence on the one thing that actually matters
# most for that buyer, and scores it as if the concern didn't exist.
_CONTEXT_SENSITIVITIES = [
    {
        "keywords": ("minor", "minors", "child", "children", "under 18", "underage"),
        "query_suffix": "COPPA children's privacy policy",
        "context_note": (
            "Buyer context indicates minors/children may be involved. COPPA "
            "(Children's Online Privacy Protection Act) and a specific "
            "children's-data policy are directly relevant here; general GDPR "
            "or CCPA language does not address this. If no such policy is "
            "found, that is a real gap for this buyer, not a neutral result."
        ),
    },
    {
        "keywords": ("phi", "protected health information", "patient data", "medical record", "health record"),
        "query_suffix": "HIPAA BAA business associate agreement",
        "context_note": (
            "Buyer context indicates protected health information may be "
            "involved. Whether the vendor will sign a Business Associate "
            "Agreement (BAA) is the key artifact, a general 'HIPAA compliant' "
            "claim without a BAA is not sufficient for this buyer."
        ),
    },
    {
        "keywords": ("card data", "payment card", "cardholder data", "credit card processing"),
        "query_suffix": "PCI DSS compliance level",
        "context_note": (
            "Buyer context indicates payment card data may be involved. PCI "
            "DSS level and scope is the key artifact to check for this buyer."
        ),
    },
]


def _detect_context_sensitivities(buyer_context: str, use_case: str) -> list[dict]:
    text = f"{buyer_context} {use_case}".lower()
    triggered = []
    for sensitivity in _CONTEXT_SENSITIVITIES:
        for kw in sensitivity["keywords"]:
            # Word-boundary match, not a raw substring check. A naive `kw in text`
            # matched "phi" inside "graphics" (gra-phi-cs) and produced a false
            # HIPAA trigger on a request that had nothing to do with health data.
            if re.search(r"\b" + re.escape(kw) + r"\b", text):
                triggered.append(sensitivity)
                break
    return triggered


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
      - if the buyer context signals a sensitive population (minors, PHI,
        payment cards), one extra targeted search for the specific artifact
        that actually matters for that buyer, since the fixed compliance
        marker list above doesn't cover any of these.

    Never raises. Each tool already returns [] on failure instead of
    throwing, so a dead search provider degrades to "no signal found" on
    that dimension rather than crashing the graph.
    """
    vendor = state["vendor_name"]
    tool_log = []
    notes = []

    def _format_results(results: list[dict], dimension_name: str, context_note: str = "") -> str:
        note_suffix = f"\n[CONTEXT NOTE: {context_note}]" if context_note else ""
        if not results:
            return f"[LIVE SEARCH: {dimension_name}] No results found for '{vendor}'.{note_suffix}"
        lines = [f"[LIVE SEARCH: {dimension_name}] {len(results)} results for '{vendor}':"]
        for r in results:
            snippet = (r.get("content") or "")[:400]
            lines.append(f"- {r.get('title', '(no title)')}: {snippet} ({r.get('url', '')})")
        return "\n".join(lines) + note_suffix

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

    # --- Context-triggered searches, only when the buyer context calls for them ---
    sensitivities = _detect_context_sensitivities(
        state.get("buyer_context", ""), state.get("use_case", "")
    )
    for sensitivity in sensitivities:
        query = f"{vendor} {sensitivity['query_suffix']}"
        results = web_search(query, max_results=4)
        tool_log.append(f"web_search('{query}') [context-triggered]: {len(results)} results")
        # Feeds both Compliance Posture and Data Handling Posture, since a
        # sensitivity like this genuinely touches both.
        notes.append(_format_results(results, "Compliance Posture", sensitivity["context_note"]))
        notes.append(_format_results(results, "Data Handling Posture", sensitivity["context_note"]))

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
    model's general knowledge, this is what makes the risk scoring
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
# a bad day; a low-confidence pattern is the honest response, not a crash.
_FALLBACK_PATTERN = {
    "score": "Risk",
    "score_value": 1,
    "reason": "Synthesis could not produce a scored result for this dimension "
              "(model call failed or returned unparseable output). Treat this "
              "dimension as unverified, not as a clean pass.",
    "confidence": "no signal found",
}


def _find_notes_for_dimension(research_notes: list[str], dimension_name: str) -> str:
    """research_notes entries are tagged with ': {Dimension Name}]' whether
    they came from the watchlist cache or (eventually) a live search. Pull
    out just the notes relevant to this one dimension."""
    matches = [note for note in research_notes if f": {dimension_name}]" in note]
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
        "You are grounded in an authored risk framework (below); use it to inform "
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
        "'limited evidence', never 'strong evidence'. "
        "If the research notes contain a line starting with '[CONTEXT NOTE:', that "
        "flags something specific to this buyer (e.g. minors are involved, protected "
        "health information, payment card data) that a generic compliance sweep "
        "doesn't cover. Treat it as directly relevant to your score: if the notes "
        "show no evidence addressing that specific concern, that is a real gap for "
        "this buyer, not a neutral 'no signal found', and should push the score "
        "toward Risk or Fail rather than Pass, even if general certifications "
        "(SOC2, GDPR, ISO) look fine. "
        "Do not use em dashes anywhere in your response; use a comma, period, or "
        "parentheses instead."
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
        # Models sometimes wrap JSON in markdown fences despite instructions; strip if present.
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
    """
    Graduated rule based on how many of the six dimensions are flagged, not
    just whether any are. The earlier version only checked for presence of
    a Risk/Fail score, so a vendor with 1 Risk out of 6 and a vendor with 5
    Risk out of 6 both landed on "Moderate risk", which doesn't reflect a
    meaningfully different risk picture from the buyer's side.

    Four visually distinct tones (green/blue/yellow/red in the UI):
      clear    - Low risk, nothing flagged
      moderate - a couple of dimensions worth checking, nothing severe
      warn     - roughly half or more flagged, this needs real attention
      risk     - a Fail, or nearly everything flagged; treat as a stop sign

    Two or more Fails is treated as more severe than a single Fail, since
    multiple disqualifying-style findings compound rather than just repeat,
    but both share the "risk" (red) tone; the verdict text still says
    "Critical risk" so the distinction isn't lost, just not a 5th color.
    """
    scores = [p["score"] for p in patterns]
    total = len(scores)
    fail_count = scores.count("Fail")
    risk_count = scores.count("Risk")

    if fail_count >= 2:
        return "Critical risk", "risk"
    if fail_count == 1:
        return "High risk", "risk"

    if risk_count == 0:
        return "Low risk", "clear"
    if risk_count <= max(1, total // 3):
        return "Moderate risk", "moderate"
    if risk_count <= max(1, (total * 2) // 3):
        return "Elevated risk", "warn"
    return "High risk", "risk"


def synthesis_node(state: GraphState) -> dict:
    """
    Turns research_notes (from the watchlist cache or a live search, same
    shape either way) into a scored PlatformRiskReport. One LLM call per
    dimension, each grounded in the retrieved framework_context.

    evidence_review, reality_check, disqualifiers, red_flags, and fix_first
    are filled in with honest placeholders here; they get real content in
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
        "subject": f"{state['vendor_name']}: {state['use_case']}",
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

    # Validate against the real schema before handing it back. If this
    # fails (e.g. _compute_verdict ever returns a tone the schema doesn't
    # accept yet, the exact bug that happened here once already), fall
    # back to a safe, always-valid tone rather than losing six real
    # DeepSeek-scored dimensions over one field.
    try:
        validated = PlatformRiskReport(**report_dict)
    except Exception as e:
        print(f"synthesis_node: report failed schema validation ({e}); "
              f"falling back to a safe verdict_tone")
        report_dict["verdict_tone"] = "warn"
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
- reality_check independently checks whether the vendor's own claims hold up against what was actually found. You will be given a list of "URLs found in research notes". A finding's url field must EXACTLY match one entry from that list, or be null. Never invent, guess, or slightly modify a URL, and never use a dimension name (like "Compliance Posture") as a source when a real URL from the list is available and relevant; prefer citing the real URL.
- reality_check also has to check basic fit, not just risk: does this vendor's actual product category match what the buyer says they intend to do with it? A database/workflow tool being evaluated for graphic design, or a chat app being evaluated for financial transaction processing, is a real mismatch worth surfacing even if every risk dimension looks fine. If the use case and the vendor's actual product don't line up, say so plainly in contradicts_stated_framing; don't let a category mismatch pass silently just because compliance and stability look clean.
- disqualifiers should ONLY be included if the buyer's stated use case or context creates a hard requirement the vendor demonstrably does not meet (e.g., buyer explicitly handles PHI and no BAA is available, or buyer context indicates minors are involved and research notes show no children's-privacy/COPPA policy). Look specifically for '[CONTEXT NOTE:' lines in the research notes below; these flag a buyer-specific requirement a generic compliance sweep wouldn't catch, and a real gap there is exactly the kind of thing that belongs in disqualifiers. A fundamental product-category mismatch (the vendor's actual product doesn't do what the use case describes) also belongs here, since no amount of good compliance or stability fixes that. If no such hard conflict exists, return an empty list; do not manufacture disqualifiers to seem thorough.
- red_flags must be grounded in specific findings from the research notes, not general risk commentary. Keep each quote under 15 words and phrase it in your own words rather than copying source text verbatim.
- fix_first names the single most important next step for the buyer before adopting this vendor: a specific, concrete action, not a generic "do more diligence." fix_first MUST be a JSON object with exactly the three string keys shown in the shape above (what, with_whom, question); never return it as a plain string.
- If the research notes are too thin to responsibly fill a section, say so honestly (empty list, or provided/performed=false) rather than inventing content.
- Do not use em dashes anywhere in your response, in any field; use a comma, period, or parentheses instead."""


def _truncate(text: str, max_chars: int = 800) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + " [...truncated]"


def _extract_urls(text: str) -> list[str]:
    """Pulls every URL out of a note's full, untruncated text. Truncating
    notes for prompt size was cutting off real citations that happened to
    sit past the cutoff, causing reality_check to correctly (per its own
    rules) fall back to a source with no URL, even when a real one existed
    further into the text. Extracting URLs before truncation means a
    citation's position in the note no longer determines whether it
    survives."""
    return re.findall(r'https?://[^\s)\]"\'<>]+', text)


def _coerce_evidence_review(value) -> dict:
    if isinstance(value, dict) and isinstance(value.get("provided"), bool):
        return value
    return {"provided": False, "repetition": None, "resolution_pattern": None,
             "volume": None, "contradiction": None}


def _coerce_reality_check(value) -> dict:
    if isinstance(value, dict) and isinstance(value.get("performed"), bool) and isinstance(value.get("findings"), list):
        return value
    return {"performed": False, "findings": [], "contradicts_stated_framing": None, "note": None}


def _coerce_list(value) -> list:
    return value if isinstance(value, list) else []


def _coerce_fix_first(value) -> dict:
    if isinstance(value, dict) and all(k in value for k in ("what", "with_whom", "question")):
        return value
    if isinstance(value, str) and value.strip():
        # The model sometimes returns a bare recommendation string instead
        # of the structured object. That string is still real, useful
        # content, salvage it into "what" rather than discarding a genuine
        # recommendation over a shape mismatch.
        return {
            "what": value.strip(),
            "with_whom": "Not specified by the model. Ask your vendor contact directly.",
            "question": "Not specified by the model.",
        }
    return {
        "what": "Not available. The model's response for this section could not be parsed.",
        "with_whom": "N/A",
        "question": "N/A",
    }


def finalize_report_node(state: GraphState) -> dict:
    """
    Fills in evidence_review, reality_check, disqualifiers, red_flags, and
    fix_first, the parts of the report that look across all six already-
    scored dimensions together, rather than one dimension at a time like
    synthesis_node does.

    On any failure, returns {} (no state change), so the report keeps the
    honest placeholders from synthesis_node instead of crashing or silently
    losing the six real dimension scores that already succeeded.
    """
    report = state.get("report")
    if not report:
        print("finalize_report_node: no report in state to finalize, skipping")
        return {}

    research_notes = state.get("research_notes", [])
    framework_context = state.get("framework_context", "")

    # Truncated per-note to keep this single call's prompt a reasonable
    # size; the six PatternResult reasons already carry the distilled
    # signal from each dimension.
    truncated_notes = "\n\n".join(_truncate(n) for n in research_notes)

    # Collect every URL from the FULL (untruncated) notes separately, so a
    # citation late in a long note isn't lost just because the note itself
    # got cut short above. Deduplicated, order preserved, capped so a huge
    # note list can't blow up the prompt.
    seen_urls = set()
    known_urls = []
    for note in research_notes:
        for url in _extract_urls(note):
            if url not in seen_urls:
                seen_urls.add(url)
                known_urls.append(url)
    known_urls = known_urls[:50]
    known_urls_block = "\n".join(known_urls) if known_urls else "(none found in research notes)"

    user_prompt = (
        f"Vendor: {state['vendor_name']}\n"
        f"Buyer's intended use case: {state['use_case']}\n"
        f"Buyer context: {state['buyer_context']}\n\n"
        f"--- Authored framework context ---\n{framework_context or '(none retrieved)'}\n\n"
        f"--- Scored dimensions ---\n{json.dumps(report['patterns'], indent=2)}\n\n"
        f"--- URLs found in research notes (reality_check may ONLY cite a URL from this exact list, or use null) ---\n{known_urls_block}\n\n"
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

        # Each field is validated and salvaged independently. A malformed
        # fix_first (or any other single field) should not throw away a
        # reality_check or evidence_review that parsed correctly, that
        # wastes a real API call and hides good data over an unrelated
        # shape mismatch.
        updated_report = {
            **report,
            "evidence_review": _coerce_evidence_review(parsed.get("evidence_review")),
            "reality_check": _coerce_reality_check(parsed.get("reality_check")),
            "disqualifiers": _coerce_list(parsed.get("disqualifiers")),
            "red_flags": _coerce_list(parsed.get("red_flags")),
            "fix_first": _coerce_fix_first(parsed.get("fix_first")),
        }

        # Validate before handing back; if this still fails (e.g. the
        # response wasn't valid JSON at all), fall through to the except
        # block below rather than corrupt the report.
        validated = PlatformRiskReport(**updated_report)
        return {"report": validated.model_dump()}

    except Exception as e:
        print(f"finalize_report_node failed, keeping placeholder sections: {e}")
        return {}


# ---- Recommendations: alternative vendors when the verdict is bad enough ----

# Rough category per watchlist vendor, used to pick alternatives that are
# actually comparable, not just a random 3 names off the list. Kept as a
# plain dict rather than pulling this from research data since it's a
# stable classification that doesn't change per-report.
WATCHLIST_CATEGORIES = {
    "openai": "AI model / API provider",
    "anthropic": "AI model / API provider",
    "cohere": "AI model / API provider",
    "aws-bedrock": "AI model / API provider (multi-model gateway)",
    "retool": "No-code / low-code internal tooling",
    "appsmith": "No-code / low-code internal tooling",
    "outsystems": "No-code / low-code enterprise app platform",
    "make": "No-code / low-code workflow automation",
    "salesforce-einstein": "AI features on existing SaaS (CRM)",
    "notion-ai": "AI features on existing SaaS (workspace/docs)",
    "github-copilot": "AI features on existing SaaS (developer tools)",
    "glean": "AI-native enterprise search",
    "uipath": "AI-native robotic process automation",
    "dust": "AI-native agent platform",
    "drata": "Compliance automation / GRC",
    "sprinto": "Compliance automation / GRC",
    "whistic": "Third-party risk management (TPRM)",
    "onetrust": "Privacy management / GRC",
    "zylo": "SaaS management / spend optimization",
    "bettercloud": "SaaS management / IT governance",
}


def _should_recommend_alternatives(report: dict) -> bool:
    """Alternatives only make sense when the verdict is bad enough to
    reasonably make a buyer look elsewhere: Elevated risk or worse, or any
    disqualifier present regardless of the six-dimension verdict. A clean
    or mildly-flagged report doesn't need alternatives, that would just be
    noise on a vendor that's actually fine."""
    if report.get("disqualifiers"):
        return True
    return report.get("verdict_tone") in ("warn", "risk")


def _pick_alternative_vendors(vendor_name: str, use_case: str, buyer_context: str) -> list[dict]:
    """One LLM call: given the buyer's use case and the watchlist's category
    map, pick up to 3 vendors that are actually comparable substitutes, not
    just any 3 names. Returns [] on any failure, never raises, alternatives
    are a bonus on top of the core report, not something worth crashing over."""
    vendor_slug = _normalize_vendor_name(vendor_name)
    candidates = {slug: cat for slug, cat in WATCHLIST_CATEGORIES.items() if slug != vendor_slug}

    system_prompt = (
        "You are picking 2-3 alternative vendors for a buyer whose first-choice "
        "vendor scored poorly on a risk evaluation. Respond with ONLY a JSON array, "
        "no markdown fences, no preamble, in exactly this shape: "
        '[{"slug": "exact-slug-from-the-list", "why": "one sentence on why this fits '
        "the buyer's use case\"}]. "
        "Only pick slugs that appear in the provided list, spelled exactly as given. "
        "Pick vendors in the same or an adjacent category to the rejected vendor's "
        "likely category, actually usable substitutes for the stated use case, not "
        "just any vendor. Pick at most 3, at least 1 if anything reasonably fits, "
        "empty array if genuinely nothing in the list fits. "
        "Do not use em dashes anywhere in your response; use a comma, period, or "
        "parentheses instead."
    )
    user_prompt = (
        f"Rejected vendor: {vendor_name}\n"
        f"Buyer's use case: {use_case}\n"
        f"Buyer context: {buyer_context}\n\n"
        f"Available vendors (slug: category):\n"
        + "\n".join(f"{slug}: {cat}" for slug, cat in candidates.items())
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
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        if not isinstance(parsed, list):
            return []

        valid = []
        for item in parsed:
            if isinstance(item, dict) and item.get("slug") in candidates:
                valid.append({"slug": item["slug"], "why": item.get("why", "")})
            if len(valid) >= 3:
                break
        return valid

    except Exception as e:
        print(f"_pick_alternative_vendors failed, skipping alternatives: {e}")
        return []


def _score_alternative(
    slug: str,
    use_case: str,
    buyer_context: str,
    framework_context: str,
) -> dict | None:
    """Loads a watchlist vendor's cached research and scores it against the
    SAME use case and buyer context as the rejected vendor, so the
    comparison in the report is apples-to-apples, not a generic profile.
    Returns None on failure (missing file, bad JSON) rather than raising;
    a missing alternative just doesn't show up, it doesn't break the report.

    Does NOT set why_suggested here on purpose: at this point the score
    doesn't exist yet, so any explanation generated now would be guessing
    at fit rather than grounded in an actual comparison. That gets filled
    in later, after we know how this alternative's scores actually compare
    to the rejected vendor's."""
    path = os.path.join(WATCHLIST_PROCESSED_DIR, f"{slug}.json")
    if not os.path.exists(path):
        print(f"_score_alternative: no processed data for '{slug}', skipping")
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"_score_alternative: cache unreadable for '{slug}' ({e}), skipping")
        return None

    dimensions = cached.get("dimensions", {})
    patterns = []
    for dimension_name in PLATFORM_RISK_DIMENSIONS:
        entry = dimensions.get(dimension_name, {})
        dimension_notes = entry.get("content") or "No research notes available for this dimension."
        scored = _score_dimension(
            vendor_name=cached.get("vendor", slug),
            use_case=use_case,
            buyer_context=buyer_context,
            dimension_name=dimension_name,
            dimension_notes=dimension_notes,
            framework_context=framework_context,
        )
        patterns.append({"name": dimension_name, **scored})

    verdict, verdict_tone = _compute_verdict(patterns)

    return {
        "vendor_name": cached.get("vendor", slug),
        "verdict": verdict,
        "verdict_tone": verdict_tone,
        "patterns": patterns,
    }


def _compute_pattern_diff(original_patterns: list[dict], alt_patterns: list[dict]) -> list[dict]:
    """Deterministic, dimension-by-dimension comparison between an
    alternative and the rejected vendor. This is plain arithmetic on
    score_value, not an LLM guess, so it's always correct even if the
    written explanation text drifts. This is what actually answers "why
    is this better," not a category description written before the score
    existed."""
    diff = []
    original_by_name = {p["name"]: p for p in original_patterns}
    for alt in alt_patterns:
        original = original_by_name.get(alt["name"])
        if not original:
            continue
        if alt["score_value"] > original["score_value"]:
            change = "better"
        elif alt["score_value"] < original["score_value"]:
            change = "worse"
        else:
            change = "same"
        diff.append({
            "dimension": alt["name"],
            "change": change,
            "original_score": original["score"],
            "alt_score": alt["score"],
        })
    return diff


def _generate_alternative_reasons(
    original_vendor_name: str,
    use_case: str,
    buyer_context: str,
    alternatives: list[dict],
) -> dict[str, str]:
    """One LLM call covering all alternatives at once, given each one's
    actual computed diff against the rejected vendor. The model is required
    to reference the real comparison, not write a generic vendor blurb,
    that's the whole fix for why_suggested reading like marketing copy
    instead of an actual reason. Falls back to a plain, still-accurate
    templated sentence per vendor on any failure, never raises."""

    def _fallback_reason(alt: dict) -> str:
        better = [d["dimension"] for d in alt["comparison"] if d["change"] == "better"]
        worse = [d["dimension"] for d in alt["comparison"] if d["change"] == "worse"]
        parts = []
        if better:
            parts.append(f"scores better on {', '.join(better)}")
        if worse:
            parts.append(f"scores worse on {', '.join(worse)}")
        if not parts:
            return f"{alt['vendor_name']} scores the same as {original_vendor_name} on every dimension."
        return f"Compared to {original_vendor_name}, {alt['vendor_name']} " + ", and ".join(parts) + "."

    fallback = {alt["vendor_name"]: _fallback_reason(alt) for alt in alternatives}

    system_prompt = (
        "You are explaining why each alternative vendor is a reasonable substitute "
        "for a rejected one, for a buyer deciding between them. You will be given, "
        "for each alternative, its actual dimension-by-dimension comparison against "
        "the rejected vendor (which dimensions it scores better on, worse on, or the "
        "same). Your explanation MUST reference at least one specific dimension from "
        "that comparison, using the exact dimension name given. Do not write a generic "
        "description of what the vendor does; explain the actual comparative advantage "
        "or tradeoff, and connect it to the buyer's specific use case and context where "
        "relevant. If an alternative is worse on a dimension that matters for the "
        "buyer's context, say so plainly rather than omitting it. "
        "Respond with ONLY a JSON object, no markdown fences, no preamble, mapping each "
        "vendor name (spelled exactly as given) to its explanation string: "
        '{"vendor-name": "explanation"}. '
        "Do not use em dashes anywhere in your response; use a comma, period, or "
        "parentheses instead."
    )

    alt_summaries = []
    for alt in alternatives:
        comparison_lines = [
            f"  {d['dimension']}: {d['change']} ({original_vendor_name}={d['original_score']}, {alt['vendor_name']}={d['alt_score']})"
            for d in alt["comparison"]
        ]
        alt_summaries.append(f"{alt['vendor_name']} (overall verdict: {alt['verdict']}):\n" + "\n".join(comparison_lines))

    user_prompt = (
        f"Rejected vendor: {original_vendor_name}\n"
        f"Buyer's use case: {use_case}\n"
        f"Buyer context: {buyer_context}\n\n"
        f"Alternatives and their actual comparisons:\n\n" + "\n\n".join(alt_summaries)
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
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        if not isinstance(parsed, dict):
            return fallback

        result = {}
        for alt in alternatives:
            name = alt["vendor_name"]
            reason = parsed.get(name)
            result[name] = reason if isinstance(reason, str) and reason.strip() else fallback[name]
        return result

    except Exception as e:
        print(f"_generate_alternative_reasons failed, using templated fallback: {e}")
        return fallback


def recommendation_node(state: GraphState) -> dict:
    """
    Runs after finalize_report. If the verdict is bad enough (Elevated risk
    or worse, or any disqualifier), picks 2-3 comparable watchlist vendors,
    scores each against the same use case and buyer context, computes a
    real dimension-by-dimension diff against the rejected vendor, and
    generates an explanation grounded in that diff, not a guess made before
    the score existed.

    Skipped entirely (no extra API calls) on a clean or mildly-flagged
    report, alternatives aren't useful noise on a vendor that's actually
    fine. Never raises: any failure here means the report keeps its
    already-complete core content with an empty alternatives list.
    """
    report = state.get("report")
    if not report:
        return {}

    if not _should_recommend_alternatives(report):
        return {}

    picks = _pick_alternative_vendors(
        state["vendor_name"], state["use_case"], state["buyer_context"]
    )
    if not picks:
        return {}

    framework_context = state.get("framework_context", "")
    scored_alternatives = []
    for pick in picks:
        scored = _score_alternative(
            slug=pick["slug"],
            use_case=state["use_case"],
            buyer_context=state["buyer_context"],
            framework_context=framework_context,
        )
        if scored:
            scored["comparison"] = _compute_pattern_diff(report["patterns"], scored["patterns"])
            scored_alternatives.append(scored)

    if not scored_alternatives:
        return {}

    reasons = _generate_alternative_reasons(
        original_vendor_name=state["vendor_name"],
        use_case=state["use_case"],
        buyer_context=state["buyer_context"],
        alternatives=scored_alternatives,
    )
    for alt in scored_alternatives:
        alt["why_suggested"] = reasons.get(alt["vendor_name"], "")

    try:
        updated_report = {**report, "alternatives": scored_alternatives}
        validated = PlatformRiskReport(**updated_report)
        return {"report": validated.model_dump()}
    except Exception as e:
        print(f"recommendation_node: failed to attach alternatives ({e}), keeping report without them")
        return {}
