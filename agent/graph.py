"""
LangGraph wiring for the platform risk research agent.

Current shape:

    watchlist_check (entry)
        -> used_cached_watchlist=True  -> framework_retrieval -> synthesis -> finalize_report -> END
        -> used_cached_watchlist=False -> live_research -> framework_retrieval -> synthesis -> finalize_report -> END

framework_retrieval runs on both paths, right before synthesis, since it
depends only on vendor/use_case/buyer_context, not on where the research
notes came from.

synthesis scores the six dimensions (one DeepSeek call each). finalize_report
looks across all six at once to fill in evidence_review, reality_check,
disqualifiers, red_flags, and fix_first. If finalize_report fails for any
reason, the report keeps the honest placeholders synthesis set instead of
losing the six real scores that already succeeded.
"""

from langgraph.graph import StateGraph, END

from agent.state import GraphState
from agent.nodes import (
    watchlist_check_node,
    live_research_node,
    framework_retrieval_node,
    synthesis_node,
    finalize_report_node,
)


def _route_after_watchlist_check(state: GraphState) -> str:
    """Conditional edge: skip live research on a fresh watchlist hit."""
    if state.get("used_cached_watchlist"):
        return "framework_retrieval"
    return "live_research"


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("watchlist_check", watchlist_check_node)
    graph.add_node("live_research", live_research_node)
    graph.add_node("framework_retrieval", framework_retrieval_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("finalize_report", finalize_report_node)

    graph.set_entry_point("watchlist_check")

    graph.add_conditional_edges(
        "watchlist_check",
        _route_after_watchlist_check,
        {
            "framework_retrieval": "framework_retrieval",
            "live_research": "live_research",
        },
    )

    graph.add_edge("live_research", "framework_retrieval")
    graph.add_edge("framework_retrieval", "synthesis")
    graph.add_edge("synthesis", "finalize_report")
    graph.add_edge("finalize_report", END)

    return graph.compile()
