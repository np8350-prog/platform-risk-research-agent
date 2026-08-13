# Setup

Step-by-step instructions for getting the agent running from a fresh
clone. See `README.md` for the short version; this document goes deeper
on each step and covers troubleshooting.


## 1. Install dependencies

```bash
pip install -r requirements.txt
```

If a package fails to install with a permissions error, try:
```bash
pip install -r requirements.txt --break-system-packages
```


## 2. API keys

Three keys are required. None of them are a direct OpenAI account, that
dependency was removed early in the project specifically to avoid it.

| Variable | Where to get it | Used for |
|---|---|---|
| `AI_GATEWAY_API_KEY` | vercel.com, AI Gateway tab | Reasoning calls (routed to DeepSeek) and corpus embeddings (routed to OpenAI's embedding model), both through the same Gateway |
| `SEARCH_API_KEY` | tavily.com | Live vendor research when a vendor isn't on the watchlist |
| `PINECONE_API_KEY` | pinecone.io | Vector store for the authored risk framework corpus |

Copy the template and fill it in:
```bash
cp .env.example .env
```

**A note on the Vercel Gateway specifically**: even the free credit tier
requires a card on file and can be rate-limited independently of request
pacing. If embedding calls fail with a rate-limit error even after
retrying, the fix is usually a small paid top-up (a few dollars), not a
code change, free-tier credit and paid credit are treated differently by
the Gateway's throttling.


## 3. Build the watchlist

The watchlist is 20 vendors researched once and cached, so the agent
doesn't re-search the same well-known vendors on every request. The raw
research already exists in `data/watchlist/raw/{vendor}/`; this step
converts it into the JSON format the graph actually reads:

```bash
python3 scripts/build_watchlist_json.py
```

Expected output: `Done. Built 20 vendor JSON files in
data/watchlist/processed/`. Re-run this any time a file under
`data/watchlist/raw/` changes.


## 4. Ingest the framework corpus

```bash
python3 -c "from rag.retrieval import ingest_corpus; ingest_corpus()"
```

This chunks and embeds every `.txt` file in `rag/corpus/` and upserts it
into a Pinecone index named `platform-risk-corpus`. It's a one-time step;
re-run it only if a corpus file changes. Running it again on an existing
index adds to it rather than replacing it, if a clean re-index is ever
needed, delete the Pinecone index first.


## 5. Run the backend

```bash
uvicorn backend.app:app --reload --port 8000
```

`uvicorn` is a foreground process: the terminal window running it is
dedicated to it and will show live request logs. Closing that window or
pressing Ctrl+C stops the server. To confirm it's running from a second
terminal:

```bash
curl http://localhost:8000/api/health
```

Should return `{"status":"ok"}`. If instead it says `Connection refused`,
nothing is listening on that port, the server isn't running. A `404` on
`GET /` is expected and harmless, the backend only has API routes, not a
root page.


## 6. Open the UI

```bash
open ui/index.html
```

This is a static file with no build step, it can also be opened by
double-clicking it in Finder. It talks to `http://localhost:8000`
directly, so the backend from step 5 must already be running.


## Common Issues

**"Vendor name is required" even though the fields look filled in**, all
three fields (vendor, use case, buyer context) are required; the error
message names exactly which ones are still empty.

**A report seems to be using old logic after a code change**, the
backend needs to actually reload. `--reload` usually catches file changes
automatically, but if in doubt, stop it (Ctrl+C) and restart it. Also
confirm the UI file itself was saved with the new version, `grep` for a
distinctive string from the change to check.

**Downloaded PDF looks broken (missing chart)**, this was a real bug
found during development: the PDF export library doesn't reliably render
raw inline SVG. The fix rasterizes the chart to an image before capture;
if a broken chart export ever recurs, check that this rasterization step
(`svgToPngDataUrl` in `ui/index.html`) is still running before
`html2pdf()` is called.
