# Stack Decision: Platform Risk Research Agent

## Primary stack: LangGraph

Platform Risk research is not a linear pipeline. The agent needs to
decide, per target, whether it has enough signal to score a risk
category or needs another search pass. That's a ReAct-style reason-act
loop, which is what LangGraph is built for. n8n would satisfy the
assignment on paper, but it hides that reasoning behind visual nodes
instead of showing it, and this project is meant to show explicit
multi-step reasoning. LangGraph also lets the report generation stay
stateful across nodes, so the humility layer (flagging low-confidence
findings) can live in the graph state itself instead of being bolted
on as a final formatting pass. This builds directly on the existing
`langgraph-chaos-agent` repo instead of starting from zero. Fintech
buyers evaluating a vendor need to see the reasoning trail, not just a
score, and LangGraph makes that trail inspectable.

## Retrieval: Pinecone

A dedicated `platform-risk-corpus` index, 1536 dimensions (OpenAI
`text-embedding-3-small`), kept separate from the existing `n8n` index
used in other labs to avoid dimension mismatches and topic bleed
between unrelated corpora. Grounded in Nelly's own framework: the six
failure patterns from "The Interface Is Not the System," and
ControlGap's four failure modes plus compliance reference material
(SOC 2, GDPR, ISO 27001, breach disclosure patterns).

## Known gap, in progress: source verification

Early feedback flagged a real weakness: the current tool design pulls
web search results without distinguishing a vendor's own claims from
independent, third-party confirmation. A vendor's marketing page
saying "SOC 2 compliant" and an actual audit report referencing it are
not the same strength of evidence, and the original design treated
them the same.

Fix in progress: tagging each search result by source type (vendor-
owned, review platform, news outlet, regulator/auditor) and feeding
that tag into the `confidence` field already defined in the report
schema (`strong evidence / limited evidence / inferred / no signal
found`). A claim only earns "strong evidence" if it's independently
confirmed, not just self-reported.

## Competitive landscape

This space already has established players, mostly built for security
and compliance teams managing vendor portfolios at enterprise scale:

- **Vanta** — bundles compliance automation with vendor risk; its
  document AI reads uploaded SOC 2 reports and summarizes control
  gaps and remediation suggestions.
- **Prevalent** — aggregates cyber signals, financial health, sanctions
  lists, and negative news from 500,000+ sources into one continuously
  refreshed risk rating.
- **VISO TRUST** — every score is traceable to a specific piece of
  evidence (e.g. a direct quote from a SOC 2 report) rather than a
  black-box rating. This is the closest existing product to the
  source-verification problem flagged above, and validates it as a
  real, unsolved gap rather than a minor detail.
- **Torii** — reads SOC reports, ISO certs, and DPAs, then auto-
  populates answers with passage-level citations back to source
  documents; also pre-scores vendors on firmographic signals before
  any formal review starts.
- **Sprinto** — scans vendor documents for missing clauses or controls
  and suggests follow-up questions, aimed at fast-growing teams
  wanting a repeatable vendor review workflow.

**How this project differs:** these tools are built for security teams
running formal, ongoing vendor management across large portfolios.
This agent is self-serve, aimed at a single buyer making a single
adopt/don't-adopt decision before onboarding one AI vendor. None of
the above are grounded in a named, authored point of view the way this
agent is grounded in Nelly's own failure-pattern framework, they run
generic scoring methodologies. The gap VISO TRUST is solving for
enterprise (evidence-traceable scoring, not black-box ratings) is the
same gap this project is solving for a single self-serve buyer.