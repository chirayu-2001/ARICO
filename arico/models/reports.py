"""Report models for sub-agent outputs."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Situation Assessment
# ---------------------------------------------------------------------------

class AgentType(str, Enum):
    """Types of SQL-based analyst agents the orchestrator can spawn."""
    SALES_ANALYST = "sales_analyst"
    COMPETITOR_ANALYST = "competitor_analyst"
    INVENTORY_ANALYST = "inventory_analyst"
    FEEDBACK_ANALYST = "feedback_analyst"


class KnowledgeGap(str, Enum):
    """Specific knowledge gaps the orchestrator can identify."""
    SALES_TRENDS = "sales_trends"
    COMPETITOR_ACTIVITY = "competitor_activity"
    INVENTORY_SUPPLY = "inventory_supply"
    CUSTOMER_SENTIMENT = "customer_sentiment"


# Maps knowledge gaps to the sub-agent that fills them
GAP_TO_AGENT: dict[KnowledgeGap, AgentType] = {
    KnowledgeGap.SALES_TRENDS:       AgentType.SALES_ANALYST,
    KnowledgeGap.COMPETITOR_ACTIVITY: AgentType.COMPETITOR_ANALYST,
    KnowledgeGap.INVENTORY_SUPPLY:   AgentType.INVENTORY_ANALYST,
    KnowledgeGap.CUSTOMER_SENTIMENT: AgentType.FEEDBACK_ANALYST,
}


class SituationAssessment(BaseModel):
    """Structured output from the orchestrator's data-sufficiency assessment."""
    data_sufficient: bool = Field(
        ...,
        description="Whether the orchestrator has enough data to synthesize a recommendation directly",
    )
    knowledge_gaps: list[KnowledgeGap] = Field(
        default_factory=list,
        description="Specific knowledge gaps that need to be filled by analyst agents",
    )
    agents_to_spawn: list[AgentType] = Field(
        default_factory=list,
        description="Analyst agents to activate based on identified knowledge gaps",
    )
    reasoning: str = Field(
        ...,
        description="Explanation of why this assessment was made (for observability)",
    )


# ---------------------------------------------------------------------------
# Store Metadata (replaces InventorySnapshot — richer, from DB)
# ---------------------------------------------------------------------------

class ProductInfo(BaseModel):
    """Product + inventory info for one SKU at one store."""
    sku: str
    name: str
    category: str
    base_price: float = Field(..., ge=0)
    unit_margin_pct: float = Field(..., ge=0, le=1)
    stock_units: int = Field(..., ge=0)
    reorder_point: int = Field(..., ge=0)
    max_allowable_discount_pct: float = Field(..., ge=0, le=1)
    last_restock_date: str | None = None


class StoreMetadata(BaseModel):
    """Store context fetched on alert ingestion."""
    store_id: str
    name: str
    city: str
    state: str
    location_type: str
    opened_date: str
    avg_monthly_foot_traffic: int | None = None
    size_sqft: int | None = None
    products: list[ProductInfo] = Field(default_factory=list)

    @property
    def max_allowable_discount_pct(self) -> float:
        """Max discount across all products in the store."""
        if not self.products:
            return 0.20
        return max(p.max_allowable_discount_pct for p in self.products)


# ---------------------------------------------------------------------------
# Analyst Reports (generic — used by all 4 SQL-based sub-agents)
# ---------------------------------------------------------------------------

class AnalystReport(BaseModel):
    """Report from a SQL-based analyst sub-agent.

    All four analysts (sales, competitor, inventory, feedback) produce
    this same structure. The analyst_type field distinguishes them.
    The key_findings list carries the structured insights; summary
    carries the free-text narrative.
    """
    analyst_type: AgentType
    store_id: str
    queries_executed: list[str] = Field(
        default_factory=list,
        description="SQL queries that were run during the investigation",
    )
    key_findings: list[str] = Field(
        ...,
        description="Bullet-point findings from the data analysis",
    )
    summary: str = Field(
        ...,
        description="LLM-generated narrative summary of the analysis",
    )
    severity: Literal["none", "low", "medium", "high"] = Field(
        ...,
        description="How significant are the findings for explaining the sales loss?",
    )
