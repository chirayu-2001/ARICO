"""ARICO database seed script.

Generates realistic mock data for 5 retail stores, each with a distinct
root cause baked into the data patterns. The orchestrator's sub-agents
must *discover* these causes through SQL analysis.

Run: python -m arico.db.seed
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from arico.db import SCHEMA_DDL, get_connection, close_connection


# ── Date range ──────────────────────────────────────────────────────────────
START_DATE = date(2026, 3, 14)   # 90 days of history
END_DATE = date(2026, 6, 11)     # "today" per alerts

# Key event dates
COMPETITOR_101_PROMO_DATE = date(2026, 6, 1)    # Store 101: Metro Shoes launches 20% off
STOCKOUT_202_DATE = date(2026, 5, 28)           # Store 202: SHOE-001 goes out of stock
SEASONAL_303_DIP_DATE = date(2026, 5, 15)       # Store 303: pre-monsoon slowdown begins
QUALITY_404_START_DATE = date(2026, 5, 1)       # Store 404: bad product batch, reviews start
COMPETITOR_505_OPEN_DATE = date(2026, 4, 28)    # Store 505: Decathlon opens nearby


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


# ── Static reference data ────────────────────────────────────────────────────

STORES = [
    ("101", "Connaught Place Store",   "New Delhi",  "DL", "downtown",          "2019-03-15", 1400, 2800),
    ("202", "Phoenix Palladium Outlet","Mumbai",     "MH", "mall",               "2020-06-01", 4200, 4500),
    ("303", "Indiranagar Store",       "Bengaluru",  "KA", "suburban",           "2018-09-20",  900, 3200),
    ("404", "Anna Nagar Store",        "Chennai",    "TN", "university_district","2021-02-10", 1600, 2100),
    ("505", "South City Mall Store",   "Kolkata",    "WB", "mall",               "2020-11-15", 2400, 3800),
]

PRODUCTS = [
    ("SHOE-001", "Classic Runner", "shoes",   5999.0, 0.35),
    ("SHOE-002", "Trail Blazer",   "shoes",   7999.0, 0.32),
    ("SHOE-003", "Urban Walker",   "shoes",   6999.0, 0.33),
    ("APP-100",  "DryFit Tee",     "apparel", 1999.0, 0.40),
]

# (store_id, sku, stock_units, reorder_point, max_discount, last_restock)
INVENTORY = [
    # Store 101 — normal levels
    ("101", "SHOE-001",  45, 20, 0.20, "2026-05-20"),
    ("101", "SHOE-002",  30, 15, 0.20, "2026-05-18"),
    ("101", "SHOE-003",  25, 12, 0.20, "2026-05-22"),
    ("101", "APP-100",  120, 40, 0.25, "2026-05-25"),
    # Store 202 — SHOE-001 critically low (stockout scenario)
    ("202", "SHOE-001",   2, 30, 0.25, "2026-04-15"),  # <-- stockout
    ("202", "SHOE-002",  85, 25, 0.25, "2026-05-20"),
    ("202", "SHOE-003",  60, 20, 0.25, "2026-05-18"),
    ("202", "APP-100",  200, 50, 0.30, "2026-05-28"),
    # Store 303 — normal
    ("303", "SHOE-001",  55, 20, 0.20, "2026-05-15"),
    ("303", "SHOE-002",  40, 15, 0.20, "2026-05-10"),
    ("303", "SHOE-003",  35, 12, 0.20, "2026-05-12"),
    ("303", "APP-100",  110, 40, 0.25, "2026-05-20"),
    # Store 404 — normal inventory (quality issue, not supply)
    ("404", "SHOE-001",  50, 18, 0.20, "2026-05-25"),
    ("404", "SHOE-002",  38, 14, 0.20, "2026-05-23"),
    ("404", "SHOE-003",  32, 12, 0.20, "2026-05-20"),
    ("404", "APP-100",   90, 35, 0.25, "2026-05-28"),
    # Store 505 — normal
    ("505", "SHOE-001",  70, 25, 0.22, "2026-05-18"),
    ("505", "SHOE-002",  55, 20, 0.22, "2026-05-15"),
    ("505", "SHOE-003",  45, 18, 0.22, "2026-05-20"),
    ("505", "APP-100",  160, 50, 0.28, "2026-05-22"),
]

COMPETITOR_ACTIVITY = [
    # Store 101: Metro Shoes at CP launches 20% off promo on June 1
    ("101", "Metro Shoes",   "promo_launch",
     "End-of-season flat 20% off on all footwear at Connaught Place outlet",
     "2026-06-01", "2026-06-30"),
    # Store 505: Decathlon opens nearby on April 28 (permanent)
    ("505", "Decathlon",     "store_opening",
     "New Decathlon megastore opened at Acropolis Mall, Kolkata (1.2 km away)",
     "2026-04-28", None),
    # Store 505: Decathlon also ran a grand-opening promo in May
    ("505", "Decathlon",     "promo_launch",
     "Grand opening offer — flat 25% off all sports footwear through May",
     "2026-04-28", "2026-05-31"),
]

# Monthly benchmark: (store_id, category, month, avg_daily_units, avg_daily_revenue, notes)
MONTHLY_BENCHMARKS = [
    # Store 303 (Indiranagar, Bengaluru) — shoes: June is pre-monsoon slowdown (~15% below May)
    # Bengaluru monsoon starts early June — people avoid buying footwear they'll ruin in rain
    ("303", "shoes", 3, 14.2, 85170.0,  "Pre-summer, normal footfall"),
    ("303", "shoes", 4, 16.1, 96440.0,  "Summer shopping peak"),
    ("303", "shoes", 5, 15.8, 94750.0,  "Pre-monsoon, still strong"),
    ("303", "shoes", 6, 13.4, 80300.0,  "Typical monsoon-onset dip — Bengaluru footfall drops as rains begin, customers avoid buying footwear"),
    ("303", "shoes", 7, 12.9, 77350.0,  "Peak monsoon, low foot traffic — expected"),
    # Store 505 (South City Mall, Kolkata) — historically grows in April-June (pre-monsoon shopping surge)
    ("505", "shoes", 3, 13.5, 80930.0,  "Normal"),
    ("505", "shoes", 4, 16.8, 100680.0, "Pre-monsoon shopping surge — Kolkata customers stock up before rains"),
    ("505", "shoes", 5, 18.2, 109100.0, "Peak pre-monsoon — typically strongest month"),
    ("505", "shoes", 6, 19.1, 114490.0, "Summer growth expected to continue into monsoon season"),
    # Store 101 (Connaught Place, New Delhi) — June historically similar to May
    ("101", "shoes", 4, 10.3, 61750.0,  "Normal"),
    ("101", "shoes", 5, 10.8, 64730.0,  "Normal"),
    ("101", "shoes", 6, 10.5, 62940.0,  "Historically stable June in CP — office-goers drive steady weekday sales"),
    # Store 404 (Anna Nagar, Chennai) — June picks up after college exams end
    ("404", "shoes", 3, 11.2, 67120.0,  "Normal"),
    ("404", "shoes", 4, 12.8, 76710.0,  "Slight uptick"),
    ("404", "shoes", 5, 14.5, 86920.0,  "Strong — Anna University semester ending, students shopping"),
    ("404", "shoes", 6, 15.2, 91110.0,  "Summer break — students and young professionals typically drive volume"),
]


def _generate_daily_sales_101(rng: random.Random) -> list[tuple]:
    """Store 101 (Connaught Place, Delhi): sharp drop from June 1 when Metro Shoes starts promo."""
    rows = []
    for d in daterange(START_DATE, END_DATE):
        date_str = d.isoformat()
        after_promo = d >= COMPETITOR_101_PROMO_DATE

        # SHOE-001: 8-12 normal → 4-6 after promo
        units_001 = rng.randint(4, 6) if after_promo else rng.randint(8, 12)
        rev_001 = round(units_001 * 5999.0, 2)

        # SHOE-002: 5-8 normal → 2-4 after promo
        units_002 = rng.randint(2, 4) if after_promo else rng.randint(5, 8)
        rev_002 = round(units_002 * 7999.0, 2)

        # SHOE-003: 3-5 normal → 2-3 after promo
        units_003 = rng.randint(2, 3) if after_promo else rng.randint(3, 5)
        rev_003 = round(units_003 * 6999.0, 2)

        # APP-100: unaffected
        units_app = rng.randint(8, 14)
        rev_app = round(units_app * 1999.0, 2)

        rows.extend([
            ("101", "SHOE-001", date_str, units_001, rev_001),
            ("101", "SHOE-002", date_str, units_002, rev_002),
            ("101", "SHOE-003", date_str, units_003, rev_003),
            ("101", "APP-100",  date_str, units_app,  rev_app),
        ])
    return rows


def _generate_daily_sales_202(rng: random.Random) -> list[tuple]:
    """Store 202 (Phoenix Palladium, Mumbai): SHOE-001 goes to near-zero from May 28 (stockout). Others stable."""
    rows = []
    for d in daterange(START_DATE, END_DATE):
        date_str = d.isoformat()
        after_stockout = d >= STOCKOUT_202_DATE

        # SHOE-001: 15-20 normal → 0-2 after stockout
        units_001 = rng.randint(0, 2) if after_stockout else rng.randint(15, 20)
        rev_001 = round(units_001 * 5499.0, 2)  # Mall outlet pricing (slight discount)

        # SHOE-002: stable throughout
        units_002 = rng.randint(8, 13)
        rev_002 = round(units_002 * 7999.0, 2)

        # SHOE-003: slight dip post-stockout (customers coming for SHOE-001, leave disappointed)
        units_003 = rng.randint(3, 6) if after_stockout else rng.randint(6, 9)
        rev_003 = round(units_003 * 6999.0, 2)

        # APP-100: unaffected
        units_app = rng.randint(18, 28)
        rev_app = round(units_app * 1999.0, 2)

        rows.extend([
            ("202", "SHOE-001", date_str, units_001, rev_001),
            ("202", "SHOE-002", date_str, units_002, rev_002),
            ("202", "SHOE-003", date_str, units_003, rev_003),
            ("202", "APP-100",  date_str, units_app,  rev_app),
        ])
    return rows


def _generate_daily_sales_303(rng: random.Random) -> list[tuple]:
    """Store 303 (Indiranagar, Bengaluru): mild dip from mid-May as monsoon approaches — matches benchmark perfectly."""
    rows = []
    for d in daterange(START_DATE, END_DATE):
        date_str = d.isoformat()

        # Seasonal factor: normal in Mar-Apr-early May, then ~15% lower from May 15
        if d < SEASONAL_303_DIP_DATE:
            base_001, base_002, base_003 = 14, 9, 7
        else:
            # 15% lower — seasonal dip
            base_001, base_002, base_003 = 12, 8, 6

        units_001 = clamp(rng.randint(base_001 - 1, base_001 + 2), 0, 30)
        units_002 = clamp(rng.randint(base_002 - 1, base_002 + 2), 0, 20)
        units_003 = clamp(rng.randint(base_003 - 1, base_003 + 2), 0, 15)

        rev_001 = round(units_001 * 5999.0, 2)
        rev_002 = round(units_002 * 7999.0, 2)
        rev_003 = round(units_003 * 6999.0, 2)

        units_app = rng.randint(6, 10)
        rev_app = round(units_app * 1999.0, 2)

        rows.extend([
            ("303", "SHOE-001", date_str, units_001, rev_001),
            ("303", "SHOE-002", date_str, units_002, rev_002),
            ("303", "SHOE-003", date_str, units_003, rev_003),
            ("303", "APP-100",  date_str, units_app,  rev_app),
        ])
    return rows


def _generate_daily_sales_404(rng: random.Random) -> list[tuple]:
    """Store 404 (Anna Nagar, Chennai): SHOE-001 gradually declining from May 1 (bad product batch / quality issue)."""
    rows = []
    for d in daterange(START_DATE, END_DATE):
        date_str = d.isoformat()

        days_since_quality_issue = max(0, (d - QUALITY_404_START_DATE).days)

        # SHOE-001: starts at 12-15, decays gradually by ~1 unit per 10 days
        base_001 = max(4, 13 - (days_since_quality_issue // 8))
        units_001 = clamp(rng.randint(base_001 - 1, base_001 + 2), 0, 18)
        rev_001 = round(units_001 * 5999.0, 2)

        # SHOE-002: stable (quality issue is SHOE-001 specific)
        units_002 = rng.randint(6, 10)
        rev_002 = round(units_002 * 7999.0, 2)

        # SHOE-003: stable
        units_003 = rng.randint(5, 8)
        rev_003 = round(units_003 * 6999.0, 2)

        # APP-100: stable
        units_app = rng.randint(10, 16)
        rev_app = round(units_app * 1999.0, 2)

        rows.extend([
            ("404", "SHOE-001", date_str, units_001, rev_001),
            ("404", "SHOE-002", date_str, units_002, rev_002),
            ("404", "SHOE-003", date_str, units_003, rev_003),
            ("404", "APP-100",  date_str, units_app,  rev_app),
        ])
    return rows


def _generate_daily_sales_505(rng: random.Random) -> list[tuple]:
    """Store 505 (South City Mall, Kolkata): all shoe SKUs gradually declining from April 28 (Decathlon opened nearby)."""
    rows = []
    for d in daterange(START_DATE, END_DATE):
        date_str = d.isoformat()

        days_since_competitor = max(0, (d - COMPETITOR_505_OPEN_DATE).days)

        # All shoe SKUs decline gradually — ~2.5% per week
        decay_factor = max(0.50, 1.0 - (days_since_competitor * 0.003))

        base_001 = int(18 * decay_factor)
        base_002 = int(12 * decay_factor)
        base_003 = int(10 * decay_factor)

        units_001 = clamp(rng.randint(max(0, base_001 - 2), base_001 + 2), 0, 22)
        units_002 = clamp(rng.randint(max(0, base_002 - 2), base_002 + 2), 0, 16)
        units_003 = clamp(rng.randint(max(0, base_003 - 2), base_003 + 2), 0, 14)

        rev_001 = round(units_001 * 5999.0, 2)
        rev_002 = round(units_002 * 7999.0, 2)
        rev_003 = round(units_003 * 6999.0, 2)

        # APP-100: less affected by shoe competitor
        units_app = rng.randint(12, 18)
        rev_app = round(units_app * 1999.0, 2)

        rows.extend([
            ("505", "SHOE-001", date_str, units_001, rev_001),
            ("505", "SHOE-002", date_str, units_002, rev_002),
            ("505", "SHOE-003", date_str, units_003, rev_003),
            ("505", "APP-100",  date_str, units_app,  rev_app),
        ])
    return rows


def _generate_customer_feedback(rng: random.Random) -> list[tuple]:
    """Generate customer feedback. Store 404 gets bad reviews from May 1 for SHOE-001."""
    rows = []

    bad_comments_404 = [
        "Sole came apart after only 2 weeks — very disappointing for Rs 5999",
        "Quality has really gone down only. Classic Runner used to be so good before",
        "Had to return the shoes, sole glue was clearly defective from factory itself",
        "Not at all worth Rs 6000 — fell apart within one month only",
        "Bought two pairs, both had same sole peeling issue. Clearly a batch problem",
        "Stitching came undone on day 3 itself. Very bad quality this time",
        "Classic Runner used to be my favourite. This new batch is very bad quality",
        "Store manager also said many customers are complaining about same issue only",
    ]

    good_comments_general = [
        "Very good product, will definitely buy again",
        "Comfortable fit and good quality, fully satisfied",
        "Prompt delivery, product is exactly as shown",
        "Paisa vasool — totally worth the price",
        "Good value for money, highly recommend",
    ]

    feedback_from_101_about_metro = [
        "Metro Shoes next to this store is giving flat 20% off — hard to justify full price here",
        "Metro has a big end-of-season sale going on in CP, checked it out",
        "Nearby Metro Shoes outlet is running a big discount, might buy from there",
    ]

    # Store 404: negative feedback for SHOE-001 from May 1 (40 reviews, mostly bad)
    for i in range(40):
        days_offset = rng.randint(0, (END_DATE - QUALITY_404_START_DATE).days)
        fd = QUALITY_404_START_DATE + timedelta(days=days_offset)
        rating = rng.randint(1, 2)  # Very poor
        comment = rng.choice(bad_comments_404)
        rows.append(("404", "SHOE-001", fd.isoformat(), rating, comment))

    # Store 404: some good reviews for SHOE-002 and SHOE-003 (control group)
    for sku in ("SHOE-002", "SHOE-003"):
        for i in range(15):
            days_offset = rng.randint(0, (END_DATE - START_DATE).days)
            fd = START_DATE + timedelta(days=days_offset)
            rating = rng.randint(4, 5)
            comment = rng.choice(good_comments_general)
            rows.append(("404", sku, fd.isoformat(), rating, comment))

    # Store 101: feedback mentioning Metro Shoes (after June 1)
    for comment in feedback_from_101_about_metro:
        days_offset = rng.randint(0, (END_DATE - COMPETITOR_101_PROMO_DATE).days)
        fd = COMPETITOR_101_PROMO_DATE + timedelta(days=days_offset)
        rating = rng.randint(3, 4)
        rows.append(("101", None, fd.isoformat(), rating, comment))

    # Store 202: feedback about stockout
    stockout_comments = [
        "Came to Phoenix specifically for Classic Runner but it is out of stock only",
        "No stock of Classic Runner in my size. Went back empty handed from mall itself",
        "Wanted to buy 2 pairs but they had only 1 left in size 9. Very bad stock management",
        "Staff said Classic Runner has been out of stock for more than one week already",
    ]
    for comment in stockout_comments:
        days_offset = rng.randint(0, (END_DATE - STOCKOUT_202_DATE).days)
        fd = STOCKOUT_202_DATE + timedelta(days=days_offset)
        rating = rng.randint(2, 3)
        rows.append(("202", "SHOE-001", fd.isoformat(), rating, comment))

    # Store 505: feedback mentioning Decathlon
    decathlon_comments = [
        "Decathlon has opened at Acropolis Mall nearby — much better selection and prices there",
        "The new Decathlon store is very good. Hard to justify coming here at full price now",
        "Decathlon has wider range of sports shoes. Will mostly shop there from now on",
    ]
    for comment in decathlon_comments:
        days_offset = rng.randint(0, (END_DATE - COMPETITOR_505_OPEN_DATE).days)
        fd = COMPETITOR_505_OPEN_DATE + timedelta(days=days_offset)
        rating = rng.randint(3, 4)
        rows.append(("505", None, fd.isoformat(), rating, comment))

    # General good feedback for other stores (303)
    for i in range(20):
        days_offset = rng.randint(0, (END_DATE - START_DATE).days)
        fd = START_DATE + timedelta(days=days_offset)
        sku = rng.choice(["SHOE-001", "SHOE-002", None])
        rating = rng.randint(4, 5)
        comment = rng.choice(good_comments_general)
        rows.append(("303", sku, fd.isoformat(), rating, comment))

    return rows


def seed(db_path: str | None = None) -> None:
    """Seed the database with schema and mock data for all 5 stores."""
    import sqlite3

    if db_path:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    else:
        conn = get_connection()

    rng = random.Random(42)  # Fixed seed for reproducibility

    print("Seeding ARICO database...")

    # 1. Create tables
    for statement in SCHEMA_DDL.strip().split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.commit()

    # 2. Stores
    conn.executemany(
        "INSERT OR REPLACE INTO stores VALUES (?,?,?,?,?,?,?,?)",
        STORES,
    )

    # 3. Products
    conn.executemany(
        "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?)",
        PRODUCTS,
    )

    # 4. Inventory
    conn.executemany(
        "INSERT OR REPLACE INTO inventory VALUES (?,?,?,?,?,?)",
        INVENTORY,
    )

    # 5. Daily sales
    print("  Generating daily sales data (~1,800 rows)...")
    all_sales = []
    all_sales.extend(_generate_daily_sales_101(rng))
    all_sales.extend(_generate_daily_sales_202(rng))
    all_sales.extend(_generate_daily_sales_303(rng))
    all_sales.extend(_generate_daily_sales_404(rng))
    all_sales.extend(_generate_daily_sales_505(rng))

    conn.executemany(
        "INSERT INTO daily_sales (store_id, sku, sale_date, units_sold, revenue) VALUES (?,?,?,?,?)",
        all_sales,
    )

    # 6. Competitor activity
    conn.executemany(
        "INSERT INTO competitor_activity (store_id, competitor_name, activity_type, description, start_date, end_date) VALUES (?,?,?,?,?,?)",
        COMPETITOR_ACTIVITY,
    )

    # 7. Customer feedback
    print("  Generating customer feedback...")
    feedback_rows = _generate_customer_feedback(rng)
    conn.executemany(
        "INSERT INTO customer_feedback (store_id, sku, feedback_date, rating, comment) VALUES (?,?,?,?,?)",
        feedback_rows,
    )

    # 8. Monthly benchmarks
    conn.executemany(
        "INSERT OR REPLACE INTO monthly_benchmarks VALUES (?,?,?,?,?,?)",
        MONTHLY_BENCHMARKS,
    )

    conn.commit()

    # Summary
    sales_count = conn.execute("SELECT COUNT(*) FROM daily_sales").fetchone()[0]
    feedback_count = conn.execute("SELECT COUNT(*) FROM customer_feedback").fetchone()[0]
    print(f"  Inserted {sales_count} daily_sales rows, {feedback_count} customer_feedback rows")
    print(f"  Stores: {', '.join(s[0] for s in STORES)}")
    print("Database seeded successfully.")

    if db_path:
        conn.close()


if __name__ == "__main__":
    seed()
