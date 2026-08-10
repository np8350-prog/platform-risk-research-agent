"""
Pydantic models defining the Platform Risk report shape.

This schema is intentionally modeled on Groundwork's own diagnostic report
format (verdict, disqualifiers, 6 scored dimensions, red flags, fix-first).
Every Groundwork diagnostic returns this same shape. Matching it here means
this agent's output could slot into Groundwork later with no rework.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field
from typing import TypedDict, Annotated
import operator


class GraphState(TypedDict):
    # ---- Input, set once at the start ----
    vendor_name: str
    use_case: str            # what the buyer wants to use this platform for
    buyer_context: str        # e.g. "fintech company handling customer PII"

    # ---- Accumulated during the ReAct loop ----
    # operator.add means each node APPENDS to this list instead of
    # overwriting it, so research findings pile up across tool calls.
    research_notes: Annotated[list[str], operator.add]
    tool_calls_made: Annotated[list[str], operator.add]

    # ---- Set once retrieval (RAG) has run ----
    framework_context: str    # relevant excerpts from your book / ControlGap

    # ---- Set by watchlist_check_node, read by the graph's routing edge ----
    # True only on a fresh watchlist hit. Lets the graph skip straight to
    # synthesis instead of running the live ReAct tool loop.
    used_cached_watchlist: bool

    # ---- Set once the agent decides it has enough signal ----
    ready_to_report: bool

    # ---- Final output, set by the last node ----
    report: dict   # will hold a PlatformRiskReport, dumped to dict


VerdictTone = Literal["clear", "warn", "risk"]
PatternScore = Literal["Pass", "Risk", "Fail"]


class EvidenceReview(BaseModel):
    provided: bool
    repetition: Optional[str] = None
    resolution_pattern: Optional[str] = None
    volume: Optional[str] = None
    contradiction: Optional[str] = None


class RealityCheckFinding(BaseModel):
    source: str
    finding: str
    url: Optional[str] = None


class RealityCheck(BaseModel):
    performed: bool
    findings: list[RealityCheckFinding] = Field(default_factory=list)
    contradicts_stated_framing: Optional[str] = None
    note: Optional[str] = None


class Disqualifier(BaseModel):
    condition: str
    cost: str


class PatternResult(BaseModel):
    name: str
    score: PatternScore
    score_value: Literal[0, 1, 2]
    reason: str
    # This is the humility layer: how confident is this specific finding?
    confidence: Literal["strong evidence", "limited evidence", "inferred", "no signal found"]


class RedFlag(BaseModel):
    quote: str
    explanation: str


class FixFirst(BaseModel):
    what: str
    with_whom: str
    question: str


class PlatformRiskReport(BaseModel):
    """The full report a completed graph run produces."""
    id: str
    created_at: str
    subject: str          # e.g. "Zapier AI Actions — customer support automation"
    summary: str
    verdict: str           # "Low risk" | "Moderate risk" | "High risk"
    verdict_tone: VerdictTone
    evidence_review: EvidenceReview
    reality_check: RealityCheck
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    patterns: list[PatternResult]   # exactly 6, same rule as Groundwork
    red_flags: list[RedFlag] = Field(default_factory=list)
    fix_first: FixFirst


# The six dimensions this agent scores, in the fixed order they must
# always appear in a report's `patterns` list.
PLATFORM_RISK_DIMENSIONS = [
    "Data Handling Posture",
    "Vendor Stability",
    "Incident History",
    "Community Signal",
    "Compliance Posture",
    "Integration Risk",
]