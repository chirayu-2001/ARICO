"""ARICO FastAPI Server.

Provides REST API endpoints for:
- Submitting alerts
- Inspecting graph state
- Resuming paused graphs (HITL approval)
- Listing active threads
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langgraph.types import Command

import os
from dotenv import load_dotenv

load_dotenv()

from arico.graph.builder import build_graph, get_memory_checkpointer, get_sqlite_checkpointer
from arico.models.alerts import Alert
from arico.models.campaigns import ApprovalStatus
from arico.db import load_all_threads, upsert_thread, update_thread_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("arico.api")


def _ensure_db_seeded() -> None:
    """Seed the database if it hasn't been seeded yet."""
    from arico.db import is_seeded, get_connection, SCHEMA_DDL

    conn = get_connection()
    for stmt in SCHEMA_DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    if not is_seeded():
        from arico.db.seed import seed
        seed()


_ensure_db_seeded()

# ── App Setup ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="ARICO API",
    description="Autonomous Retail Intervention & Campaign Orchestrator",
    version="2.0.0",
)

_checkpointer = get_sqlite_checkpointer()
logger.info("API: using SQLite checkpointer (arico.db)")

_graph = build_graph(checkpointer=_checkpointer)

# Thread registry — loaded from DB on startup, written through on every change
_active_threads: dict[str, dict] = {}

def _load_threads_from_db() -> None:
    """Restore thread registry from DB after a restart."""
    for row in load_all_threads():
        _active_threads[row["thread_id"]] = {
            "alert": json.loads(row["alert_json"]),
            "paused": row["status"] == "paused",
            "run_config": json.loads(row["run_config_json"]) if row["run_config_json"] else {},
        }
    if _active_threads:
        logger.info(f"Restored {len(_active_threads)} thread(s) from DB")

_load_threads_from_db()


# ── Request/Response Models ────────────────────────────────────────────────

class SubmitAlertRequest(BaseModel):
    alert: Alert
    thread_id: str | None = Field(None, description="Custom thread ID (auto-generated if not provided)")


class SubmitAlertResponse(BaseModel):
    thread_id: str
    status: str
    message: str
    requires_approval: bool = False
    interrupt_data: dict | None = None


class ApprovalRequest(BaseModel):
    status: ApprovalStatus
    feedback: str | None = None


class ApprovalResponse(BaseModel):
    thread_id: str
    status: str
    message: str
    requires_approval: bool = False
    interrupt_data: dict | None = None


class ThreadState(BaseModel):
    thread_id: str
    current_node: list[str] | None
    alert: dict | None
    situation_assessment: dict | None
    recommendation: dict | None
    proposed_campaign: dict | None
    cost_estimate: dict | None
    deployment_result: dict | None
    approval_status: str | None
    execution_log: list[str]
    is_paused: bool


# ── Helper ─────────────────────────────────────────────────────────────────

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


