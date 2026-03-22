#!/usr/bin/env python3
"""Tests for analytics module."""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common import analytics


def _create_test_log_db(path: Path):
    """Create a test logs.db with sample data."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE dialog_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT,
            user_id TEXT,
            company_id TEXT,
            skill_name TEXT,
            message TEXT,
            response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    rows = [
        ("t1", "user1", "company-a", "calc",
         json.dumps({"product": "телефон", "weight_kg": 10, "route": "Гуанчжоу→Москва"}),
         "result1"),
        ("t2", "user2", "company-a", "calc",
         json.dumps({"product": "наушники", "weight_kg": 2, "route": "Гуанчжоу→Москва"}),
         "result2"),
        ("t3", "user1", "company-a", "calc",
         json.dumps({"product": "ткань", "weight_kg": 50, "route": "Иу→СПб"}),
         "result3"),
        ("t4", "user1", "company-a", "admin", "show", "rates"),
        ("t5", "user3", "company-b", "calc",
         json.dumps({"product": "обувь", "weight_kg": 20, "route": "Гуанчжоу→Москва"}),
         "result5"),
        ("t6", "user3", "company-b", "status", "where truck", "in transit"),
    ]

    for row in rows:
        conn.execute(
            "INSERT INTO dialog_logs (trace_id, user_id, company_id, skill_name, message, response) VALUES (?,?,?,?,?,?)",
            row,
        )
    conn.commit()
    conn.close()


def test_company_stats():
    """Test per-company analytics."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    _create_test_log_db(db_path)

    with patch.object(analytics, "LOG_DB_PATH", db_path):
        stats = analytics.get_company_stats("company-a", "all")

    assert stats["total_requests"] == 4
    assert stats["calculations"] == 3
    assert stats["unique_clients"] == 2
    assert stats["by_skill"]["calc"] == 3
    assert stats["by_skill"]["admin"] == 1
    # Top routes
    routes = dict(stats["top_routes"])
    assert routes["Гуанчжоу→Москва"] == 2
    assert routes["Иу→СПб"] == 1


def test_company_stats_empty():
    """Test analytics for non-existent company."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    _create_test_log_db(db_path)

    with patch.object(analytics, "LOG_DB_PATH", db_path):
        stats = analytics.get_company_stats("nonexistent", "all")

    assert stats["total_requests"] == 0
    assert stats["calculations"] == 0
    assert stats["unique_clients"] == 0


def test_company_stats_no_db():
    """Test analytics when logs.db doesn't exist."""
    with patch.object(analytics, "LOG_DB_PATH", Path("/tmp/nonexistent_analytics.db")):
        stats = analytics.get_company_stats("any", "month")

    assert stats["total_requests"] == 0


def test_owner_summary():
    """Test cross-company summary."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    _create_test_log_db(db_path)

    with patch.object(analytics, "LOG_DB_PATH", db_path):
        stats = analytics.get_owner_summary("all")

    assert stats["total_requests"] == 6
    assert stats["total_calculations"] == 4
    assert stats["total_unique_clients"] == 3
    assert len(stats["companies"]) == 2

    # company-a has more requests
    companies = {c["company_id"]: c for c in stats["companies"]}
    assert companies["company-a"]["requests"] == 4
    assert companies["company-b"]["requests"] == 2


def test_format_company_stats():
    """Test formatting produces readable output."""
    stats = {
        "total_requests": 10,
        "calculations": 7,
        "unique_clients": 3,
        "by_skill": {"calc": 7, "admin": 3},
        "top_routes": [("Гуанчжоу→Москва", 5), ("Иу→СПб", 2)],
        "recent_calcs": [
            {"user_id": "u1", "timestamp": "2026-03-22 10:30:00", "product": "телефон", "weight_kg": 10, "route": "ГЧ→МСК"},
        ],
        "period": "month",
    }
    text = analytics.format_company_stats("test-co", stats)
    assert "Аналитика test-co" in text
    assert "10" in text
    assert "Расчёты: 7" in text
    assert "Гуанчжоу→Москва" in text


def test_format_owner_summary():
    """Test owner summary formatting."""
    stats = {
        "total_requests": 20,
        "total_calculations": 15,
        "total_unique_clients": 8,
        "companies": [
            {"company_id": "co-a", "requests": 12, "calculations": 10, "unique_clients": 5},
            {"company_id": "co-b", "requests": 8, "calculations": 5, "unique_clients": 3},
        ],
        "period": "week",
    }
    text = analytics.format_owner_summary(stats)
    assert "Сводка по всем компаниям" in text
    assert "за неделю" in text
    assert "co-a" in text
    assert "co-b" in text


if __name__ == "__main__":
    test_company_stats()
    test_company_stats_empty()
    test_company_stats_no_db()
    test_owner_summary()
    test_format_company_stats()
    test_format_owner_summary()
    print("All analytics tests passed!")
