"""ARICO v2 LangGraph Builder.

Graph structure:
    START → fetch_store_metadata → assess_situation
        → [if sufficient] → synthesize_findings
        → [if gaps] → [parallel analysts] → synthesize_findings
    synthesize_findings
        → [no action] → final_report → END
        → [action needed] → generate_campaign → calculate_cost
            → [low risk] → execute_promotion
            → [high risk] → request_approval
                → [approved] → execute_promotion
                → [rejected] → archive_rejected
                → [modified] → generate_campaign (loop, max 3)
    execute_promotion / archive_rejected → final_report → END
"""
from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from arico import config
from arico.graph.nodes import (
    archive_rejected,
    assess_data_sufficiency,
    calculate_cost_and_roi,
    execute_promotion,
    fetch_store_metadata,
    generate_campaign,
    generate_final_report,
    run_competitor_analyst,
    run_feedback_analyst,
    run_inventory_analyst,
    run_sales_analyst,
    synthesize_findings,
)
from arico.graph.routing import (
    route_after_approval,
    route_after_risk_gate,
    route_after_synthesis,
    route_after_assessment,
)
from arico.hitl.approval import request_human_approval
from arico.models.state import ARICOState

logger = logging.getLogger(__name__)


def build_graph(checkpointer=None):
    """Build the ARICO v2 StateGraph.

    Args:
        checkpointer: Optional checkpointer for persistence.
                      If None, uses MemorySaver.

    Returns:
        Compiled StateGraph ready for execution.
    """
    graph = StateGraph(ARICOState)

    # ── Register nodes ─────────────────────────────────────────────────

    # Phase 1: Store context
    graph.add_node("fetch_store_metadata", fetch_store_metadata)

    # Phase 2: Situation Assessment
    graph.add_node("assess_situation", assess_data_sufficiency)

    # Phase 3: SQL Analyst agents (targets for Send())
    graph.add_node("sales_analyst", run_sales_analyst)
    graph.add_node("competitor_analyst", run_competitor_analyst)
    graph.add_node("inventory_analyst", run_inventory_analyst)
    graph.add_node("feedback_analyst", run_feedback_analyst)

    # Phase 4: Synthesis
    graph.add_node("synthesize_findings", synthesize_findings)

    # Phase 5: Campaign pipeline
    graph.add_node("generate_campaign", generate_campaign)
    graph.add_node("calculate_cost", calculate_cost_and_roi)

    # Phase 6: HITL
    graph.add_node("request_approval", request_human_approval)

    # Phase 7: Execution
    graph.add_node("execute_promotion", execute_promotion)
    graph.add_node("archive_rejected", archive_rejected)

    # Phase 8: Report
    graph.add_node("final_report", generate_final_report)

    # ── Edges ──────────────────────────────────────────────────────────

    # START → Phase 1
    graph.add_edge(START, "fetch_store_metadata")

    # Phase 1 → Phase 2
    graph.add_edge("fetch_store_metadata", "assess_situation")

    # Phase 2 → Conditional: spawn analysts or go to synthesis
    graph.add_conditional_edges(
        "assess_situation",
        route_after_assessment,
        ["synthesize_findings", "sales_analyst", "competitor_analyst",
         "inventory_analyst", "feedback_analyst"],
    )

    # Analysts → Synthesis (all converge here after parallel execution)
    graph.add_edge("sales_analyst",     "synthesize_findings")
    graph.add_edge("competitor_analyst","synthesize_findings")
    graph.add_edge("inventory_analyst", "synthesize_findings")
    graph.add_edge("feedback_analyst",  "synthesize_findings")

    # Synthesis → Conditional: no action → report, action → campaign
    graph.add_conditional_edges(
        "synthesize_findings",
        route_after_synthesis,
        {
            "generate_campaign": "generate_campaign",
            "final_report": "final_report",
        },
    )

    # Campaign → Cost calculation
    graph.add_edge("generate_campaign", "calculate_cost")

    # Cost → Risk gate
    graph.add_conditional_edges(
        "calculate_cost",
        route_after_risk_gate,
        {
            "execute_promotion": "execute_promotion",
            "request_approval": "request_approval",
        },
    )

    # Approval → Conditional
    graph.add_conditional_edges(
        "request_approval",
        route_after_approval,
        {
            "execute_promotion": "execute_promotion",
            "archive_rejected": "archive_rejected",
            "generate_campaign": "generate_campaign",
        },
    )

    # Terminal nodes → Final report
    graph.add_edge("execute_promotion", "final_report")
    graph.add_edge("archive_rejected",  "final_report")

    # Final report → END
    graph.add_edge("final_report", END)

    # ── Compile ────────────────────────────────────────────────────────
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("ARICO v2 graph compiled successfully")
    return compiled


def get_sqlite_checkpointer(db_path: str | None = None):
    """Create a SQLite-backed checkpointer (same DB as retail data).

    In production, swap this for PostgresSaver from langgraph-checkpoint-postgres.
    """
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    path = db_path or config.SQLITE_DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()
    return checkpointer


def get_memory_checkpointer() -> MemorySaver:
    """Create an in-memory checkpointer for testing."""
    return MemorySaver()
