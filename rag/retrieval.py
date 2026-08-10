"""
RAG retrieval over Nelly's own framework: the book's six failure
patterns, plus ControlGap and governance playbook material.

Embedding model: OpenAI text-embedding-3-small (1536 dimensions).
This number MUST match the Pinecone index's configured dimension
exactly, or writes fail silently at insert time. Locked here as a
constant so it's never guessed at in two different places.
"""

import os
import time
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI, RateLimitError
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
INDEX_NAME = "platform-risk-corpus"

_pc: Pinecone | None = None
_openai: OpenAI | None = None


def _get_pinecone() -> Pinecone:
    global _pc
    if _pc is None:
        api_key = os.getenv("PINECONE_API_KEY")
        if not api_key:
            raise RuntimeError("PINECONE_API_KEY not set in .env")
        _pc = Pinecone(api_key=api_key)
    return _pc


def _get_openai() -> OpenAI:
    global _openai
    if _openai is None:
        api_key = os.getenv("AI_GATEWAY_API_KEY")
        if not api_key:
            raise RuntimeError("AI_GATEWAY_API_KEY not set in .env")
        _openai = OpenAI(
            api_key=api_key,
            base_url="https://ai-gateway.vercel.sh/v1",
        )
    return _openai


def ensure_index_exists() -> None:
    """Creates the Pinecone index if it doesn't already exist."""
    pc = _get_pinecone()
    existing = [idx["name"] for idx in pc.list_indexes()]
    if INDEX_NAME in existing:
        print(f"Index '{INDEX_NAME}' already exists.")
        return

    pc.create_index(
        name=INDEX_NAME,
        dimension=EMBEDDING_DIMENSIONS,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    print(f"Created index '{INDEX_NAME}' with dimension {EMBEDDING_DIMENSIONS}.")


CORPUS_DIR = "rag/corpus"
CHUNK_SIZE_WORDS = 150  # rough target; paragraphs are merged up to this size


def _chunk_text(text: str, source: str, chunk_size_words: int = CHUNK_SIZE_WORDS) -> list[dict]:
    """Splits text into paragraph-based chunks, merging short paragraphs
    together so chunks stay near chunk_size_words instead of being one
    paragraph each (too small) or the whole file (too big to embed well)."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current, current_words = [], 0

    for para in paragraphs:
        words = len(para.split())
        if current and current_words + words > chunk_size_words:
            chunks.append({"text": "\n\n".join(current), "source": source})
            current, current_words = [], 0
        current.append(para)
        current_words += words

    if current:
        chunks.append({"text": "\n\n".join(current), "source": source})

    return chunks


def _embed_batch(texts: list[str], max_retries: int = 4) -> list[list[float]]:
    """
    Embeds all texts in ONE request instead of one-per-text. This is both
    faster and far less likely to hit the Gateway's free-tier rate limit,
    which triggers on request bursts, not on total volume.

    Retries with exponential backoff if the free tier still rate-limits
    the single batched call — 5s, 10s, 20s, 40s — before giving up.
    """
    openai_client = _get_openai()
    for attempt in range(max_retries):
        try:
            response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            return [item.embedding for item in response.data]
        except RateLimitError:
            if attempt == max_retries - 1:
                raise
            wait = 5 * (2 ** attempt)
            print(f"Rate limited, retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait)


def ingest_corpus(corpus_dir: str = CORPUS_DIR) -> int:
    """
    Chunks every .txt file in corpus_dir, embeds all chunks in a single
    batched request, and upserts into the Pinecone index. Run this once
    (or whenever the corpus files change), not on every graph run — it's
    a one-time indexing step, not part of the request path.
    """
    ensure_index_exists()
    pc = _get_pinecone()
    index = pc.Index(INDEX_NAME)

    all_chunks = []
    for fname in sorted(os.listdir(corpus_dir)):
        if not fname.endswith(".txt"):
            continue
        with open(os.path.join(corpus_dir, fname), "r", encoding="utf-8") as f:
            text = f.read()
        all_chunks.extend(_chunk_text(text, source=fname))

    if not all_chunks:
        print(f"No .txt files found in {corpus_dir} — nothing to ingest.")
        return 0

    embeddings = _embed_batch([chunk["text"] for chunk in all_chunks])

    vectors = [
        {
            "id": f"{chunk['source']}-{i}",
            "values": embedding,
            "metadata": {"text": chunk["text"], "source": chunk["source"]},
        }
        for i, (chunk, embedding) in enumerate(zip(all_chunks, embeddings))
    ]

    index.upsert(vectors=vectors)
    print(f"Ingested {len(vectors)} chunks from {corpus_dir} into '{INDEX_NAME}'.")
    return len(vectors)


def retrieve_framework_context(query: str, top_k: int = 4) -> str:
    """
    Embeds `query` and retrieves the top_k most relevant chunks from the
    authored framework corpus (book failure patterns + ControlGap failure
    modes). Returns them concatenated as plain text, ready to drop into
    a synthesis prompt.

    Never raises. A RAG outage should degrade the report (framework_context
    comes back empty, synthesis proceeds without it) rather than crash the
    graph — same failure philosophy as the search tools.
    """
    try:
        query_embedding = _embed_batch([query])[0]

        pc = _get_pinecone()
        index = pc.Index(INDEX_NAME)
        results = index.query(vector=query_embedding, top_k=top_k, include_metadata=True)

        chunks = []
        for match in results.get("matches", []):
            metadata = match.get("metadata", {})
            text = metadata.get("text", "")
            source = metadata.get("source", "unknown")
            if text:
                chunks.append(f"[{source}] {text}")

        return "\n\n".join(chunks)

    except Exception as e:
        print(f"retrieve_framework_context failed, continuing without framework grounding: {e}")
        return ""