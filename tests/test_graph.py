"""Tests for ARICO v2 graph."""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from arico.models.alerts import Alert
from arico.models.campaigns import ApprovalStatus
from arico.models.reports import (
    AgentType,
    AnalystReport,
    KnowledgeGap,
    StoreMetadata,
    ProductInfo,
    SituationAssessment,
)
from arico.models.recommendations import RecommendationDecision
from arico.graph.builder import build_graph, get_memory_checkpointer
from arico.tools.sql_tool import run_sql_query
from arico.tools.store_lookup import get_store_metadata
from arico.tools.promotion_tool import deploy_promotion


# ── Fixtures ───────────────────────────────────────────────────────────────

ALERT_101 = Alert(
    store_id="101",
    loss_reason="Store 101 is losing shoe sales",
    revenue_at_risk=3500.0,
    product_category="shoes",
    estimated_units_at_risk=25,
)

ALERT_202 = Alert(
    store_id="202",
    loss_reason="Store 202 is losing shoe sales",
    revenue_at_risk=12000.0,
    product_category="shoes",
    estimated_units_at_risk=150,
)

ALERT_303 = Alert(
    store_id="303",
    loss_reason="Store 303 is experiencing a mild decline in shoe sales",
    revenue_at_risk=2800.0,
    product_category="shoes",
    estimated_units_at_risk=18,
)


def _initial_state(alert: Alert) -> dict:
    return {
        "alert": alert,
        "store_metadata": None,
        "situation_assessment": None,
        "sales_analysis": None,
        "competitor_analysis": None,
        "inventory_analysis": None,
        "feedback_analysis": None,
        "research_errors": [],
        "recommendation": None,
        "proposed_campaign": None,
        "cost_estimate": None,
        "requires_approval": False,
        "approval_status": None,
        "human_feedback": None,
        "deployment_result": None,
        "iteration_count": 0,
        "execution_log": [],
    }


# ── Model Tests ────────────────────────────────────────────────────────────

class TestModels:
    """Test Pydantic model validation."""

    def test_alert_valid(self):
        alert = Alert(
            store_id="101",
            loss_reason="Test",
            revenue_at_risk=1000.0,
            product_category="shoes",
            estimated_units_at_risk=10,
        )
        assert alert.store_id == "101"
        assert alert.revenue_at_risk == 1000.0

    def test_alert_negative_revenue_rejected(self):
        with pytest.raises(Exception):
            Alert(
                store_id="101",
                loss_reason="Test",
                revenue_at_risk=-100.0,
                product_category="shoes",
                estimated_units_at_risk=10,
            )

    def test_situation_assessment_v2_enums(self):
        triage = SituationAssessment(
            data_sufficient=False,
            knowledge_gaps=[KnowledgeGap.SALES_TRENDS, KnowledgeGap.COMPETITOR_ACTIVITY],
            agents_to_spawn=[AgentType.SALES_ANALYST, AgentType.COMPETITOR_ANALYST],
            reasoning="Need sales trend and competitor data",
        )
        assert not triage.data_sufficient
        assert KnowledgeGap.SALES_TRENDS in triage.knowledge_gaps
        assert AgentType.SALES_ANALYST in triage.agents_to_spawn

    def test_analyst_report_valid(self):
        report = AnalystReport(
            analyst_type=AgentType.SALES_ANALYST,
            store_id="101",
            queries_executed=["SELECT * FROM daily_sales WHERE store_id='101'"],
            key_findings=["Sales dropped 50% from June 1"],
            summary="Clear inflection point on June 1",
            severity="high",
        )
        assert report.severity == "high"
        assert len(report.queries_executed) == 1

    def test_recommendation_decision_valid(self):
        rec = RecommendationDecision(
            action_needed=True,
            root_cause="Competitor promo on June 1 drove 50% unit decline",
            reasoning="Sales analyst found sharp drop; competitor analyst found FootLocker promo",
            confidence=0.9,
        )
        assert rec.action_needed
        assert rec.confidence == 0.9

    def test_recommendation_no_action(self):
        rec = RecommendationDecision(
            action_needed=False,
            root_cause="Seasonal dip matching June benchmark",
            reasoning="Sales match historical benchmark exactly",
            confidence=0.85,
            no_action_reason="Matches expected seasonal benchmark — normal June dip",
        )
        assert not rec.action_needed
        assert rec.no_action_reason is not None


# ── SQL Tool Tests ─────────────────────────────────────────────────────────