def _run_graph(thread_id: str, input_data: dict) -> SubmitAlertResponse:
    """Run or resume the graph, returning status and any interrupt data."""
    run_config = _active_threads.get(thread_id, {}).get("run_config", {})
    config = {"configurable": {"thread_id": thread_id}, **run_config}

    result = None
    for event in _graph.stream(input_data, config, stream_mode="values"):
        result = event

    state = _graph.get_state(config)
    if state.next:
        interrupt_data = None
        if state.tasks:
            for task in state.tasks:
                if hasattr(task, 'interrupts') and task.interrupts:
                    interrupt_data = task.interrupts[0].value

        _active_threads[thread_id]["paused"] = True
        update_thread_status(thread_id, "paused")
        return SubmitAlertResponse(
            thread_id=thread_id,
            status="paused",
            message="Campaign requires human approval. Use POST /threads/{thread_id}/approve to respond.",
            requires_approval=True,
            interrupt_data=interrupt_data,
        )

    _active_threads[thread_id]["paused"] = False
    update_thread_status(thread_id, "completed")
    return SubmitAlertResponse(
        thread_id=thread_id,
        status="completed",
        message="Alert processed successfully",
    )


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.post("/alerts", response_model=SubmitAlertResponse)
async def submit_alert(request: SubmitAlertRequest):
    """Submit a loss alert for processing."""
    thread_id = request.thread_id or str(uuid4())

    run_config = {
        "run_name": f"ARICO | Store {request.alert.store_id} | ₹{request.alert.revenue_at_risk:,.0f} at risk",
        "tags": ["arico", f"store-{request.alert.store_id}", request.alert.product_category],
        "metadata": {
            "store_id": request.alert.store_id,
            "revenue_at_risk": request.alert.revenue_at_risk,
            "product_category": request.alert.product_category,
        },
    }
    _active_threads[thread_id] = {
        "alert": request.alert.model_dump(),
        "paused": False,
        "run_config": run_config,
    }
    upsert_thread(
        thread_id=thread_id,
        store_id=request.alert.store_id,
        alert_json=request.alert.model_dump_json(),
        status="active",
        run_config_json=json.dumps(run_config),
    )

    try:
        return await asyncio.to_thread(
            _run_graph, thread_id, _build_initial_state(request.alert)
        )
    except Exception as e:
        logger.error(f"Error processing alert: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/threads/{thread_id}/approve", response_model=ApprovalResponse)
async def approve_campaign(thread_id: str, request: ApprovalRequest):
    """Approve, reject, or modify a campaign waiting for human approval."""
    if thread_id not in _active_threads:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

    if not _active_threads[thread_id].get("paused"):
        raise HTTPException(status_code=400, detail=f"Thread {thread_id} is not waiting for approval")

    config = {"configurable": {"thread_id": thread_id}}
    approval_data = {"status": request.status.value, "feedback": request.feedback}

    def _resume():
        result = None
        for event in _graph.stream(Command(resume=approval_data), config, stream_mode="values"):
            result = event
        return result

    try:
        await asyncio.to_thread(_resume)

        state = _graph.get_state(config)
        if state.next:
            interrupt_data = None
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        interrupt_data = task.interrupts[0].value

            return ApprovalResponse(
                thread_id=thread_id,
                status="paused",
                message="Modified campaign requires re-approval",
                requires_approval=True,
                interrupt_data=interrupt_data,
            )

        _active_threads[thread_id]["paused"] = False
        final_status = "rejected" if request.status.value == "rejected" else "completed"
        update_thread_status(thread_id, final_status)
        return ApprovalResponse(
            thread_id=thread_id,
            status="completed",
            message=f"Campaign {request.status.value}",
        )

    except Exception as e:
        logger.error(f"Error during approval: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/threads/{thread_id}", response_model=ThreadState)
async def get_thread_state(thread_id: str):
    """Inspect the current state of a processing thread."""
    config = {"configurable": {"thread_id": thread_id}}

    try:
        state = _graph.get_state(config)
        if not state.values:
            raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")

        values = state.values
        return ThreadState(
            thread_id=thread_id,
            current_node=list(state.next) if state.next else None,
            alert=values.get("alert").model_dump() if values.get("alert") else None,
            situation_assessment=values.get("situation_assessment").model_dump() if values.get("situation_assessment") else None,
            recommendation=values.get("recommendation").model_dump() if values.get("recommendation") else None,
            proposed_campaign=values.get("proposed_campaign").model_dump() if values.get("proposed_campaign") else None,
            cost_estimate=values.get("cost_estimate").model_dump() if values.get("cost_estimate") else None,
            deployment_result=values.get("deployment_result").model_dump() if values.get("deployment_result") else None,
            approval_status=values.get("approval_status").value if values.get("approval_status") else None,
            execution_log=values.get("execution_log", []),
            is_paused=bool(state.next),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting thread state: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/threads")
async def list_threads():
    """List all active/recent processing threads."""
    return {
        "threads": [
            {
                "thread_id": tid,
                "store_id": meta["alert"].get("store_id"),
                "paused": meta.get("paused", False),
            }
            for tid, meta in _active_threads.items()
        ]
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "arico", "version": "2.0.0"}
