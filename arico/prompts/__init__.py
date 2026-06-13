"""Prompt loader for ARICO.

Reads prompt templates from .txt files in this directory.
Files are cached in memory after first load.

Usage:
    from arico.prompts import get

    # Static prompt
    system_msg = get("orchestrator_agent")

    # Prompt with variables
    system_msg = get("analyst_sales", store_id="101", schema=SCHEMA_DDL)
"""
from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent
_cache: dict[str, str] = {}


def get(name: str, **kwargs) -> str:
    """Load a prompt template by name, with optional variable substitution.

    Args:
        name: Filename without extension (e.g. "orchestrator_agent").
        **kwargs: Variables to substitute into the template using str.format().

    Returns:
        The prompt string, stripped of leading/trailing whitespace.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    if name not in _cache:
        path = _PROMPTS_DIR / f"{name}.txt"
        _cache[name] = path.read_text(encoding="utf-8")

    template = _cache[name]
    return template.format(**kwargs).strip() if kwargs else template.strip()
