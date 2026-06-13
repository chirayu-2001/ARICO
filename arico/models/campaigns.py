"""Campaign and cost models for ARICO."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PromotionType(str, Enum):
    """Types of promotional campaigns."""
    PERCENTAGE_DISCOUNT = "percentage_discount"
    BOGO = "buy_one_get_one"
    BUNDLE = "bundle_deal"
    FLASH_SALE = "flash_sale"
    LOYALTY_BONUS = "loyalty_bonus"


class CampaignChannel(str, Enum):
    """Marketing channels for campaign deployment."""
    IN_STORE_SIGNAGE = "in_store_signage"
    EMAIL_BLAST = "email_blast"
    SOCIAL_MEDIA_GEO = "social_media_geo"
    SMS = "sms"
    PUSH_NOTIFICATION = "push_notification"
    LOCAL_AD = "local_ad"


class Campaign(BaseModel):
    """A proposed marketing campaign to counter sales loss."""
    campaign_name: str = Field(..., description="Human-readable campaign name")
    store_id: str
    product_category: str
    promotion_type: PromotionType
    discount_pct: float | None = Field(
        None, ge=0, le=1, description="Discount percentage (0-1) if applicable"
    )
    promotion_details: str = Field(
        ..., description="Detailed description of the promotion"
    )
    target_skus: list[str] = Field(
        ..., description="SKUs targeted by this campaign"
    )
    channels: list[CampaignChannel] = Field(
        ..., description="Marketing channels to deploy through"
    )
    duration_days: int = Field(..., ge=1, le=90, description="Campaign duration in days")
    rationale: str = Field(
        ..., description="Why this campaign was chosen based on the research"
    )


class CostEstimate(BaseModel):
    """Cost and ROI breakdown for a proposed campaign."""
    store_id: str
    campaign_name: str
    # Costs
    marketing_spend: float = Field(..., ge=0, description="External marketing costs (ads, printing, etc.)")
    margin_cost: float = Field(
        ..., ge=0,
        description="Revenue lost due to discounting (units × price × discount)"
    )
    total_cost: float = Field(..., ge=0, description="Total campaign cost")
    # Revenue impact
    revenue_at_risk: float = Field(..., ge=0, description="Revenue we're trying to save")
    estimated_revenue_recovered: float = Field(
        ..., ge=0, description="Estimated revenue recovered by this campaign"
    )
    recovery_rate: float = Field(
        ..., ge=0, le=1, description="Fraction of at-risk revenue expected to recover"
    )
    # ROI
    estimated_roi: float = Field(
        ..., description="ROI = (recovered - cost) / cost. Can be negative."
    )
    # Risk assessment
    risk_level: Literal["low", "medium", "high"] = Field(
        ..., description="Overall risk level of the campaign"
    )
    risk_factors: list[str] = Field(
        default_factory=list, description="Specific risk factors identified"
    )


class ApprovalStatus(str, Enum):
    """Status of the human-in-the-loop approval."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


class HumanApproval(BaseModel):
    """Human approval decision with optional feedback."""
    status: ApprovalStatus
    feedback: str | None = Field(
        None,
        description="Optional feedback from brand owner, especially for 'modified' status"
    )
    approved_by: str = Field(default="brand_owner", description="Who made the decision")


class DeploymentResult(BaseModel):
    """Result of deploying a promotion."""
    status: Literal["deployed", "failed", "skipped"]
    promotion_id: str | None = None
    channels: list[str] = Field(default_factory=list)
    estimated_reach: int = 0
    start_date: str | None = None
    error: str | None = None