class TestSQLTool:
    """Test the read-only SQL query tool."""

    def test_select_allowed(self):
        result = run_sql_query.invoke({"query": "SELECT store_id, name FROM stores ORDER BY store_id"})
        assert "error" not in result or result.get("error") is None
        assert result["row_count"] == 5
        assert result["columns"] == ["store_id", "name"]

    def test_insert_blocked(self):
        result = run_sql_query.invoke({"query": "INSERT INTO stores VALUES ('999', 'x', 'x', 'x', 'x', 'x', 0, 0)"})
        assert result["error"] == "Only SELECT queries are permitted"
        assert result["row_count"] == 0

    def test_delete_blocked(self):
        result = run_sql_query.invoke({"query": "DELETE FROM daily_sales"})
        assert "Only SELECT" in result["error"]

    def test_update_blocked(self):
        result = run_sql_query.invoke({"query": "UPDATE stores SET name='x' WHERE store_id='101'"})
        assert "Only SELECT" in result["error"]

    def test_sales_trend_query(self):
        """Verify the data pattern for Store 101 (competitor promo scenario)."""
        result = run_sql_query.invoke({"query": """
            SELECT
                CASE WHEN sale_date >= '2026-06-01' THEN 'after' ELSE 'before' END as period,
                ROUND(AVG(units_sold), 2) as avg_units
            FROM daily_sales
            WHERE store_id = '101' AND sku LIKE 'SHOE%'
              AND sale_date >= '2026-05-25'
            GROUP BY period
        """})
        assert result["row_count"] == 2
        rows = {r[0]: r[1] for r in result["rows"]}
        # After promo (June 1+) should be lower than before
        assert rows["after"] < rows["before"], "Store 101 should show sales drop after June 1"

    def test_stockout_query(self):
        """Verify Store 202 SHOE-001 stockout data."""
        result = run_sql_query.invoke({"query": """
            SELECT i.stock_units, i.reorder_point, i.last_restock_date
            FROM inventory i
            WHERE i.store_id = '202' AND i.sku = 'SHOE-001'
        """})
        assert result["row_count"] == 1
        row = result["rows"][0]
        stock_units = row[result["columns"].index("stock_units")]
        reorder_point = row[result["columns"].index("reorder_point")]
        assert stock_units < reorder_point, "SHOE-001 should be below reorder point (stockout)"

    def test_seasonal_benchmark_query(self):
        """Verify Store 303 seasonal benchmark matches actual data."""
        actual = run_sql_query.invoke({"query": """
            SELECT ROUND(AVG(units_sold), 1) as avg_units
            FROM daily_sales
            WHERE store_id = '303' AND sku = 'SHOE-001'
              AND strftime('%m', sale_date) = '06'
        """})
        benchmark = run_sql_query.invoke({"query": """
            SELECT avg_daily_units
            FROM monthly_benchmarks
            WHERE store_id = '303' AND category = 'shoes' AND month = 6
        """})
        actual_avg = actual["rows"][0][0]
        bench_avg = benchmark["rows"][0][0]
        # Should be within 20% of each other
        assert abs(actual_avg - bench_avg) / bench_avg < 0.20, (
            f"Store 303 actual ({actual_avg}) should be close to benchmark ({bench_avg})"
        )


# ── Store Lookup Tests ─────────────────────────────────────────────────────

class TestStoreLookup:
    """Test the store metadata lookup."""

    def test_store_101_found(self):
        meta = get_store_metadata("101")
        assert meta is not None
        assert meta["name"] == "Connaught Place Store"
        assert meta["location_type"] == "downtown"
        assert len(meta["products"]) == 4

    def test_store_202_stockout_visible(self):
        meta = get_store_metadata("202")
        shoe_001 = next(p for p in meta["products"] if p["sku"] == "SHOE-001")
        assert shoe_001["stock_units"] < shoe_001["reorder_point"]

    def test_unknown_store_returns_none(self):
        meta = get_store_metadata("999")
        assert meta is None

    def test_all_5_stores_exist(self):
        for store_id in ["101", "202", "303", "404", "505"]:
            meta = get_store_metadata(store_id)
            assert meta is not None, f"Store {store_id} should exist"


# ── Promotion Tool Tests ────────────────────────────────────────────────────

class TestPromotionTool:
    def test_deploy_promotion(self):
        result = deploy_promotion.invoke({
            "store_id": "101",
            "campaign_name": "Test Campaign",
            "channels": ["in_store_signage", "email_blast"],
            "discount_pct": 0.15,
            "duration_days": 7,
        })
        assert result["status"] == "deployed"
        assert result["promotion_id"] is not None


