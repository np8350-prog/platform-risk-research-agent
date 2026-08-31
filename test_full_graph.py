from agent.graph import build_graph

graph = build_graph()

result = graph.invoke({
    "vendor_name": "Eightfold AI",
    "use_case": "AI-powered candidate screening and hiring recommendations",
    "buyer_context": "mid-size company hiring for corporate roles",
    "research_notes": [],
    "tool_calls_made": [],
    "framework_context": "",
    "incident_matches": [],
    "used_cached_watchlist": False,
    "ready_to_report": False,
    "report": {},
})

report = result["report"]

print("\n=== INCIDENT MATCHES ON REPORT ===")
for m in report.get("incident_matches", []):
    print(f"- {m['text']}")
    print(f"  sector: {m['sector']}, year: {m['year']}, "
          f"similarity: {m['similarity_score']}, confidence: {m['confidence']}\n")

print("\n=== INCIDENT HISTORY DIMENSION ===")
for p in report["patterns"]:
    if p["name"] == "Incident History":
        print(f"Score: {p['score']}")
        print(f"Confidence: {p['confidence']}")
        print(f"Reason: {p['reason']}")
