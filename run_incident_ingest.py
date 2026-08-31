"""
One-time script. Run this once to ingest the AI Incident Database CSV into
Pinecone's incident-corpus namespace. Re-run only if the CSV changes.
"""

from rag.retrieval import ingest_incident_corpus

if __name__ == "__main__":
    count = ingest_incident_corpus()
    print(f"\nDone. {count} incidents ingested.")
