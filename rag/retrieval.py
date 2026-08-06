"""
RAG retrieval over Nelly's own framework: the book's six failure
patterns, plus ControlGap and governance playbook material.

Embedding model: OpenAI text-embedding-3-small (1536 dimensions).
This number MUST match the Pinecone index's configured dimension
exactly, or writes fail silently at insert time. Locked here as a
constant so it's never guessed at in two different places.
"""

import os
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
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
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set in .env")
        _openai = OpenAI(api_key=api_key)
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