# Architecture

This document explains how the agent is put together, node by node and
function by function. It assumes no prior context, if a term is unfamiliar
(disqualifier, verdict tone, confidence tag), it's defined the first time
it's used below.


## Core Concepts

Every vendor gets scored on **six dimensions**: Vendor Stability, Incident
History, Community Signal, Compliance Posture, Data Handling Posture, and
Integration Risk. Each dimension lands on **Pass**, **Risk**, or **Fail**.

Those six scores combine into an overall **verdict**, on a graduated scale:

| Verdict | Trigger | Color |
|---|---|---|
| Low risk | 0 dimensions flagged Risk, no Fail | Green |
| Moderate risk | 1-2 dimensions flagged Risk | Blue |
| Elevated risk | 3-4 dimensions flagged Risk | Yellow |
| High risk | 5-6 flagged, or exactly one Fail | Red |
| Critical risk | Two or more Fails | Red |

A **disqualifier** is a separate, harder signal from the six-dimension
score: a specific requirement the buyer stated that the vendor demonstrably
cannot meet (for example, a buyer working with minors and a vendor with no
children's-privacy policy). A disqualifier can be present even when all six
dimensions look fine, and it always forces the recommendation engine to
run, regardless of the verdict.

Every finding carries a **confidence tag**: `strong evidence` (an
independent auditor, registrar, or government body confirms it),
`limited evidence` (traceable to a specific named source but not
independently verified), `inferred` (general narrative, no single
traceable source), or `no signal found` (nothing substantive found, stated
honestly instead of guessed).


## The Pipeline, Node by Node

### `watchlist_check_node`

The entry point. Normalizes the vendor name and checks whether
`data/watchlist/processed/{vendor}.json` exists and is under 30 days old.
On a fresh hit, it loads all six dimensions of cached research directly
into `research_notes` and sets `used_cached_watchlist=True`, which lets
the graph skip the entire live research branch. On a miss, stale entry, or
unreadable cache file, it logs why and falls through to live research,
never raising.

### `live_research_node`

Runs only on a watchlist miss. Calls `firmographic_search` (Vendor
Stability) and `compliance_search` (Compliance Posture), both of which
internally fan out into several targeted queries already. Then runs one
targeted web search each for the four remaining dimensions.

It also checks the buyer context for three specific signals, minors,
protected health information, or payment card data, and if any are
present, runs one additional targeted search for the specific artifact
that matters (a COPPA policy, a signed BAA, a PCI DSS level). This exists
because a generic compliance sweep has no way to know a buyer cares about
children's privacy specifically; without a dedicated search, that concern
would go completely unaddressed even though it was stated directly in the
buyer context.

### `framework_retrieval_node`

Runs on both paths, right before scoring. Embeds a query built from the
vendor, use case, and buyer context, and retrieves the closest-matching
excerpts from the authored framework corpus (the book's failure patterns,
ControlGap's failure modes, and reference material on SOC 2, GDPR, ISO
27001/27701, HIPAA, PCI DSS, and FedRAMP). This is what makes scoring
reflect a specific point of view instead of a model's generic training
knowledge.

### `synthesis_node`

Scores each of the six dimensions with one reasoning call per dimension,
each grounded in both the research notes for that dimension and the
retrieved framework context. Scoping calls per-dimension, rather than one
call for all six, means a single malformed response can't sink the other
five, and each prompt stays small enough for the model to actually engage
with the material instead of skimming.

The verdict is then computed deterministically from the six scores (see
the table above), not asked of the model. This matters: an earlier version
only checked whether *any* dimension was flagged, so a vendor with one
Risk and a vendor with five Risk both showed as "Moderate risk." The fixed
version counts how many dimensions are flagged and grades accordingly.

### `finalize_report_node`

One more reasoning call that looks across all six already-scored
dimensions at once, since these fields need the whole picture, not one
dimension in isolation:

- **evidence_review**, is evidence repeated across sources without new
  substantiation, is there a documented pattern for resolving issues, is
  the evidence volume meaningful, do any sources contradict each other.
- **reality_check**, does the vendor's own framing hold up against what
  was actually found, including whether the vendor's product category
  actually matches what the buyer says they intend to do with it.
- **disqualifiers**, only populated if the buyer's stated context creates
  a hard requirement the vendor demonstrably doesn't meet.
- **red_flags**, specific findings worth flagging, each grounded in the
  research, not general risk commentary.
- **fix_first**, one concrete next action, not "do more diligence."

Each of these five fields is validated and salvaged independently. If one
field comes back malformed (for example, a model returning a plain string
where a structured object was required), that field falls back to a safe
default while the other four, which parsed correctly, are kept rather than
discarded along with it.

Citations in `reality_check` are drawn from a list of URLs extracted from
the *full*, untruncated research notes, not the same notes truncated for
prompt size. An earlier version extracted URLs from the truncated text,
which meant a citation sitting past the truncation cutoff was invisible to
the model, even though it existed in the source data.

### `recommendation_node`

Runs last, and only when `_should_recommend_alternatives` returns true:
verdict is Elevated risk or worse, or any disqualifier is present,
regardless of the six-dimension verdict. On a clean report, this function
returns immediately and nothing downstream runs, no extra cost is spent
recommending alternatives to a vendor that's actually fine.

When it does run:

1. `_pick_alternative_vendors` selects up to three vendors from a fixed
   category map of the twenty watchlist vendors, with the rejected
   vendor's own entry removed before the model ever sees the list.
2. `_score_alternative` scores each candidate on all six dimensions,
   against the *same* use case and buyer context as the rejected vendor,
   using its own cached watchlist research. No live search needed.
3. `_compute_pattern_diff` compares each alternative's six scores against
   the rejected vendor's, dimension by dimension, labeling each one
   better, worse, or the same. This is plain arithmetic on the score
   values, not a model's opinion, so it cannot drift or hallucinate.
4. `_generate_alternative_reasons` writes the explanation for why each
   alternative was suggested, using the actual computed diff as input, and
   is explicitly required to reference specific dimensions from it. An
   earlier version generated this explanation *before* scoring existed,
   which produced generic category descriptions instead of grounded
   comparisons. Ordering the diff before the explanation is what fixed
   that.

If any step in this chain fails, the report keeps its already-complete
core content with an empty `alternatives` list, rather than losing work
that already succeeded.


## The Never-Crash Rule

Every tool (`web_search`, `compliance_search`, `firmographic_search`)
returns an empty list on failure instead of raising. Every LLM call has a
validated shape check and a safe fallback. The schema itself
(`agent/state.py`) is Pydantic, which actively rejects a malformed report
rather than silently accepting one, and the final validation step in
`synthesis_node` has its own fallback for the rare case where the schema
and the code drift out of sync (this happened once in development: a
fourth verdict tone was added to the scoring logic before the schema's
allowed values were updated to match, and the fallback is what keeps a
similar future mismatch from crashing the whole run instead of just
degrading one field).

A bad response should become an honest "no signal found," never a crash,
and never a fabricated fact.


## What's Tested

Every node above has been run against real API keys and real vendor data,
not just mocked. Specific things confirmed working end to end: a watchlist
hit and a watchlist miss both completing correctly, the graduated verdict
producing different tiers for vendors that used to be wrongly grouped
together, the recommendation engine triggering only when warranted and
producing a mathematically verifiable diff, and the disqualifier-overrides-
verdict rule (a Moderate-risk vendor still triggering alternatives when a
real disqualifier is present).

## Known Gaps

- The recommendation engine currently only pulls alternatives from the
  20-vendor watchlist. A rejected vendor with no comparable watchlist
  entry gets no alternatives, even if a good live-researchable
  alternative exists.
- Context-sensitivity detection covers three signals (minors, PHI,
  payment cards). Other sensitive categories (biometric data, government
  data, export-controlled information) aren't yet covered by a dedicated
  search trigger.
