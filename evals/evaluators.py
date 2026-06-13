"""Evaluators for ARICO LangSmith evaluation suite.

Mix of deterministic checks and LLM-as-judge evaluators:

Deterministic:
  - action_decision_correct  : did the agent correctly decide action_needed?
  - no_action_has_no_campaign: store 303 must not generate a campaign
  - root_cause_keywords      : does root_cause mention at least one expected keyword?

LLM-as-judge:
  - root_cause_accuracy      : judge whether the diagnosed root cause is correct
  - campaign_appropriateness : judge whether the campaign fits the root cause
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ── Deterministic evaluators ───────────────────────────────────────────────

def action_decision_correct(outputs: dict, reference_outputs: dict) -> dict:
    """Check whether action_needed matches the expected value."""
    actual = outputs.get("action_needed")
    expected = reference_outputs.get("expected_action_needed")

    if actual is None:
        return {"key": "action_decision_correct", "score": 0, "comment": "action_needed missing from output"}

    correct = actual == expected
    return {
        "key": "action_decision_correct",
        "score": int(correct),
        "comment": f"Expected action_needed={expected}, got {actual}",
    }


def no_action_has_no_campaign(outputs: dict, reference_outputs: dict) -> dict:
    """For no-action cases, verify no campaign was generated."""
    expected_action = reference_outputs.get("expected_action_needed")

    if expected_action is not False:
        # Only applies to no-action cases — skip for action cases
        return {"key": "no_action_has_no_campaign", "score": 1, "comment": "N/A — action expected for this store"}

    campaign = outputs.get("campaign")
    if campaign:
        return {
            "key": "no_action_has_no_campaign",
            "score": 0,
            "comment": f"Campaign was generated despite no action being needed: {campaign}",
        }

    return {"key": "no_action_has_no_campaign", "score": 1, "comment": "Correctly produced no campaign"}


def root_cause_keywords_present(outputs: dict, reference_outputs: dict) -> dict:
    """Check whether at least one expected keyword appears in the root cause."""
    root_cause = (outputs.get("root_cause") or "").lower()
    keywords = reference_outputs.get("expected_root_cause_keywords", [])

    if not root_cause:
        return {"key": "root_cause_keywords_present", "score": 0, "comment": "root_cause is empty"}

    matched = [kw for kw in keywords if kw.lower() in root_cause]
    score = 1 if matched else 0

    return {
        "key": "root_cause_keywords_present",
        "score": score,
        "comment": f"Matched keywords: {matched}" if matched else f"None of {keywords} found in: '{root_cause[:120]}'",
    }


def confidence_above_threshold(outputs: dict, reference_outputs: dict) -> dict:
    """For unambiguous cases (clear stockout, clear competitor event), confidence should be ≥ 0.7."""
    confidence = outputs.get("confidence")
    if confidence is None:
        return {"key": "confidence_above_threshold", "score": 0, "comment": "confidence missing from output"}

    threshold = 0.7
    score = 1 if confidence >= threshold else 0
    return {
        "key": "confidence_above_threshold",
        "score": score,
        "comment": f"Confidence: {confidence:.2f} (threshold: {threshold})",
    }


# ── LLM-as-judge evaluators ────────────────────────────────────────────────

def _judge_with_llm(prompt: str) -> tuple[int, str]:
    """Call Claude to judge an output. Returns (score 0/1, reasoning)."""
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # cheap model for evals
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
            system=(
                "You are an evaluator for a retail AI system. "
                "Respond with exactly two lines:\n"
                "SCORE: 1 (if correct/appropriate) or 0 (if wrong/inappropriate)\n"
                "REASON: one sentence explaining the score"
            ),
        )
        text = response.content[0].text.strip()
        score_line = next((l for l in text.splitlines() if l.startswith("SCORE:")), "SCORE: 0")
        reason_line = next((l for l in text.splitlines() if l.startswith("REASON:")), "REASON: unknown")
        score = int(score_line.split(":")[-1].strip())
        reason = reason_line.split(":", 1)[-1].strip()
        return score, reason
    except Exception as e:
        logger.warning(f"LLM judge call failed: {e}")
        return 0, f"Judge failed: {e}"


def root_cause_accuracy(outputs: dict, reference_outputs: dict) -> dict:
    """LLM-as-judge: is the diagnosed root cause actually correct?"""
    root_cause = outputs.get("root_cause", "")
    reasoning = outputs.get("reasoning", "")
    expected_hint = reference_outputs.get("expected_campaign_hint", "")
    store_name = reference_outputs.get("store_name", "")
    keywords = reference_outputs.get("expected_root_cause_keywords", [])

    prompt = f"""You are evaluating an AI agent that diagnosed the root cause of a retail store's sales drop.

Store: {store_name}
Expected root cause (ground truth): The correct cause involves these concepts: {keywords}
Context: {expected_hint}

Agent's diagnosed root cause: "{root_cause}"
Agent's reasoning: "{reasoning}"

Is the agent's root cause diagnosis correct? It should identify the right underlying reason for the sales drop."""

    score, reason = _judge_with_llm(prompt)
    return {"key": "root_cause_accuracy", "score": score, "comment": reason}


def campaign_appropriateness(outputs: dict, reference_outputs: dict) -> dict:
    """LLM-as-judge: is the campaign type appropriate for the diagnosed root cause?"""
    action_needed = outputs.get("action_needed")

    if not action_needed:
        # No campaign expected — skip this evaluator
        return {"key": "campaign_appropriateness", "score": 1, "comment": "N/A — no action case, no campaign expected"}

    campaign = outputs.get("campaign")
    if not campaign:
        return {"key": "campaign_appropriateness", "score": 0, "comment": "Campaign expected but none generated"}

    expected_hint = reference_outputs.get("expected_campaign_hint", "")
    store_name = reference_outputs.get("store_name", "")

    prompt = f"""You are evaluating an AI agent that generated a retail marketing campaign.

Store: {store_name}
What the campaign should address: {expected_hint}

Campaign generated by the agent:
{campaign}

Is this campaign appropriate and well-targeted for the problem described? Consider:
- Does it address the actual root cause?
- Is the approach (discount, restock, loyalty, differentiation) sensible given the situation?
- Would this campaign plausibly help recover the revenue at risk?"""

    score, reason = _judge_with_llm(prompt)
    return {"key": "campaign_appropriateness", "score": score, "comment": reason}


# ── Evaluator registry ─────────────────────────────────────────────────────

DETERMINISTIC_EVALUATORS = [
    action_decision_correct,
    no_action_has_no_campaign,
    root_cause_keywords_present,
    confidence_above_threshold,
]

LLM_JUDGE_EVALUATORS = [
    root_cause_accuracy,
    campaign_appropriateness,
]

ALL_EVALUATORS = DETERMINISTIC_EVALUATORS + LLM_JUDGE_EVALUATORS
