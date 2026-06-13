"""Run ARICO LangSmith evaluations.

Usage:
    uv run python -m evals.run                    # all evaluators
    uv run python -m evals.run --deterministic    # skip LLM-as-judge (fast, no API cost)
    uv run python -m evals.run --store 303        # single store only

Requires:
    ANTHROPIC_API_KEY or OPENAI_API_KEY
    LANGCHAIN_API_KEY
    LANGCHAIN_TRACING_V2=true
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("arico.evals")


def _build_target():
    """Build the ARICO graph target function for LangSmith evaluate()."""
    from arico.db import SCHEMA_DDL, get_connection, is_seeded
    from arico.db.seed import seed
    from arico.graph.builder import build_graph, get_memory_checkpointer
    from arico.models.alerts import Alert

    # Ensure DB is seeded
    conn = get_connection()
    for stmt in SCHEMA_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    if not is_seeded():
        seed()

    # Use in-memory checkpointer for evals — no persistence needed
    graph = build_graph(checkpointer=get_memory_checkpointer())

    def run_arico(inputs: dict) -> dict:
        """Run the ARICO graph and return fields relevant for evaluation."""
        alert = Alert(**inputs["alert"])
        thread_id = str(uuid4())

        initial_state = {
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

        run_config = {
            "configurable": {"thread_id": thread_id},
            "run_name": f"ARICO Eval | Store {alert.store_id}",
            "tags": ["arico", "eval", f"store-{alert.store_id}"],
            "metadata": {"store_id": alert.store_id, "eval": True},
        }

        result = None
        for event in graph.stream(initial_state, run_config, stream_mode="values"):
            result = event

        if result is None:
            return {"error": "Graph produced no output"}

        recommendation = result.get("recommendation")
        campaign = result.get("proposed_campaign")
        assessment = result.get("situation_assessment")

        return {
            "action_needed": recommendation.action_needed if recommendation else None,
            "root_cause": recommendation.root_cause if recommendation else None,
            "reasoning": recommendation.reasoning if recommendation else None,
            "confidence": recommendation.confidence if recommendation else None,
            "no_action_reason": recommendation.no_action_reason if recommendation else None,
            "campaign": json.dumps(campaign.model_dump(), indent=2) if campaign else None,
            "analysts_spawned": (
                [a.value for a in assessment.agents_to_spawn] if assessment else []
            ),
            "execution_log": result.get("execution_log", []),
        }

    return run_arico


def main():
    parser = argparse.ArgumentParser(description="Run ARICO LangSmith evaluations")
    parser.add_argument("--deterministic", action="store_true", help="Skip LLM-as-judge evaluators")
    parser.add_argument("--store", type=str, help="Evaluate a single store ID only")
    args = parser.parse_args()

    # Validate required env vars
    if not os.getenv("LANGCHAIN_API_KEY"):
        logger.error("LANGCHAIN_API_KEY not set — LangSmith evaluations require it")
        sys.exit(1)
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        logger.error("No LLM API key found — set ANTHROPIC_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    from langsmith import Client, evaluate
    from evals.dataset import DATASET_NAME, create_or_update_dataset
    from evals.evaluators import ALL_EVALUATORS, DETERMINISTIC_EVALUATORS

    client = Client()
    create_or_update_dataset(client)

    evaluators = DETERMINISTIC_EVALUATORS if args.deterministic else ALL_EVALUATORS

    # Optionally filter to a single store
    filter_expr = None
    if args.store:
        filter_expr = f'eq(metadata["store_id"], "{args.store}")'
        logger.info(f"Filtering evaluation to store {args.store}")

    logger.info(f"Running evaluation on dataset '{DATASET_NAME}' with {len(evaluators)} evaluator(s)...")
    logger.info(f"LLM-as-judge: {'disabled' if args.deterministic else 'enabled'}")

    target = _build_target()

    results = evaluate(
        target,
        data=DATASET_NAME,
        evaluators=evaluators,
        experiment_prefix="arico-eval",
        metadata={"version": "v2", "deterministic_only": args.deterministic},
        filter=filter_expr,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("  ARICO Evaluation Results")
    print("=" * 60)

    scores: dict[str, list[float]] = {}
    for result in results:
        for feedback in result.get("feedback", []):
            key = feedback.key
            scores.setdefault(key, []).append(feedback.score or 0)

    if scores:
        for metric, vals in sorted(scores.items()):
            avg = sum(vals) / len(vals)
            print(f"  {metric:<35} {avg:.0%}  ({sum(v == 1 for v in vals)}/{len(vals)} passed)")
    else:
        print("  No scored feedback found — check LangSmith dashboard for results")

    print("=" * 60)
    print(f"\nFull results: https://smith.langchain.com/")
    print("(Filter by project 'arico' and look for experiments prefixed 'arico-eval')\n")


if __name__ == "__main__":
    main()
