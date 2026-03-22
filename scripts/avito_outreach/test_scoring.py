#!/usr/bin/env python3
"""Tests for the Avito seller scoring model."""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.avito_outreach.scoring import (
    ScoreResult,
    _init_scored_table,
    _load_median_prices,
    _parse_date,
    get_stats,
    run_scoring,
    score_seller,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SELLER_SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    seller_id INTEGER PRIMARY KEY,
    item_id INTEGER,
    title TEXT,
    price INTEGER,
    photos_count INTEGER DEFAULT 0,
    category TEXT,
    city TEXT,
    seller_reg_date TEXT,
    items_count INTEGER,
    first_seen_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _make_db(tmp_path: Path, sellers: list[dict]) -> Path:
    """Create a temp SQLite DB with sellers table and seed data."""
    db_path = tmp_path / "test_sellers.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SELLER_SCHEMA)
    for s in sellers:
        conn.execute(
            "INSERT INTO sellers (seller_id, item_id, title, price, photos_count, "
            "category, city, seller_reg_date, items_count) "
            "VALUES (:seller_id, :item_id, :title, :price, :photos_count, "
            ":category, :city, :seller_reg_date, :items_count)",
            s,
        )
    conn.commit()
    conn.close()
    return db_path


def _base_seller(**overrides) -> dict:
    """Return a baseline seller dict with optional overrides."""
    base = {
        "seller_id": 1001,
        "item_id": 5001,
        "title": "Чехол для iPhone 15",
        "price": 5000,
        "photos_count": 3,
        "category": "electronics",
        "city": "Москва",
        "seller_reg_date": "2023-01-15",
        "items_count": 5,
    }
    base.update(overrides)
    return base


NOW = datetime(2026, 3, 22)
MEDIANS = {"electronics": 5000, "clothing": 3000, "beauty": 2000, "auto_parts": 8000}


# ---------------------------------------------------------------------------
# Unit tests: score_seller
# ---------------------------------------------------------------------------

class TestScoreSeller:
    def test_zero_score_for_baseline(self):
        """Baseline seller with no matching signals should score 0."""
        seller = _base_seller()
        result = score_seller(seller, MEDIANS, NOW)
        assert result.score == 0
        assert result.is_candidate is False
        assert result.breakdown == {}

    def test_new_account(self):
        """Account < 1 year old gets +15."""
        reg = (NOW - timedelta(days=200)).strftime("%Y-%m-%d")
        seller = _base_seller(seller_reg_date=reg)
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("new_account") == 15
        assert result.score >= 15

    def test_old_account_no_bonus(self):
        """Account > 1 year old gets nothing."""
        seller = _base_seller(seller_reg_date="2020-01-01")
        result = score_seller(seller, MEDIANS, NOW)
        assert "new_account" not in result.breakdown

    def test_many_items(self):
        """More than 10 items → +20."""
        seller = _base_seller(items_count=25)
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("many_items") == 20

    def test_few_items_no_many_bonus(self):
        """5 items should not trigger many_items."""
        seller = _base_seller(items_count=5)
        result = score_seller(seller, MEDIANS, NOW)
        assert "many_items" not in result.breakdown

    def test_catalog_photos(self):
        """>=5 photos → +15."""
        seller = _base_seller(photos_count=7)
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("catalog_photos") == 15

    def test_few_photos_no_bonus(self):
        seller = _base_seller(photos_count=2)
        result = score_seller(seller, MEDIANS, NOW)
        assert "catalog_photos" not in result.breakdown

    def test_low_price(self):
        """Price below 70% of median → +15."""
        seller = _base_seller(price=2000, category="electronics")  # median 5000, 2000 < 3500
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("low_price") == 15

    def test_normal_price_no_bonus(self):
        seller = _base_seller(price=4500, category="electronics")  # 4500 >= 3500
        result = score_seller(seller, MEDIANS, NOW)
        assert "low_price" not in result.breakdown

    def test_import_keywords_opt(self):
        """Title with 'оптом' → +20."""
        seller = _base_seller(title="Наушники оптом от 10 штук")
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("import_keywords") == 20

    def test_import_keywords_producer(self):
        seller = _base_seller(title="Куртки от производителя зимние")
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("import_keywords") == 20

    def test_import_keywords_china(self):
        seller = _base_seller(title="Запчасти из Китая для авто")
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("import_keywords") == 20

    def test_no_keywords_no_bonus(self):
        seller = _base_seller(title="Б/у велосипед")
        result = score_seller(seller, MEDIANS, NOW)
        assert "import_keywords" not in result.breakdown

    def test_import_hub_city(self):
        """Vladivostok → +10."""
        seller = _base_seller(city="Владивосток")
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("import_hub_city") == 10

    def test_novosibirsk_hub(self):
        seller = _base_seller(city="Новосибирск")
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("import_hub_city") == 10

    def test_moscow_not_hub(self):
        seller = _base_seller(city="Москва")
        result = score_seller(seller, MEDIANS, NOW)
        assert "import_hub_city" not in result.breakdown

    def test_few_items_proxy_reviews(self):
        """<=3 items → +5 (proxy for few reviews)."""
        seller = _base_seller(items_count=2)
        result = score_seller(seller, MEDIANS, NOW)
        assert result.breakdown.get("few_items") == 5

    def test_candidate_threshold(self):
        """Seller with multiple signals should cross threshold."""
        reg = (NOW - timedelta(days=100)).strftime("%Y-%m-%d")
        seller = _base_seller(
            seller_reg_date=reg,      # +15
            items_count=20,           # +20
            photos_count=8,           # +15
            price=1500,               # +15 (below 3500)
            category="electronics",
            title="Наушники оптом",   # +20
            city="Владивосток",       # +10
        )
        result = score_seller(seller, MEDIANS, NOW)
        assert result.score == 95
        assert result.is_candidate is True

    def test_null_fields_no_crash(self):
        """Seller with None fields shouldn't crash."""
        seller = _base_seller(
            price=None,
            seller_reg_date=None,
            items_count=None,
            title=None,
        )
        result = score_seller(seller, MEDIANS, NOW)
        assert isinstance(result.score, int)


# ---------------------------------------------------------------------------
# Unit tests: _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2025-06-15T10:30:00") == datetime(2025, 6, 15, 10, 30, 0)

    def test_iso_with_z(self):
        assert _parse_date("2025-06-15T10:30:00Z") == datetime(2025, 6, 15, 10, 30, 0)

    def test_date_only(self):
        assert _parse_date("2025-06-15") == datetime(2025, 6, 15)

    def test_russian_format(self):
        assert _parse_date("15.06.2025") == datetime(2025, 6, 15)

    def test_invalid_returns_none(self):
        assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# Integration tests: run_scoring + get_stats
# ---------------------------------------------------------------------------

class TestRunScoring:
    def test_full_pipeline(self, tmp_path):
        """End-to-end: seed sellers, run scoring, verify output."""
        reg_new = (NOW - timedelta(days=100)).strftime("%Y-%m-%d")
        sellers = [
            _base_seller(
                seller_id=1,
                items_count=25,
                photos_count=8,
                price=1500,
                title="Наушники оптом",
                city="Владивосток",
                seller_reg_date=reg_new,
            ),
            _base_seller(
                seller_id=2,
                items_count=3,
                photos_count=2,
                price=6000,
                title="Б/у велосипед",
                city="Москва",
                seller_reg_date="2020-01-01",
            ),
        ]
        db_path = _make_db(tmp_path, sellers)
        result = run_scoring(db_path)

        assert result["total_scored"] == 2
        assert result["candidates"] == 1  # only seller_id=1

        # Verify DB contents
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT seller_id, score, is_candidate FROM scored_sellers ORDER BY score DESC"
        ).fetchall()
        conn.close()

        assert len(rows) == 2
        assert rows[0][0] == 1  # top scorer
        assert rows[0][1] >= 60
        assert rows[0][2] == 1
        assert rows[1][0] == 2
        assert rows[1][2] == 0

    def test_get_stats_after_scoring(self, tmp_path):
        reg_new = (NOW - timedelta(days=100)).strftime("%Y-%m-%d")
        sellers = [
            _base_seller(
                seller_id=i, items_count=20, title="оптом",
                city="Владивосток", seller_reg_date=reg_new, photos_count=6,
            )
            for i in range(1, 6)
        ]
        db_path = _make_db(tmp_path, sellers)
        run_scoring(db_path)

        stats = get_stats(db_path)
        assert stats["total"] == 5
        assert stats["candidates"] >= 1
        assert "score_distribution" in stats
        assert "top_10" in stats

    def test_empty_db(self, tmp_path):
        db_path = _make_db(tmp_path, [])
        result = run_scoring(db_path)
        assert result["total_scored"] == 0
        assert result["candidates"] == 0

    def test_idempotent_scoring(self, tmp_path):
        """Running scoring twice should update, not duplicate."""
        sellers = [_base_seller(seller_id=1, items_count=15, title="оптом")]
        db_path = _make_db(tmp_path, sellers)

        run_scoring(db_path)
        run_scoring(db_path)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM scored_sellers").fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# Median price tests
# ---------------------------------------------------------------------------

class TestMedianPrices:
    def test_median_computed_per_category(self, tmp_path):
        sellers = [
            _base_seller(seller_id=1, price=100, category="electronics"),
            _base_seller(seller_id=2, price=200, category="electronics"),
            _base_seller(seller_id=3, price=300, category="electronics"),
            _base_seller(seller_id=4, price=1000, category="clothing"),
            _base_seller(seller_id=5, price=2000, category="clothing"),
        ]
        db_path = _make_db(tmp_path, sellers)
        conn = sqlite3.connect(db_path)
        medians = _load_median_prices(conn)
        conn.close()

        assert medians["electronics"] == 200  # middle of [100, 200, 300]
        assert medians["clothing"] == 1500    # avg of [1000, 2000]

    def test_null_prices_excluded(self, tmp_path):
        sellers = [
            _base_seller(seller_id=1, price=None, category="electronics"),
            _base_seller(seller_id=2, price=500, category="electronics"),
        ]
        db_path = _make_db(tmp_path, sellers)
        conn = sqlite3.connect(db_path)
        medians = _load_median_prices(conn)
        conn.close()

        assert medians["electronics"] == 500
