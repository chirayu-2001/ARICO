"""Human-in-the-loop approval logic.

Uses LangGraph's interrupt mechanism to pause the graph and wait
for human input. Supports approve, reject, and modify-with-feedback.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from langgraph.types import interrupt

from arico.models.campaigns import ApprovalStatus, HumanApproval
from arico.models.state import ARICOState

logger = logging.getLogger(__name__)


def request_human_approval(state: ARICOState) -> dict:
    """Pause execution and request human approval.
    
    Uses LangGraph's interrupt() to pause the graph. The graph state
    is checkpointed and can be resumed with the approval decision.
    
    The interrupt presents the campaign details and cost estimate to
    the brand owner for review.
    """
    campaign = state["proposed_campaign"]
    cost = state["cost_estimate"]
    iteration = state.get("iteration_count", 0)

    log_entry = (
        f"[{datetime.now().isoformat()}] Requesting human approval for "
        f"'{campaign.campaign_name}' (iteration {iteration + 1})"
    )
    logger.info(log_entry)

    # Build the approval request payload
    approval_request = {
        "message": "Campaign requires brand owner approval",
        "campaign": {
            "name": campaign.campaign_name,
            "store_id": campaign.store_id,
            "category": campaign.product_category,
            "type": campaign.promotion_type.value,
            "discount": f"{campaign.discount_pct * 100:.0f}%" if campaign.discount_pct else "N/A",
            "details": campaign.promotion_details,
            "channels": [c.value for c in campaign.channels],
            "duration_days": campaign.duration_days,
            "rationale": campaign.rationale,
        },
        "cost_estimate": {
            "total_cost": f"${cost.total_cost:,.2f}",
            "marketing_spend": f"${cost.marketing_spend:,.2f}",
            "margin_cost": f"${cost.margin_cost:,.2f}",
            "estimated_roi": f"{cost.estimated_roi:.2f}x",
            "recovery_rate": f"{cost.recovery_rate:.0%}",
            "risk_level": cost.risk_level,
            "risk_factors": cost.risk_factors,
        },
        "iteration": iteration + 1,
        "max_iterations": 3,
        "instructions": (
            "Respond with JSON: "
            '{"status": "approved"|"rejected"|"modified", '
            '"feedback": "optional feedback for modifications"}'
        ),
    }

    # This pauses the graph — execution resumes when human provides input
    human_response = interrupt(approval_request)

    # Parse the human response
    try:
        if isinstance(human_response, str):
            parsed = json.loads(human_response)
        elif isinstance(human_response, dict):
            parsed = human_response
        else:
            raise ValueError(f"Unexpected response type: {type(human_response)}")

        approval = HumanApproval(
            status=ApprovalStatus(parsed["status"]),
            feedback=parsed.get("feedback"),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.error(f"Invalid approval response: {human_response!r} — {e}")
        # Treat invalid input as rejection for safety
        approval = HumanApproval(
            status=ApprovalStatus.REJECTED,
            feedback=f"Invalid approval input: {e}",
        )

    logger.info(f"Human decision: {approval.status.value} (feedback: {approval.feedback})")

    result: dict = {
        "approval_status": approval.status,
        "execution_log": [
            log_entry,
            f"[{datetime.now().isoformat()}] Human decision: {approval.status.value}",
        ],
    }

    if approval.status == ApprovalStatus.MODIFIED:
        result["human_feedback"] = approval.feedback
        result["iteration_count"] = iteration + 1

    return result
