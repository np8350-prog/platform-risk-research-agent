# Platform Risk Research Agent

An autonomous LangGraph agent that researches an AI SaaS vendor or no-code
platform and produces a structured, evidence-graded trust report. It runs
with almost no human in the loop after you hit go: check a pre-researched
watchlist, search live if needed, ground every score in an authored risk
framework, score six dimensions, and, when a vendor scores badly enough,
propose comparable alternatives scored against the exact same use case.

**The question it exists to answer:** "Is this AI vendor safe to
recommend?", with receipts, not just an opinion.

**The one idea everything else serves:** a vendor's own claim about itself
and an independently verified fact are not the same strength of evidence.
The watchlist, the RAG grounding, and the scoring all exist to keep that
distinction visible instead of flattening it.


## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up API keys

Copy `.env.example` to `.env` and fill in three keys:

```
AI_GATEWAY_API_KEY=    # Vercel AI Gateway (routes to DeepSeek for reasoning,
                        # OpenAI's embedding model for the corpus)
SEARCH_API_KEY=        # Tavily, for live vendor research
PINECONE_API_KEY=      # Vector store for the authored risk framework
```

No direct OpenAI account is needed. `AI_GATEWAY_API_KEY` covers both the
reasoning calls (routed to DeepSeek) and the embedding calls (routed to
OpenAI's embedding model), all through one Gateway key and one bill.

### 3. Build the watchlist (one-time)

The watchlist is 20 pre-researched vendors, checked before any live search
runs, so common vendors don't get re-researched from scratch every request.
The raw research already lives in `data/watchlist/raw/`; this step converts
it into the JSON format the agent actually reads:

```bash
python3 scripts/build_watchlist_json.py
```

### 4. Ingest the framework corpus (one-time)

Embeds and indexes the authored risk framework (book failure patterns,
ControlGap notes, compliance reference material) into Pinecone:

```bash
python3 -c "from rag.retrieval import ingest_corpus; ingest_corpus()"
```

### 5. Run it

Two things running at once, in separate terminals.

**Terminal 1**, start the backend and leave it running:
```bash
uvicorn backend.app:app --reload --port 8000
```

**Terminal 2** (or Finder), open the UI, it's a static file, no server needed:
```bash
open ui/index.html
```

Enter a vendor, a use case, and buyer context, then run a diagnostic.


## Project Structure

```
platform-risk-research-agent/
├── data/watchlist/       # 20 pre-researched vendors (raw/ + processed/)
├── rag/                  # Authored framework corpus + retrieval logic
├── agent/                # The graph itself: state, nodes, wiring
├── tools/                # Three search tools (web, compliance, firmographic)
├── backend/              # FastAPI layer exposing the graph over HTTP
├── ui/                   # Single-file browser interface, no build step
├── scripts/               # One-off utilities (watchlist JSON builder)
├── reports/               # Saved report output (fills automatically)
└── docs/                 # Architecture, setup, and API reference
```

See `docs/ARCHITECTURE.md` for how the pieces fit together, and
`docs/API.md` for the exact report and endpoint shapes.


## The Pipeline

```
Trigger
   │
   ▼
watchlist_check ──hit──► framework_retrieval ──► synthesis ──► finalize_report ──► recommendation
   │
  miss
   │
   ▼
live_research ──► framework_retrieval ──► synthesis ──► finalize_report ──► recommendation
```

- **watchlist_check**, the cost-saving node. Checks if the vendor already
  has fresh (under 30 days old), pre-researched data.
- **live_research**, only on a miss. Calls three search tools, plus
  context-triggered searches when the buyer context signals something a
  generic sweep wouldn't catch (minors, protected health information,
  payment card data).
- **framework_retrieval**, grounds every score in the authored framework,
  not just general model knowledge.
- **synthesis**, one reasoning call per dimension, each scored Pass, Risk,
  or Fail with an honest confidence tag.
- **finalize_report**, evidence review, reality check, disqualifiers, red
  flags, and a concrete first action.
- **recommendation**, only runs when the verdict is bad enough (Elevated
  risk or worse, or any disqualifier). Proposes 2-3 comparable vendors from
  the watchlist, scored against the exact same use case, so a rejection
  comes with a real next step instead of a dead end.


## Key Design Decisions

| Decision | Why |
|---|---|
| LangGraph over a linear script | The agent needs to reason step by step about whether it has enough signal; a fixed pipeline can't express that. |
| Watchlist-first architecture | The core cost control. Twenty common vendors are researched once and cached; live search only runs on a miss. |
| DeepSeek for reasoning, OpenAI's embedding model for the corpus | DeepSeek has no embeddings endpoint. Embedding is a one-time cost; reasoning runs repeatedly, so its cost matters more. |
| Confidence vocabulary (strong / limited / inferred / no signal found) | Runs through the entire system, hand-tagged watchlist data and live LLM scoring are indistinguishable in the final report. |
| Deterministic diff for alternative comparisons | Whether an alternative scores better or worse than the rejected vendor is computed with plain arithmetic, not guessed by a model, so it cannot drift or hallucinate. |
| Never-crash rule | Every tool and every LLM call has a fallback. A bad response degrades to an honest "no signal found," never a crash, never a fabricated fact. |

See `stack_decision.md` for the full reasoning behind each technology
choice, including the switch off direct OpenAI billing.


## Status

All core features are built and tested end to end against real vendors:
watchlist lookup, live research with context-aware search, RAG-grounded
scoring, disqualifier detection, and the recommendation engine. See
`docs/ARCHITECTURE.md` for what's tested versus what's still a known gap.
