"""
Firmographic / vendor stability tool.

Note on approach: a dedicated firmographic API (e.g. Crunchbase) would
give cleaner structured data, but reliable free-tier access wasn't
available within this project's timeline. Instead, this tool uses
targeted search synthesis: it asks specific questions a firmographic
API would answer (funding, founding year, team size, recent news)
and lets the reasoning model synthesize the search results. This
trade-off is documented in stack_decision.md.
"""

from tools.web_search import web_search


STABILITY_QUERIES = [
    "{vendor} company funding round",
    "{vendor} founded year headquarters",
    "{vendor} layoffs OR shutdown OR acquired",
    "{vendor} company size employees",
]


def firmographic_search(vendor_name: str) -> list[dict]:
    """
    Runs targeted queries to gather vendor stability signal:
    funding, age, size, and any distress signals (layoffs, shutdown,
    acquisition). Returns deduplicated results. Empty list on failure,
    never raises.
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for template in STABILITY_QUERIES:
        query = template.format(vendor=vendor_name)
        results = web_search(query, max_results=3)
        for r in results:
            if r["url"] and r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                all_results.append(r)

    if not all_results:
        print(f"[firmographic_search] no stability signal found for {vendor_name}")

    return all_results