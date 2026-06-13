"""Database connection manager for ARICO.

One SQLite file serves dual purpose:
- Retail mock data tables (stores, products, inventory, daily_sales, etc.)
- LangGraph checkpoint tables (created automatically by SqliteSaver)
"""
from __future__ import annotations

import sqlite3

from arico import config

_connection: sqlite3.Connection | None = None

# Full schema DDL — shared with analyst prompt templates for LLM context
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    location_type TEXT NOT NULL,
    opened_date TEXT NOT NULL,
    avg_monthly_foot_traffic INTEGER,
    size_sqft INTEGER
);

CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    base_price REAL NOT NULL,
    unit_margin_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    store_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    stock_units INTEGER NOT NULL,
    reorder_point INTEGER NOT NULL,
    max_allowable_discount_pct REAL NOT NULL,
    last_restock_date TEXT,
    PRIMARY KEY (store_id, sku)
);

CREATE TABLE IF NOT EXISTS daily_sales (
    store_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    sale_date TEXT NOT NULL,
    units_sold INTEGER NOT NULL,
    revenue REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    competitor_name TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    description TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT
);

CREATE TABLE IF NOT EXISTS customer_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL,
    sku TEXT,
    feedback_date TEXT NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT
);

CREATE TABLE IF NOT EXISTS monthly_benchmarks (
    store_id TEXT NOT NULL,
    category TEXT NOT NULL,
    month INTEGER NOT NULL,
    avg_daily_units REAL NOT NULL,
    avg_daily_revenue REAL NOT NULL,
    notes TEXT,
    PRIMARY KEY (store_id, category, month)
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    alert_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    run_config_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    """Get or create the shared SQLite connection to arico.db."""
    global _connection
    if _connection is None:
        db_path = config.SQLITE_DB_PATH
        _connection = sqlite3.connect(db_path, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


def close_connection() -> None:
    """Close the database connection."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


def upsert_thread(thread_id: str, store_id: str, alert_json: str, status: str, run_config_json: str) -> None:
    """Insert or update a thread record."""
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO threads (thread_id, store_id, alert_json, status, run_config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            status=excluded.status,
            run_config_json=excluded.run_config_json,
            updated_at=excluded.updated_at
        """,
        (thread_id, store_id, alert_json, status, run_config_json, now, now),
    )
    conn.commit()


def update_thread_status(thread_id: str, status: str) -> None:
    """Update the status of an existing thread."""
    import datetime
    now = datetime.datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE threads SET status=?, updated_at=? WHERE thread_id=?",
        (status, now, thread_id),
    )
    conn.commit()


def load_all_threads() -> list[dict]:
    """Load all thread records from the DB (used on server startup)."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT thread_id, store_id, alert_json, status, run_config_json FROM threads"
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []


def is_seeded() -> bool:
    """Return True if the retail data tables are already populated."""
    try:
        conn = get_connection()
        cursor = conn.execute("SELECT COUNT(*) FROM stores")
        return cursor.fetchone()[0] > 0
    except Exception:
        return False
