"""Alert input models for ARICO."""
from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    """Represents a loss alert from the retail analytics dashboard."""
    store_id: str = Field(..., description="Unique identifier for the store")
    loss_reason: str = Field(..., description="Human-readable description of why sales are being lost")
    revenue_at_risk: float = Field(..., ge=0, description="Estimated revenue at risk in USD")
    product_category: str = Field(..., description="Product category affected (e.g., 'shoes', 'apparel')")
    estimated_units_at_risk: int = Field(..., ge=0, description="Estimated number of units at risk")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "store_id": "101",
                    "loss_reason": "Store 101 is losing shoe sales",
                    "revenue_at_risk": 3500.0,
                    "product_category": "shoes",
                    "estimated_units_at_risk": 25,
                }
            ]
        }
    )
