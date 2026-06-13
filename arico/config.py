"""ARICO configuration — thresholds, LLM settings, and runtime config."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# ── LLM ────────────────────────────────────────────────────────────────────
# Supported providers: "openai", "anthropic"
# Auto-detected from available API keys if not explicitly set.
LLM_PROVIDER = os.getenv("ARICO_LLM_PROVIDER", "").lower()
LLM_MODEL = os.getenv("ARICO_LLM_MODEL", "")
LLM_TEMPERATURE = float(os.getenv("ARICO_LLM_TEMPERATURE", "0.2"))


def _resolve_provider_and_model() -> tuple[str, str]:
    """Auto-detect provider and model from environment if not explicitly set."""
    provider = LLM_PROVIDER
    model = LLM_MODEL

    if provider and model:
        return provider, model

    # Auto-detect from available API keys
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    has_openai = bool(os.getenv("OPENAI_API_KEY"))

    if not provider:
        if has_anthropic:
            provider = "anthropic"
        elif has_openai:
            provider = "openai"
        else:
            # Default to openai, will fail at runtime if no key
            provider = "openai"

    if not model:
        if provider == "anthropic":
            model = "claude-sonnet-4-6"
        else:
            model = "gpt-4o-mini"

    return provider, model


RESOLVED_PROVIDER, RESOLVED_MODEL = _resolve_provider_and_model()

# ── HITL ───────────────────────────────────────────────────────────────────
MAX_HITL_ITERATIONS = int(os.getenv("ARICO_MAX_HITL_ITERATIONS", "3"))

# ── Auto-deploy thresholds ─────────────────────────────────────────────────
# Campaigns whose total_cost < (AUTO_DEPLOY_COST_RATIO × revenue_at_risk)
# AND estimated_roi > AUTO_DEPLOY_MIN_ROI are auto-deployed.
AUTO_DEPLOY_COST_RATIO = float(os.getenv("ARICO_AUTO_DEPLOY_COST_RATIO", "0.3"))
AUTO_DEPLOY_MIN_ROI = float(os.getenv("ARICO_AUTO_DEPLOY_MIN_ROI", "1.5"))

# ── SQLite ─────────────────────────────────────────────────────────────────
# Single file serves both retail data tables and LangGraph checkpoints.
# In production, swap SqliteSaver for PostgresSaver (langgraph-checkpoint-postgres).
SQLITE_DB_PATH = os.getenv("ARICO_DB_PATH", "arico.db")

# ── Tool reliability (for mock failure simulation) ─────────────────────────
TOOL_FAILURE_RATE = float(os.getenv("ARICO_TOOL_FAILURE_RATE", "0.0"))  # 0.0-1.0
TOOL_MAX_RETRIES = int(os.getenv("ARICO_TOOL_MAX_RETRIES", "2"))

# ── LangSmith ──────────────────────────────────────────────────────────────
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "arico")
