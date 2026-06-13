"""Store metadata lookup for ARICO.

Called directly (not as a LangChain tool) by the fetch_store_metadata node
to enrich the alert with store and inventory context before situation assessment.
"""
from __future__ import annotations

from arico.db import get_connection


def get_store_metadata(store_id: str) -> dict | None:
    """Pull store info and full inventory from the database.

    Returns:
        Dict with store fields + "products" list, or None if store not found.
    """
    conn = get_connection()

    row = conn.execute(
        "SELECT * FROM stores WHERE store_id = ?", (store_id,)
    ).fetchone()

    if row is None:
        return None

    store = dict(row)

    products = [
        dict(r) for r in conn.execute(
            """
            SELECT p.sku, p.name, p.category, p.base_price, p.unit_margin_pct,
                   i.stock_units, i.reorder_point, i.max_allowable_discount_pct,
                   i.last_restock_date
            FROM inventory i
            JOIN products p ON i.sku = p.sku
            WHERE i.store_id = ?
            ORDER BY p.category, p.sku
            """,
            (store_id,),
        ).fetchall()
    ]

    store["products"] = products
    return store
