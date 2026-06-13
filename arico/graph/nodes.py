"""Graph node functions for ARICO v2.

Each function is a node in the LangGraph StateGraph. Nodes read from
and write to specific ARICOState fields.

v2 changes:
- fetch_store_metadata: replaces enrich_with_inventory (SQL lookup)
- 4 SQL analyst agents: replace the 4 mock-tool sub-agents
- synthesize_findings: new node that diagnoses root cause + decides action/no-action
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from arico import config
from arico import prompts
from arico.models.alerts import Alert
from arico.models.campaigns import (
    ApprovalStatus,
    Campaign,
    CampaignChannel,
    CostEstimate,
    DeploymentResult,
    PromotionType,
)
from arico.models.recommendations import RecommendationDecision
from arico.models.reports import (
    AgentType,
    AnalystReport,
    GAP_TO_AGENT,
    KnowledgeGap,
    ProductInfo,
    StoreMetadata,
    SituationAssessment,
)
from arico.models.state import ARICOState
from arico.db import save_report
from arico.tools.promotion_tool import deploy_promotion
from arico.tools.sql_tool import run_sql_query
from arico.tools.store_lookup import get_store_metadata

logger = logging.getLogger(__name__)


def _get_llm() -> BaseChatModel:
    """Create a configured LLM instance. Auto-detects provider from API keys."""
    provider = config.RESOLVED_PROVIDER
    model = config.RESOLVED_MODEL
    temperature = config.LLM_TEMPERATURE

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature)
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)


def _parse_json(content: str) -> dict:
    """Extract and parse JSON from LLM response (handles markdown code blocks)."""
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]
    return json.loads(content.strip())


def _with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) with exponential-backoff retries.

    Uses config.TOOL_MAX_RETRIES (default 2), giving TOOL_MAX_RETRIES+1 total
    attempts. Waits 1s, 2s, 4s... between attempts.
    """
    last_exc: Exception | None = None
    for attempt in range(config.TOOL_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < config.TOOL_MAX_RETRIES:
                wait = 2 ** attempt  # 1s, 2s, ...
                logger.warning(
                    f"Attempt {attempt + 1}/{config.TOOL_MAX_RETRIES + 1} failed: {exc}. "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)
            else:
                logger.error(
                    f"All {config.TOOL_MAX_RETRIES + 1} attempts failed. Last error: {exc}"
                )
    raise last_exc


# ═══════════════════════════════════════════════════════════════════════════
# Node 1: Fetch Store Metadata (Phase 1 — always runs, SQL lookup, no LLM)
# ═══════════════════════════════════════════════════════════════════════════

def fetch_store_metadata(state: ARICOState) -> dict:
    """Phase 1: Pull store info and inventory from SQLite.

    Always runs regardless of alert complexity. Provides the orchestrator
    with rich store context for the triage decision.
    """
    alert = state["alert"]
    log_entry = f"[{datetime.now().isoformat()}] Phase 1: Fetching store metadata for store {alert.store_id}"
    logger.info(log_entry)

    try:
        raw = get_store_metadata(alert.store_id)
        if raw is None:
            raise ValueError(f"Store {alert.store_id} not found in database")

        products = [
            ProductInfo(
                sku=p["sku"],
                name=p["name"],
                category=p["category"],
                base_price=p["base_price"],
                unit_margin_pct=p["unit_margin_pct"],
                stock_units=p["stock_units"],
                reorder_point=p["reorder_point"],
                max_allowable_discount_pct=p["max_allowable_discount_pct"],
                last_restock_date=p.get("last_restock_date"),
            )
            for p in raw.get("products", [])
        ]

        metadata = StoreMetadata(
            store_id=raw["store_id"],
            name=raw["name"],
            city=raw["city"],
            state=raw["state"],
            location_type=raw["location_type"],
            opened_date=raw["opened_date"],
            avg_monthly_foot_traffic=raw.get("avg_monthly_foot_traffic"),
            size_sqft=raw.get("size_sqft"),
            products=products,
        )

        return {
            "store_metadata": metadata,
            "execution_log": [log_entry + f" ✓ ({metadata.name}, {metadata.city})"],
        }

    except Exception as e:
        error_msg = f"Failed to fetch store metadata for {alert.store_id}: {e}"
        logger.error(error_msg)
        return {
            "store_metadata": None,
            "research_errors": [error_msg],
            "execution_log": [log_entry + f" ✗ ({e})"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Node 2: Situation Assessment (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════

def assess_data_sufficiency(state: ARICOState) -> dict:
    """Phase 2: LLM assesses whether we have enough data to diagnose the problem,
    or whether analyst agents must be spawned.
    """
    alert = state["alert"]
    metadata = state.get("store_metadata")
    log_entry = f"[{datetime.now().isoformat()}] Phase 2: Assessing data sufficiency for store {alert.store_id}"
    logger.info(log_entry)

    llm = _get_llm()

    metadata_context = "No store metadata available (lookup failed)."
    if metadata:
        products_summary = [
            {
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "stock_units": p.stock_units,
                "reorder_point": p.reorder_point,
                "last_restock_date": p.last_restock_date,
                "max_allowable_discount_pct": p.max_allowable_discount_pct,
            }
            for p in metadata.products
        ]
        metadata_context = json.dumps({
            "store_name": metadata.name,
            "location": f"{metadata.city}, {metadata.state}",
            "location_type": metadata.location_type,
            "avg_monthly_foot_traffic": metadata.avg_monthly_foot_traffic,
            "products": products_summary,
        }, indent=2)

    system_prompt = prompts.get("orchestrator_agent")

    human_prompt = f"""Loss Alert:
- Store ID: {alert.store_id}
- Loss Reason: {alert.loss_reason}
- Revenue at Risk: ${alert.revenue_at_risk:,.2f}
- Product Category: {alert.product_category}
- Units at Risk: {alert.estimated_units_at_risk}

Store Metadata:
{metadata_context}

Assess data sufficiency and identify knowledge gaps."""

    try:
        response = _with_retry(
            llm.with_config(
                run_name="Assess Situation | Data Sufficiency",
                tags=["orchestrator", "assess_situation", "phase-2"],
                metadata={
                    "store_id": alert.store_id,
                    "revenue_at_risk": alert.revenue_at_risk,
                    "product_category": alert.product_category,
                },
            ).invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)],
        )

        parsed = _parse_json(response.content)

        knowledge_gaps = [KnowledgeGap(g) for g in parsed.get("knowledge_gaps", [])]
        agents_to_spawn = list({GAP_TO_AGENT[gap] for gap in knowledge_gaps})

        triage = SituationAssessment(
            data_sufficient=parsed["data_sufficient"],
            knowledge_gaps=knowledge_gaps,
            agents_to_spawn=agents_to_spawn,
            reasoning=parsed.get("reasoning", "No reasoning provided"),
        )

        logger.info(
            f"Assessment: sufficient={triage.data_sufficient}, "
            f"gaps={[g.value for g in triage.knowledge_gaps]}, "
            f"agents={[a.value for a in triage.agents_to_spawn]}"
        )

        return {
            "situation_assessment": triage,
            "execution_log": [
                log_entry + f" ✓ (sufficient={triage.data_sufficient}, "
                f"agents={[a.value for a in triage.agents_to_spawn]})"
            ],
        }

    except Exception as e:
        logger.error(f"Assessment LLM call failed: {e}")
        # Conservative fallback: spawn all agents
        fallback_triage = SituationAssessment(
            data_sufficient=False,
            knowledge_gaps=list(KnowledgeGap),
            agents_to_spawn=list(AgentType),
            reasoning=f"Assessment LLM failed ({e}), spawning all agents as fallback",
        )
        return {
            "situation_assessment": fallback_triage,
            "research_errors": [f"Assessment LLM failed: {e}"],
            "execution_log": [log_entry + f" ✗ (fallback: all agents) — {e}"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Node 3a-d: SQL Analyst Sub-Agents (parallel via Send)
# Pattern: LLM generates SQL → tool executes → LLM analyzes results
# ═══════════════════════════════════════════════════════════════════════════

def _run_sql_analyst(
    state: ARICOState,
    agent_type: AgentType,
    prompt_name: str,
    state_field: str,
    log_label: str,
) -> dict:
    """Generic SQL analyst executor used by all 4 analyst nodes.

    Step 1: LLM generates SQL queries given table schemas + task.
    Step 2: Queries are executed against SQLite via run_sql_query.
    Step 3: LLM analyzes the results and produces a structured report.
    """
    alert = state["alert"]
    metadata = state.get("store_metadata")
    log_entry = f"[{datetime.now().isoformat()}] Analyst: {log_label} for store {alert.store_id}"
    logger.info(log_entry)

    store_context = f"Store ID: {alert.store_id}"
    if metadata:
        store_context = (
            f"Store ID: {alert.store_id} | {metadata.name} | "
            f"{metadata.city}, {metadata.state} | {metadata.location_type}"
        )

    system_prompt = prompts.get(prompt_name)
    llm = _get_llm()

    # ── Step 1: LLM generates SQL queries ──────────────────────────────
    step1_human = (
        f"{store_context}\n"
        f"Product Category: {alert.product_category}\n"
        f"Alert: {alert.loss_reason}\n"
        f"Revenue at Risk: ${alert.revenue_at_risk:,.2f}\n\n"
        f"Generate your SQL queries to investigate this situation. "
        f"Respond ONLY with JSON: {{\"queries\": [\"SELECT ...\", ...]}}"
    )

    try:
        sql_response = _with_retry(
            llm.with_config(
                run_name=f"{log_label} | Step 1: Generate SQL",
                tags=["analyst", agent_type.value, "sql-generation"],
                metadata={"store_id": alert.store_id, "analyst": agent_type.value},
            ).invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=step1_human)],
        )

        sql_parsed = _parse_json(sql_response.content)
        queries = sql_parsed.get("queries", [])

        if not queries:
            raise ValueError("LLM returned no SQL queries")

    except Exception as e:
        logger.error(f"{log_label} SQL generation failed: {e}")
        return {
            "research_errors": [f"{log_label} SQL generation failed: {e}"],
            "execution_log": [log_entry + f" ✗ (SQL gen failed: {e})"],
        }

    # ── Step 2: Execute SQL queries ────────────────────────────────────
    query_results = []
    executed_queries = []
    for query in queries[:3]:  # cap at 3 queries
        result = run_sql_query.invoke({"query": query})
        query_results.append({"query": query, "result": result})
        executed_queries.append(query)
        logger.info(
            f"  SQL [{agent_type.value}]: {query[:80]}... → "
            f"{result.get('row_count', 0)} rows"
        )

    # ── Step 3: LLM analyzes results ──────────────────────────────────
    step3_human = (
        f"Store: {store_context}\n"
        f"Alert: {alert.loss_reason}\n\n"
        f"Here are the SQL query results:\n"
        f"{json.dumps(query_results, indent=2)}\n\n"
        f"Analyze these results and produce your findings report. "
        f"Respond ONLY with JSON matching the output format in your instructions."
    )

    try:
        analysis_response = _with_retry(
            llm.with_config(
                run_name=f"{log_label} | Step 2: Analyze Results",
                tags=["analyst", agent_type.value, "analysis"],
                metadata={"store_id": alert.store_id, "analyst": agent_type.value, "row_count": sum(r["result"].get("row_count", 0) for r in query_results)},
            ).invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=step3_human)],
        )

        analysis_parsed = _parse_json(analysis_response.content)

        report = AnalystReport(
            analyst_type=agent_type,
            store_id=alert.store_id,
            queries_executed=executed_queries,
            key_findings=analysis_parsed.get("key_findings", ["No findings"]),
            summary=analysis_parsed.get("summary", "No summary available"),
            severity=analysis_parsed.get("severity", "low"),
        )

        return {
            state_field: report,
            "execution_log": [
                log_entry + f" ✓ (severity={report.severity}, {len(executed_queries)} queries)"
            ],
        }

    except Exception as e:
        logger.error(f"{log_label} analysis failed: {e}")
        return {
            "research_errors": [f"{log_label} analysis failed: {e}"],
            "execution_log": [log_entry + f" ✗ (analysis failed: {e})"],
        }


