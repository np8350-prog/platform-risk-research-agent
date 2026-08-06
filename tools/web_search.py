"""
Web search tool. Wraps Tavily's search API for general news, incident
reports, and public signal on a vendor or platform.

Every tool in this project follows the same shape: takes a plain string
query, returns a list of plain dicts, and never raises past this file.
A failed search returns an empty list with an error note instead of
crashing the graph.
"""

import os
from tavily import TavilyClient
from dotenv import load_dotenv

load_dotenv()

_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        api_key = os.getenv("SEARCH_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SEARCH_API_KEY is not set. Add it to your .env file."
            )
        _client = TavilyClient(api_key=api_key)
    return _client


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web for a query. Returns a list of dicts with
    'title', 'url', and 'content' keys. Returns an empty list
    with a printed warning on any failure, never raises.
    """
    try:
        client = _get_client()
        response = client.search(query=query, max_results=max_results)
        results = response.get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in results
        ]
    except Exception as e:
        print(f"[web_search] error for query '{query}': {e}")
        return []