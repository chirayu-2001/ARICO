"""ARICO Main Entry Point.

Run the ARICO orchestration graph with sample alerts.
Demonstrates auto-deploy, human-approval, and no-action flows.

Usage:
    python -m arico.main
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

from langgraph.types import Command
from arico.graph.builder import build_graph, get_memory_checkpointer, get_sqlite_checkpointer
from arico.models.alerts import Alert
from arico.models.campaigns import ApprovalStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arico")


def _ensure_db_seeded() -> None:
    """Seed the database if it hasn't been seeded yet."""
    from arico.db import is_seeded, get_connection
    from arico.db import SCHEMA_DDL

    conn = get_connection()
    # Ensure tables exist
    for stmt in SCHEMA_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    if not is_seeded():
        logger.info("Database not seeded — running seed script...")
        from arico.db.seed import seed
        seed()
    else:
        logger.info("Database already seeded")


def load_alerts(path: str | None = None) -> list[Alert]:
    """Load alerts from a JSON file."""
    if path is None:
        path = str(Path(__file__).parent.parent / "data" / "alerts.json")
    with open(path) as f:
        return [Alert(**a) for a in json.load(f)]


def _build_initial_state(alert: Alert) -> dict:
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


def run_alert(graph, alert: Alert, thread_id: str | None = None) -> dict:
    """Run the ARICO graph for a single alert.

    Handles the HITL interrupt/resume loop interactively.
    """
    if thread_id is None:
        thread_id = str(uuid4())

    run_config = {
        "configurable": {"thread_id": thread_id},
        "run_name": f"ARICO | Store {alert.store_id} | ₹{alert.revenue_at_risk:,.0f} at risk",
        "tags": ["arico", f"store-{alert.store_id}", alert.product_category],
        "metadata": {
            "store_id": alert.store_id,
            "revenue_at_risk": alert.revenue_at_risk,
            "product_category": alert.product_category,
            "estimated_units_at_risk": alert.estimated_units_at_risk,
        },
    }

    print(f"\n{'='*60}")
    print(f"Processing Alert: Store {alert.store_id}")
    print(f"Loss Reason: {alert.loss_reason}")
    print(f"Revenue at Risk: ${alert.revenue_at_risk:,.2f}")
    print(f"Thread ID: {thread_id}")
    print(f"{'='*60}\n")

    result = None
    for event in graph.stream(_build_initial_state(alert), run_config, stream_mode="values"):
        result = event

    state = graph.get_state(run_config)

    while state.next:
        print("\n" + "="*60)
        print("HUMAN APPROVAL REQUIRED")
        print("="*60)

        if state.tasks:
            for task in state.tasks:
                if hasattr(task, 'interrupts') and task.interrupts:
                    print(json.dumps(task.interrupts[0].value, indent=2))

        print("\nOptions:")
        print("  1. Approve  — Deploy the campaign as-is")
        print("  2. Reject   — Cancel the campaign")
        print("  3. Modify   — Request changes")

        choice = input("\nYour choice (1/2/3): ").strip()

        if choice == "1":
            approval = {"status": "approved"}
        elif choice == "2":
            approval = {"status": "rejected"}
        elif choice == "3":
            feedback = input("Enter your modification feedback: ").strip()
            approval = {"status": "modified", "feedback": feedback}
        else:
            print("Invalid choice. Treating as rejection.")
            approval = {"status": "rejected"}

        print(f"\nResuming with: {json.dumps(approval)}\n")

        for event in graph.stream(
            Command(resume=approval),
            run_config,
            stream_mode="values",
        ):
            result = event

        state = graph.get_state(run_config)

    return result


def main():
    """Main entry point: process all sample alerts."""
    print("\n" + "=" * 60)
    print("  ARICO — Autonomous Retail Intervention & Campaign Orchestrator")
    print("=" * 60 + "\n")

    # Ensure DB is seeded
    _ensure_db_seeded()

    # Build graph with SQLite checkpointer (same file as retail data)
    import os
    if os.getenv("USE_SQLITE", "true").lower() != "false":
        checkpointer = get_sqlite_checkpointer()
        logger.info("Using SQLite checkpointer (arico.db)")
    else:
        checkpointer = get_memory_checkpointer()
        logger.info("Using in-memory checkpointer")

    graph = build_graph(checkpointer=checkpointer)

    # Load alerts
    alerts = load_alerts()
    print(f"Loaded {len(alerts)} alerts\n")

    for i, alert in enumerate(alerts):
        print(f"\n{'━'*60}")
        print(f"  Alert {i+1}/{len(alerts)}")
        print(f"{'━'*60}")

        try:
            result = run_alert(graph, alert)

            if result and "execution_log" in result:
                print("\nExecution Log:")
                for entry in result["execution_log"]:
                    print(f"  {entry}")

        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Exiting.")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Error processing alert for store {alert.store_id}: {e}", exc_info=True)
            print(f"\nError: {e}")

    print("\n\nAll alerts processed!")


if __name__ == "__main__":
    main()