def run_sales_analyst(state: ARICOState) -> dict:
    """Sales Trend Analyst: investigates when the decline started, which SKUs,
    sudden vs gradual, and compares to monthly benchmarks.
    """
    return _run_sql_analyst(
        state,
        agent_type=AgentType.SALES_ANALYST,
        prompt_name="analyst_sales",
        state_field="sales_analysis",
        log_label="Sales Trend Analyst",
    )


def run_competitor_analyst(state: ARICOState) -> dict:
    """Competitor Intelligence Analyst: queries competitor_activity table
    and correlates events with sales timing.
    """
    return _run_sql_analyst(
        state,
        agent_type=AgentType.COMPETITOR_ANALYST,
        prompt_name="analyst_competitor",
        state_field="competitor_analysis",
        log_label="Competitor Intelligence Analyst",
    )


def run_inventory_analyst(state: ARICOState) -> dict:
    """Inventory Analyst: checks stock levels vs reorder points,
    identifies stockouts, and assesses promotion capacity.
    """
    return _run_sql_analyst(
        state,
        agent_type=AgentType.INVENTORY_ANALYST,
        prompt_name="analyst_inventory",
        state_field="inventory_analysis",
        log_label="Inventory Analyst",
    )


def run_feedback_analyst(state: ARICOState) -> dict:
    """Customer Feedback Analyst: aggregates ratings, identifies
    quality/service themes, and correlates with sales timeline.
    """
    return _run_sql_analyst(
        state,
        agent_type=AgentType.FEEDBACK_ANALYST,
        prompt_name="analyst_feedback",
        state_field="feedback_analysis",
        log_label="Customer Feedback Analyst",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Node 4: Synthesize Findings (NEW in v2)
# ═══════════════════════════════════════════════════════════════════════════

def synthesize_findings(state: ARICOState) -> dict:
    """Combine all analyst reports into a root-cause diagnosis.

    Routes to:
    - final_report  if no action is needed (seasonal blip, noise, etc.)
    - generate_campaign  if an intervention is warranted
    """
    alert = state["alert"]
    log_entry = f"[{datetime.now().isoformat()}] Synthesizing findings for store {alert.store_id}"
    logger.info(log_entry)

    # Collect all available reports
    reports = {}
    for field, label in [
        ("sales_analysis", "Sales Trend"),
        ("competitor_analysis", "Competitor Intel"),
        ("inventory_analysis", "Inventory Supply"),
        ("feedback_analysis", "Customer Feedback"),
    ]:
        report = state.get(field)
        if report is not None:
            reports[label] = {
                "severity": report.severity,
                "key_findings": report.key_findings,
                "summary": report.summary,
            }

    # Also include situation assessment reasoning
    assessment = state.get("situation_assessment")
    triage_reasoning = assessment.reasoning if assessment else "N/A"

    metadata = state.get("store_metadata")
    store_desc = f"Store {alert.store_id}"
    if metadata:
        store_desc = f"{metadata.name} ({metadata.city}, {metadata.location_type})"

    human_prompt = (
        f"Store: {store_desc}\n"
        f"Alert: {alert.loss_reason}\n"
        f"Revenue at Risk: ${alert.revenue_at_risk:,.2f}\n"
        f"Situation Assessment: {triage_reasoning}\n\n"
        f"Analyst Reports:\n{json.dumps(reports, indent=2)}\n\n"
        f"Errors (if any): {state.get('research_errors', [])}\n\n"
        f"Synthesize all findings and decide: is an intervention needed?"
    )

    llm = _get_llm()
    system_prompt = prompts.get("synthesize_findings")

    try:
        response = _with_retry(
            llm.with_config(
                run_name="Synthesize Findings | Root Cause Diagnosis",
                tags=["synthesis", "decision"],
                metadata={
                    "store_id": alert.store_id,
                    "reports_available": list(reports.keys()),
                },
            ).invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)],
        )

        parsed = _parse_json(response.content)

        recommendation = RecommendationDecision(
            action_needed=parsed["action_needed"],
            root_cause=parsed.get("root_cause", "Unknown"),
            reasoning=parsed.get("reasoning", ""),
            confidence=float(parsed.get("confidence", 0.5)),
            no_action_reason=parsed.get("no_action_reason"),
        )

        logger.info(
            f"Synthesis: action_needed={recommendation.action_needed}, "
            f"root_cause='{recommendation.root_cause[:80]}', "
            f"confidence={recommendation.confidence:.2f}"
        )

        return {
            "recommendation": recommendation,
            "execution_log": [
                log_entry + f" ✓ (action={recommendation.action_needed}, "
                f"confidence={recommendation.confidence:.2f})"
            ],
        }

    except Exception as e:
        logger.error(f"Synthesis LLM failed: {e}")
        # Fallback: assume action needed if we can't synthesize
        fallback = RecommendationDecision(
            action_needed=True,
            root_cause="Unable to determine root cause — synthesis failed",
            reasoning=f"Synthesis LLM failed: {e}. Proceeding to campaign generation as a precaution.",
            confidence=0.3,
        )
        return {
            "recommendation": fallback,
            "research_errors": [f"Synthesis LLM failed: {e}"],
            "execution_log": [log_entry + f" ✗ (fallback: action=True) — {e}"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Node 5: Campaign Generator
# ═══════════════════════════════════════════════════════════════════════════

def generate_campaign(state: ARICOState) -> dict:
    """Generate a localized marketing campaign based on all available data."""
    alert = state["alert"]
    metadata = state.get("store_metadata")
    recommendation = state.get("recommendation")
    log_entry = f"[{datetime.now().isoformat()}] Generating campaign for store {alert.store_id}"
    logger.info(log_entry)

    # Build comprehensive context
    context_parts = [f"Alert: {alert.model_dump_json(indent=2)}"]

    if metadata:
        context_parts.append(f"Store: {metadata.name}, {metadata.city} ({metadata.location_type})")

    if recommendation:
        context_parts.append(
            f"Root Cause Diagnosis: {recommendation.root_cause}\n"
            f"Confidence: {recommendation.confidence:.0%}\n"
            f"Reasoning: {recommendation.reasoning}"
        )

    for field, label in [
        ("sales_analysis", "Sales Trend Analysis"),
        ("competitor_analysis", "Competitor Intelligence"),
        ("inventory_analysis", "Inventory Analysis"),
        ("feedback_analysis", "Customer Feedback Analysis"),
    ]:
        report = state.get(field)
        if report:
            context_parts.append(
                f"{label}:\n"
                f"  Severity: {report.severity}\n"
                f"  Findings: {'; '.join(report.key_findings)}\n"
                f"  Summary: {report.summary}"
            )

    human_feedback = state.get("human_feedback")
    if human_feedback:
        context_parts.append(f"\nBRAND OWNER FEEDBACK (must be incorporated): {human_feedback}")

    errors = state.get("research_errors", [])
    if errors:
        context_parts.append(f"\nResearch Errors: {errors}")

    # Get product context for the target category
    available_skus = []
    max_discount = 0.20
    if metadata:
        for p in metadata.products:
            if p.category == alert.product_category:
                available_skus.append(p.sku)
        max_discount = metadata.max_allowable_discount_pct

    llm = _get_llm()
    system_prompt = prompts.get(
        "campaign_system",
        available_skus=available_skus,
        max_discount_pct=round(max_discount * 100),
    )

    iteration = state.get("iteration_count", 0)
    try:
        response = _with_retry(
            llm.with_config(
                run_name=f"Campaign Generator | {'Re-generation' if iteration > 0 else 'Initial'} (iter {iteration + 1})",
                tags=["campaign-generator", "re-generation" if iteration > 0 else "initial"],
                metadata={
                    "store_id": alert.store_id,
                    "iteration": iteration + 1,
                    "root_cause": recommendation.root_cause if recommendation else "unknown",
                    "has_human_feedback": human_feedback is not None,
                },
            ).invoke,
            [SystemMessage(content=system_prompt), HumanMessage(content="\n\n".join(context_parts))],
        )

        parsed = _parse_json(response.content)

        campaign = Campaign(
            campaign_name=parsed["campaign_name"],
            store_id=alert.store_id,
            product_category=alert.product_category,
            promotion_type=PromotionType(parsed["promotion_type"]),
            discount_pct=parsed.get("discount_pct"),
            promotion_details=parsed["promotion_details"],
            target_skus=parsed.get("target_skus", available_skus),
            channels=[CampaignChannel(c) for c in parsed["channels"]],
            duration_days=parsed.get("duration_days", 7),
            rationale=parsed["rationale"],
        )

        return {
            "proposed_campaign": campaign,
            "execution_log": [log_entry + f" ✓ ({campaign.campaign_name})"],
        }

    except Exception as e:
        logger.error(f"Campaign generation failed: {e}")
        fallback = Campaign(
            campaign_name=f"Emergency {alert.product_category.title()} Promotion - Store {alert.store_id}",
            store_id=alert.store_id,
            product_category=alert.product_category,
            promotion_type=PromotionType.PERCENTAGE_DISCOUNT,
            discount_pct=min(0.15, max_discount),
            promotion_details=f"15% off all {alert.product_category} to address sales decline",
            target_skus=available_skus or ["ALL"],
            channels=[CampaignChannel.IN_STORE_SIGNAGE, CampaignChannel.EMAIL_BLAST],
            duration_days=7,
            rationale=f"Fallback campaign — LLM generation failed: {e}",
        )
        return {
            "proposed_campaign": fallback,
            "research_errors": [f"Campaign LLM failed, using fallback: {e}"],
            "execution_log": [log_entry + f" ✗ (fallback) — {e}"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Node 6: Cost & ROI Calculator
# ═══════════════════════════════════════════════════════════════════════════

def calculate_cost_and_roi(state: ARICOState) -> dict:
    """Calculate the cost and expected ROI of the proposed campaign."""
    alert = state["alert"]
    campaign = state["proposed_campaign"]
    metadata = state.get("store_metadata")
    log_entry = f"[{datetime.now().isoformat()}] Calculating cost/ROI for '{campaign.campaign_name}'"
    logger.info(log_entry)

    # Margin cost from discounting
    margin_cost = 0.0
    if campaign.discount_pct and metadata:
        relevant = [p for p in metadata.products if p.category == alert.product_category]
        for product in relevant:
            units_to_sell = min(
                product.stock_units,
                alert.estimated_units_at_risk // max(1, len(relevant)),
            )
            margin_cost += units_to_sell * product.base_price * campaign.discount_pct

    # Marketing spend by channel
    channel_costs = {
        "in_store_signage": 150, "email_blast": 200, "social_media_geo": 500,
        "sms": 300, "push_notification": 100, "local_ad": 1000,
    }
    marketing_spend = sum(channel_costs.get(ch.value, 200) for ch in campaign.channels)
    total_cost = margin_cost + marketing_spend

    # Recovery estimate
    base_recovery = 0.3
    if campaign.discount_pct:
        recovery_rate = min(0.85, base_recovery + min(0.4, campaign.discount_pct * 2))
    else:
        recovery_rate = base_recovery

    estimated_revenue_recovered = alert.revenue_at_risk * recovery_rate
    estimated_roi = (
        (estimated_revenue_recovered - total_cost) / total_cost if total_cost > 0 else 0
    )

    # Risk assessment
    risk_factors = []
    cost_ratio = total_cost / alert.revenue_at_risk if alert.revenue_at_risk > 0 else 1.0

    if cost_ratio > 0.5:
        risk_factors.append("Campaign cost exceeds 50% of revenue at risk")
    if campaign.discount_pct and campaign.discount_pct > 0.20:
        risk_factors.append("Discount exceeds 20% — significant margin impact")
    if estimated_roi < 1.0:
        risk_factors.append("Expected ROI below 1.0x")
    if len(state.get("research_errors", [])) > 0:
        risk_factors.append("Some research data was unavailable")

    risk_level = "high" if len(risk_factors) >= 3 else ("medium" if risk_factors else "low")

    cost_estimate = CostEstimate(
        store_id=alert.store_id,
        campaign_name=campaign.campaign_name,
        marketing_spend=round(marketing_spend, 2),
        margin_cost=round(margin_cost, 2),
        total_cost=round(total_cost, 2),
        revenue_at_risk=alert.revenue_at_risk,
        estimated_revenue_recovered=round(estimated_revenue_recovered, 2),
        recovery_rate=round(recovery_rate, 4),
        estimated_roi=round(estimated_roi, 4),
        risk_level=risk_level,
        risk_factors=risk_factors,
    )

    requires_approval = not (
        cost_ratio <= config.AUTO_DEPLOY_COST_RATIO
        and estimated_roi >= config.AUTO_DEPLOY_MIN_ROI
        and risk_level == "low"
    )

    logger.info(
        f"Cost: ${total_cost:.2f}, ROI: {estimated_roi:.2f}x, "
        f"Risk: {risk_level}, Approval needed: {requires_approval}"
    )

    return {
        "cost_estimate": cost_estimate,
        "requires_approval": requires_approval,
        "execution_log": [
            log_entry + f" ✓ (cost=${total_cost:.2f}, ROI={estimated_roi:.2f}x, "
            f"risk={risk_level}, approval={'required' if requires_approval else 'auto'})"
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Node 7: Execute Promotion
# ═══════════════════════════════════════════════════════════════════════════

def execute_promotion(state: ARICOState) -> dict:
    """Deploy the approved campaign via the promotion tool."""
    campaign = state["proposed_campaign"]
    log_entry = f"[{datetime.now().isoformat()}] Deploying '{campaign.campaign_name}' to store {campaign.store_id}"
    logger.info(log_entry)

    try:
        result = _with_retry(
            deploy_promotion.invoke,
            {
                "store_id": campaign.store_id,
                "campaign_name": campaign.campaign_name,
                "channels": [c.value for c in campaign.channels],
                "discount_pct": campaign.discount_pct,
                "duration_days": campaign.duration_days,
            },
        )
        deployment = DeploymentResult(**result)
        return {
            "deployment_result": deployment,
            "execution_log": [log_entry + f" ✓ (promo_id={deployment.promotion_id})"],
        }
    except Exception as e:
        failed = DeploymentResult(status="failed", error=str(e))
        return {
            "deployment_result": failed,
            "research_errors": [f"Deployment failed: {e}"],
            "execution_log": [log_entry + f" ✗ ({e})"],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Node 8: Archive Rejected
# ═══════════════════════════════════════════════════════════════════════════

def archive_rejected(state: ARICOState) -> dict:
    """Archive a rejected campaign — log the decision and skip deployment."""
    campaign = state["proposed_campaign"]
    log_entry = (
        f"[{datetime.now().isoformat()}] Campaign '{campaign.campaign_name}' "
        f"rejected by brand owner. Archiving."
    )
    logger.info(log_entry)
    return {
        "deployment_result": DeploymentResult(
            status="skipped",
            error="Campaign rejected by brand owner",
        ),
        "execution_log": [log_entry],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Node 9: Generate Final Report
# ═══════════════════════════════════════════════════════════════════════════

def generate_final_report(state: ARICOState, config: RunnableConfig) -> dict:
    """Generate a final summary report of the entire ARICO workflow and persist it to the DB."""
    alert = state["alert"]
    metadata = state.get("store_metadata")
    recommendation = state.get("recommendation")
    campaign = state.get("proposed_campaign")
    cost = state.get("cost_estimate")
    deployment = state.get("deployment_result")
    assessment = state.get("situation_assessment")

    store_name = metadata.name if metadata else f"Store {alert.store_id}"

    report_lines = [
        "=" * 60,
        "ARICO FINAL REPORT",
        "=" * 60,
        f"Store: {alert.store_id} — {store_name}",
        f"Alert: {alert.loss_reason}",
        f"Revenue at Risk: ${alert.revenue_at_risk:,.2f}",
        "",
    ]

    if assessment:
        report_lines.extend([
            f"Assessment: {'Data Sufficient' if assessment.data_sufficient else 'Research Required'}",
            f"Agents Spawned: {[a.value for a in assessment.agents_to_spawn]}",
            "",
        ])

    if recommendation:
        action_str = "ACTION NEEDED" if recommendation.action_needed else "NO ACTION NEEDED"
        report_lines.extend([
            f"Diagnosis: [{action_str}]",
            f"Root Cause: {recommendation.root_cause}",
            f"Confidence: {recommendation.confidence:.0%}",
            f"Reasoning: {recommendation.reasoning}",
        ])
        if not recommendation.action_needed and recommendation.no_action_reason:
            report_lines.append(f"No Action Reason: {recommendation.no_action_reason}")
        report_lines.append("")

    if campaign:
        report_lines.extend([
            f"Campaign: {campaign.campaign_name}",
            f"Type: {campaign.promotion_type.value}",
            f"Discount: {campaign.discount_pct * 100:.0f}%" if campaign.discount_pct else "Discount: N/A",
            f"Channels: {[c.value for c in campaign.channels]}",
            f"Duration: {campaign.duration_days} days",
            "",
        ])

    if cost:
        report_lines.extend([
            f"Total Cost: ${cost.total_cost:,.2f}",
            f"Expected ROI: {cost.estimated_roi:.2f}x",
            f"Risk Level: {cost.risk_level}",
            "",
        ])

    if deployment:
        report_lines.extend([
            f"Deployment: {deployment.status}",
            f"Promotion ID: {deployment.promotion_id or 'N/A'}",
        ])
        if deployment.estimated_reach:
            report_lines.append(f"Estimated Reach: {deployment.estimated_reach:,}")
        report_lines.append("")

    errors = state.get("research_errors", [])
    if errors:
        report_lines.extend(["Errors:", *[f"  - {e}" for e in errors], ""])

    report_lines.append("=" * 60)
    report = "\n".join(report_lines)
    logger.info(f"\n{report}")

    # Determine action taken for the DB record
    if recommendation and not recommendation.action_needed:
        action_taken = "no_action"
    elif deployment and deployment.status == "deployed":
        action_taken = "deployed"
    elif deployment and deployment.status == "skipped":
        action_taken = "rejected"
    else:
        action_taken = "unknown"

    # Persist to reports table
    thread_id = (config.get("configurable") or {}).get("thread_id", alert.store_id)
    try:
        save_report(
            thread_id=thread_id,
            store_id=alert.store_id,
            action_taken=action_taken,
            report_text=report,
        )
        logger.info(f"Report saved to DB (thread_id={thread_id}, action={action_taken})")
    except Exception as e:
        logger.warning(f"Could not save report to DB: {e}")

    return {
        "final_report": report,
        "execution_log": [f"[{datetime.now().isoformat()}] Final report generated and saved (action={action_taken})"],
    }
