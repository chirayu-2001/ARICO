"""Recommendation decision model for the synthesize_findings node."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RecommendationDecision(BaseModel):
    """Structured output from the synthesize_findings node.

    The synthesizer reads all analyst reports and decides whether an
    intervention is warranted at all — or whether the dip is noise.
    """

    action_needed: bool = Field(
        ...,
        description=(
            "True if a marketing/operational intervention is warranted. "
            "False if the dip is seasonal, statistical noise, or already resolving."
        ),
    )
    root_cause: str = Field(
        ...,
        description=(
            "One-sentence statement of the primary diagnosed cause "
            "(e.g. 'Competitor promo launched June 1 drove ~50% unit decline')."
        ),
    )
    reasoning: str = Field(
        ...,
        description=(
            "2-4 sentences synthesizing the analyst findings that support "
            "this decision. Must cite specific data points."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in the root cause diagnosis (0-1).",
    )
    no_action_reason: str | None = Field(
        None,
        description=(
            "If action_needed=False, explain why no intervention is warranted "
            "(e.g. 'Matches expected seasonal benchmark — normal June dip')."
        ),
    )
