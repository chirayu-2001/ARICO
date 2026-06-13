"""Graph state schema for ARICO v2."""
from __future__ import annotations

from typing import Annotated, TypedDict

from arico.models.alerts import Alert
from arico.models.campaigns import (
    ApprovalStatus,
    Campaign,
    CostEstimate,
    DeploymentResult,
    HumanApproval,
)
from arico.models.recommendations import RecommendationDecision
from arico.models.reports import (
    AnalystReport,
    StoreMetadata,
    SituationAssessment,
)


def _merge_errors(existing: list[str], new: list[str]) -> list[str]:
    """Reducer: append new errors to existing list."""
    return existing + new


def _merge_logs(existing: list[str], new: list[str]) -> list[str]:
    """Reducer: append new log entries to existing list."""
    return existing + new


class ARICOState(TypedDict):
    """Central state for the ARICO v2 orchestration graph.

    Flow:
      1. alert → fetch_store_metadata → assess_situation
      2. (optional) → analyst sub-agents (parallel SQL investigation)
      3. → synthesize_findings → [no action → final_report] OR [action needed →]
      4. → generate_campaign → calculate_cost
      5. → risk gate → (auto-deploy OR human approval)
      6. → deployment_result → final_report
    """

    # ── Input ──────────────────────────────────────────────────────────
    alert: Alert

    # ── Phase 1: Store Context (always runs, SQL lookup) ───────────────
    store_metadata: StoreMetadata | None

    # ── Phase 2: Situation Assessment ──────────────────────────────────
    situation_assessment: SituationAssessment | None

    # ── Phase 3: Analyst Reports (populated by SQL sub-agents) ─────────
    sales_analysis: AnalystReport | None
    competitor_analysis: AnalystReport | None
    inventory_analysis: AnalystReport | None
    feedback_analysis: AnalystReport | None
    research_errors: Annotated[list[str], _merge_errors]

    # ── Phase 4: Synthesis (NEW — decides action vs. no-action) ────────
    recommendation: RecommendationDecision | None

    # ── Campaign (only if action needed) ──────────────────────────────
    proposed_campaign: Campaign | None
    cost_estimate: CostEstimate | None

    # ── HITL ───────────────────────────────────────────────────────────
    requires_approval: bool
    approval_status: ApprovalStatus | None
    human_feedback: str | None

    # ── Execution ──────────────────────────────────────────────────────
    deployment_result: DeploymentResult | None

    # ── Meta ───────────────────────────────────────────────────────────
    iteration_count: int
    execution_log: Annotated[list[str], _merge_logs]
    final_report: str | None