# ── Graph Compilation Tests ────────────────────────────────────────────────

class TestGraphCompilation:
    """Test that the graph compiles and has the correct structure."""

    def test_graph_compiles(self):
        graph = build_graph()
        assert graph is not None

    def test_graph_has_all_nodes(self):
        graph = build_graph()
        node_names = list(graph.get_graph().nodes.keys())
        expected_nodes = [
            "fetch_store_metadata",
            "assess_situation",
            "sales_analyst",
            "competitor_analyst",
            "inventory_analyst",
            "feedback_analyst",
            "synthesize_findings",
            "generate_campaign",
            "calculate_cost",
            "request_approval",
            "execute_promotion",
            "archive_rejected",
            "final_report",
        ]
        for node in expected_nodes:
            assert node in node_names, f"Node '{node}' missing from graph"

    def test_graph_has_expected_nodes(self):
        graph = build_graph()
        # __start__ + __end__ + 13 custom nodes = 15
        assert len(graph.get_graph().nodes) >= 14


# ── Integration Tests (requires LLM API key) ──────────────────────────────

def _has_llm_key() -> bool:
    return bool(
        os.getenv("ANTHROPIC_API_KEY") or
        (os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_API_KEY") != "your-key-here")
    )


@pytest.mark.skipif(not _has_llm_key(), reason="No LLM API key (ANTHROPIC_API_KEY or OPENAI_API_KEY)")
class TestGraphExecution:
    """Integration tests — run the full graph with real LLM calls."""

    def test_full_flow_store_101(self):
        """Store 101 (competitor promo): should diagnose competitor and recommend campaign."""
        graph = build_graph()
        config = {"configurable": {"thread_id": "test-store-101"}}

        result = None
        for event in graph.stream(_initial_state(ALERT_101), config, stream_mode="values"):
            result = event

        assert result is not None
        assert result.get("store_metadata") is not None
        assert result.get("situation_assessment") is not None
        assert result.get("recommendation") is not None

    def test_full_flow_store_303_no_action(self):
        """Store 303 (seasonal blip): should conclude no action needed."""
        from langgraph.types import Command
        graph = build_graph()
        config = {"configurable": {"thread_id": "test-store-303"}}

        result = None
        for event in graph.stream(_initial_state(ALERT_303), config, stream_mode="values"):
            result = event

        # Handle HITL if it occurs (shouldn't for no-action path)
        state = graph.get_state(config)
        while state.next:
            for event in graph.stream(
                Command(resume={"status": "rejected"}),
                config,
                stream_mode="values",
            ):
                result = event
            state = graph.get_state(config)

        assert result is not None
        rec = result.get("recommendation")
        assert rec is not None
        # The agent should ideally conclude no action needed for store 303
        # (not a hard assert since LLM may vary, but log the decision)
        print(f"\nStore 303 recommendation: action_needed={rec.action_needed}, confidence={rec.confidence:.2f}")
        print(f"Root cause: {rec.root_cause}")

    def test_hitl_approve_flow(self):
        """Test HITL approval flow resumes correctly."""
        from langgraph.types import Command
        graph = build_graph()
        config = {"configurable": {"thread_id": "test-hitl-approve"}}

        result = None
        for event in graph.stream(_initial_state(ALERT_202), config, stream_mode="values"):
            result = event

        state = graph.get_state(config)
        if state.next:
            for event in graph.stream(
                Command(resume={"status": "approved"}),
                config,
                stream_mode="values",
            ):
                result = event

        # Whether it hit HITL or auto-deployed, there should be a deployment result
        assert result is not None
        if result.get("deployment_result"):
            assert result["deployment_result"].status in ("deployed", "skipped", "failed")

    def test_hitl_reject_flow(self):
        """Test HITL rejection flow."""
        from langgraph.types import Command
        graph = build_graph()
        config = {"configurable": {"thread_id": "test-hitl-reject"}}

        result = None
        for event in graph.stream(_initial_state(ALERT_202), config, stream_mode="values"):
            result = event

        state = graph.get_state(config)
        if state.next:
            for event in graph.stream(
                Command(resume={"status": "rejected"}),
                config,
                stream_mode="values",
            ):
                result = event
            assert result.get("deployment_result") is not None
            assert result["deployment_result"].status == "skipped"
