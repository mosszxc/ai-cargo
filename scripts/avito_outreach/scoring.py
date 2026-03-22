#!/usr/bin/env python3
"""
Scoring model for identifying probable China importers among Avito sellers.

Reads the `sellers` table (parser output), applies weighted heuristics,
and writes results to `scored_sellers` table with score breakdown.

Usage:
  python -m scripts.avito_outreach.scoring [--db-path PATH] [--threshold 60]
  python scripts/avito_outreach/scoring.py --stats

Env vars:
  AVITO_DB_PATH — SQLite database path (default: data/avito_sellers.db)
"""

import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("avito_scoring")

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "avito_sellers.db"

THRESHOLD = 60

# Cities that are major China import hubs
IMPORT_HUB_CITIES = {"Владивосток", "Новосибирск"}

# Keywords indicating wholesale / direct import from China
IMPORT_KEYWORDS = re.compile(
    r"опт|оптом|от производителя|прямая поставка|прямые поставки|"
    r"из китая|из кнр|фабрик|завод|партия|оптовая|wholesale|1688",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    seller_id: int
    score: int
    is_candidate: bool
    breakdown: dict


# ---------------------------------------------------------------------------
# SQLite scored_sellers table
# ---------------------------------------------------------------------------

SCORED_SELLERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scored_sellers (
    seller_id INTEGER PRIMARY KEY,
    score INTEGER NOT NULL,
    is_candidate INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL,
    scored_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scored_sellers_score ON scored_sellers(score);
CREATE INDEX IF NOT EXISTS idx_scored_sellers_candidate ON scored_sellers(is_candidate);
"""


def _init_scored_table(conn: sqlite3.Connection):
    conn.executescript(SCORED_SELLERS_SCHEMA)


# ---------------------------------------------------------------------------
# Median price cache per category
# ---------------------------------------------------------------------------

def _load_median_prices(conn: sqlite3.Connection) -> dict[str, float]:
    """Compute median price per category from the sellers table."""
    rows = conn.execute(
        "SELECT category, price FROM sellers WHERE price IS NOT NULL AND price > 0"
    ).fetchall()

    by_cat: dict[str, list[int]] = {}
    for cat, price in rows:
        by_cat.setdefault(cat, []).append(price)

    medians = {}
    for cat, prices in by_cat.items():
        prices.sort()
        n = len(prices)
        if n % 2 == 1:
            medians[cat] = float(prices[n // 2])
        else:
            medians[cat] = (prices[n // 2 - 1] + prices[n // 2]) / 2.0
    return medians


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------

def score_seller(
    row: dict,
    median_prices: dict[str, float],
    now: datetime,
) -> ScoreResult:
    """Apply scoring heuristics to a single seller row.

    Weights (max 100):
      - New account (< 1 year):          +15
      - Many items (>10 in category):    +20
      - Many catalog photos (>=5):       +15
      - Low price (below median by 30%): +15
      - Import keywords in title:        +20
      - Import hub city:                 +10
      - Few/no items (proxy for few reviews): +5
    """
    breakdown = {}
    total = 0

    # 1. New account (< 1 year) → +15
    reg_date_str = row.get("seller_reg_date")
    if reg_date_str:
        try:
            reg_date = _parse_date(reg_date_str)
            if reg_date and (now - reg_date) < timedelta(days=365):
                breakdown["new_account"] = 15
                total += 15
        except (ValueError, TypeError):
            pass

    # 2. Many items (>10) → +20
    items_count = row.get("items_count")
    if items_count is not None and items_count > 10:
        breakdown["many_items"] = 20
        total += 20

    # 3. Catalog-style photos (>=5 photos as proxy for studio shots) → +15
    photos_count = row.get("photos_count") or 0
    if photos_count >= 5:
        breakdown["catalog_photos"] = 15
        total += 15

    # 4. Low price (below category median by 30%+) → +15
    price = row.get("price")
    category = row.get("category")
    if price and category and category in median_prices:
        median = median_prices[category]
        if median > 0 and price < median * 0.7:
            breakdown["low_price"] = 15
            total += 15

    # 5. Import keywords in title → +20
    title = row.get("title") or ""
    if IMPORT_KEYWORDS.search(title):
        breakdown["import_keywords"] = 20
        total += 20

    # 6. Import hub city (Vladivostok, Novosibirsk) → +10
    city = row.get("city") or ""
    if city in IMPORT_HUB_CITIES:
        breakdown["import_hub_city"] = 10
        total += 10

    # 7. Few/no items (fresh seller, proxy for few reviews) → +5
    if items_count is not None and items_count <= 3:
        breakdown["few_items"] = 5
        total += 5

    is_candidate = total >= THRESHOLD

    return ScoreResult(
        seller_id=row["seller_id"],
        score=total,
        is_candidate=is_candidate,
        breakdown=breakdown,
    )


def _parse_date(date_str: str) -> Optional[datetime]:
    """Try common date formats from Avito API."""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str.strip()[:19], fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Main scoring pipeline
# ---------------------------------------------------------------------------

def run_scoring(db_path: Optional[Path] = None, threshold: int = THRESHOLD) -> dict:
    """Score all sellers and write results to scored_sellers table.

    Returns summary stats.
    """
    db_path = db_path or Path(os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH)))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init_scored_table(conn)

    now = datetime.now(tz=None)
    median_prices = _load_median_prices(conn)

    sellers = conn.execute(
        "SELECT seller_id, item_id, title, price, photos_count, "
        "category, city, seller_reg_date, items_count FROM sellers"
    ).fetchall()

    total = 0
    candidates = 0
    score_sum = 0

    for row in sellers:
        row_dict = dict(row)
        result = score_seller(row_dict, median_prices, now)

        conn.execute(
            """
            INSERT INTO scored_sellers (seller_id, score, is_candidate, score_breakdown)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(seller_id) DO UPDATE SET
                score = excluded.score,
                is_candidate = excluded.is_candidate,
                score_breakdown = excluded.score_breakdown,
                scored_at = datetime('now')
            """,
            (
                result.seller_id,
                result.score,
                int(result.is_candidate),
                json.dumps(result.breakdown, ensure_ascii=False),
            ),
        )

        total += 1
        score_sum += result.score
        if result.is_candidate:
            candidates += 1

    conn.commit()
    conn.close()

    avg_score = round(score_sum / total, 1) if total > 0 else 0

    stats = {
        "total_scored": total,
        "candidates": candidates,
        "candidates_pct": round(candidates / total * 100, 1) if total > 0 else 0,
        "avg_score": avg_score,
        "threshold": threshold,
        "median_prices": {k: round(v) for k, v in median_prices.items()},
    }

    logger.info(
        "Scoring done: %d sellers, %d candidates (%.1f%%), avg score %.1f",
        total, candidates, stats["candidates_pct"], avg_score,
    )

    return stats


def get_stats(db_path: Optional[Path] = None) -> dict:
    """Read scoring stats from DB without re-scoring."""
    db_path = db_path or Path(os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH)))
    conn = sqlite3.connect(db_path)

    total = conn.execute("SELECT COUNT(*) FROM scored_sellers").fetchone()[0]
    candidates = conn.execute(
        "SELECT COUNT(*) FROM scored_sellers WHERE is_candidate = 1"
    ).fetchone()[0]

    score_dist = dict(conn.execute(
        """
        SELECT
            CASE
                WHEN score >= 80 THEN '80-100'
                WHEN score >= 60 THEN '60-79'
                WHEN score >= 40 THEN '40-59'
                WHEN score >= 20 THEN '20-39'
                ELSE '0-19'
            END AS bucket,
            COUNT(*)
        FROM scored_sellers GROUP BY bucket ORDER BY bucket DESC
        """
    ).fetchall())

    top_sellers = [
        dict(zip(["seller_id", "score", "breakdown"], r))
        for r in conn.execute(
            "SELECT seller_id, score, score_breakdown FROM scored_sellers "
            "ORDER BY score DESC LIMIT 10"
        ).fetchall()
    ]

    conn.close()

    return {
        "total": total,
        "candidates": candidates,
        "candidates_pct": round(candidates / total * 100, 1) if total > 0 else 0,
        "score_distribution": score_dist,
        "top_10": top_sellers,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Avito seller scoring model")
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (default: data/avito_sellers.db)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=THRESHOLD,
        help=f"Score threshold for outreach candidate (default: {THRESHOLD})",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print scoring stats from DB and exit (no re-scoring)",
    )

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    if args.stats:
        try:
            result = get_stats(db_path)
        except sqlite3.OperationalError as e:
            print(f"Error: {e}. Run scoring first.", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result = run_scoring(db_path, threshold=args.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
