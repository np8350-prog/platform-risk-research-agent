from rag.retrieval import retrieve_incident_matches

query = "AI hiring tool used to screen job candidates automatically"
matches = retrieve_incident_matches(query, top_k=2)

print(f"\nQuery: {query}\n")
print(f"Found {len(matches)} matches:\n")
for i, m in enumerate(matches, 1):
    print(f"--- Match {i} ---")
    print(f"Text: {m['text']}")
    print(f"Sector: {m['sector']}")
    print(f"Harm domain: {m['harm_domain']}")
    print(f"Year: {m['year']}")
    print(f"Similarity score: {m['similarity_score']}")
    print(f"Confidence: {m['confidence']}\n")
