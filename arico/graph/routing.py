"""Conditional routing logic for the ARICO v2 graph."""
from __future__ import annotations

import logging
from typing import Literal

from langgraph.types import Send

from arico import config
from arico.models.campaigns import ApprovalStatus
from arico.models.reports import AgentType
from arico.models.state import ARICOState

logger = logging.getLogger(__name__)


# Map AgentType → node name in the graph
AGENT_NODE_MAP: dict[AgentType, str] = {
    AgentType.SALES_ANALYST:       "sales_analyst",
    AgentType.COMPETITOR_ANALYST:  "competitor_analyst",
    AgentType.INVENTORY_ANALYST:   "inventory_analyst",
    AgentType.FEEDBACK_ANALYST:    "feedback_analyst",
}


def route_after_assessment(state: ARICOState) -> list[Send] | str:
    """Route after situation assessment: spawn analysts or go directly to synthesis.

    Uses LangGraph's Send() API for dynamic parallel fan-out.
    Returns:
        - "synthesize_findings" if data is sufficient (no agents needed)
        - list[Send(...)] to spawn needed analyst agents in parallel
    """
    assessment = state.get("situation_assessment")

    if assessment is None:
        logger.error("No situation assessment found — spawning all agents as fallback")
        return [Send(node_name, state) for node_name in AGENT_NODE_MAP.values()]

    if assessment.data_sufficient:
        logger.info("Assessment: data sufficient — going directly to synthesis")
        return "synthesize_findings"

    sends = []
    for agent_type in assessment.agents_to_spawn:
        node_name = AGENT_NODE_MAP.get(agent_type)
        if node_name:
            sends.append(Send(node_name, state))
            logger.info(f"Assessment: spawning {agent_type.value} → {node_name}")

    if not sends:
        logger.warning("Assessment: data insufficient but no gaps identified — spawning all agents as fallback")
        return [Send(node_name, state) for node_name in AGENT_NODE_MAP.values()]

    return sends


def route_after_synthesis(
    state: ARICOState,
) -> Literal["generate_campaign", "final_report"]:
    """Route after synthesis: action needed → campaign, no action → report."""
    recommendation = state.get("recommendation")

    if recommendation is None or recommendation.action_needed:
        logger.info("Synthesis: action needed — proceeding to campaign generation")
        return "generate_campaign"
    else:
        logger.info(
            f"Synthesis: no action needed — routing to final report. "
            f"Reason: {recommendation.no_action_reason}"
        )
        return "final_report"


def route_after_risk_gate(
    state: ARICOState,
) -> Literal["execute_promotion", "request_approval"]:
    """Route after cost calculation: auto-deploy or request human approval."""
    if state.get("requires_approval", True):
        logger.info("Risk gate: human approval required")
        return "request_approval"
    else:
        logger.info("Risk gate: auto-deploying (low risk, high ROI)")
        return "execute_promotion"


def route_after_approval(
    state: ARICOState,
) -> Literal["execute_promotion", "archive_rejected", "generate_campaign"]:
    """Route after human approval decision."""
    approval = state.get("approval_status")

    if approval == ApprovalStatus.APPROVED:
        logger.info("Approval: approved — deploying")
        return "execute_promotion"

    elif approval == ApprovalStatus.REJECTED:
        logger.info("Approval: rejected — archiving")
        return "archive_rejected"

    elif approval == ApprovalStatus.MODIFIED:
        iteration = state.get("iteration_count", 0)
        if iteration >= config.MAX_HITL_ITERATIONS:
            logger.warning(
                f"Approval: modified but max iterations ({config.MAX_HITL_ITERATIONS}) reached — archiving"
            )
            return "archive_rejected"
        logger.info(f"Approval: modified (iteration {iteration + 1}) — re-generating campaign")
        return "generate_campaign"

    else:
        logger.error(f"Unexpected approval status: {approval}")
        return "archive_rejected"
