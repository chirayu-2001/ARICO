"""Read-only SQL query tool for ARICO analyst sub-agents."""
from __future__ import annotations

from langchain_core.tools import tool

from arico.db import get_connection


@tool
def run_sql_query(query: str) -> dict:
    """Execute a read-only SQL query against the ARICO retail database.

    Returns column names and all matching rows. Use this to investigate
    sales trends, inventory levels, competitor activity, and customer
    feedback. Only SELECT queries are allowed.

    Args:
        query: A valid SQLite SELECT statement.

    Returns:
        Dict with keys: columns (list[str]), rows (list[list]), row_count (int).
        On error, returns {"error": "...", "columns": [], "rows": [], "row_count": 0}.
    """
    normalized = query.strip().upper()
    if not normalized.startswith("SELECT"):
        return {
            "error": "Only SELECT queries are permitted",
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    try:
        conn = get_connection()
        cursor = conn.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:
        return {
            "error": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
