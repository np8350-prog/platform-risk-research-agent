"""
Compliance and security-signal search tool.

Distinct from general web_search: this tool constructs targeted queries
around specific compliance certifications and security disclosures,
rather than open-ended news search. Reuses the same Tavily client under
the hood, but the query shape and what we're looking for is different
enough to warrant its own tool for the assignment's 3+ tool requirement,
and for clarity in the report.
"""

from tools.web_search import web_search


# Certifications and disclosures relevant to a fintech buyer evaluating
# a vendor. Not exhaustive, but covers what shows up publicly most often.
COMPLIANCE_MARKERS = [
    "SOC 2",
    "GDPR compliance",
    "ISO 27001",
    "data breach",
    "security incident disclosure",
]


def compliance_search(vendor_name: str) -> list[dict]:
    """
    Runs a set of targeted compliance/security queries for a vendor
    and returns the combined, deduplicated results. Returns an empty
    list on total failure, never raises.
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for marker in COMPLIANCE_MARKERS:
        query = f"{vendor_name} {marker}"
        results = web_search(query, max_results=3)
        for r in results:
            if r["url"] and r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    if not all_results:
        print(f"[compliance_search] no compliance signal found for {vendor_name}")

    return all_results