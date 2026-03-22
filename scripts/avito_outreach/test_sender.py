#!/usr/bin/env python3
"""Tests for sender.py and message_templates.py."""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure we can import from the package
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.avito_outreach.sender import (
    Account,
    SendResult,
    SessionStats,
    _init_outreach_table,
    get_daily_send_count,
    get_candidates,
    get_outreach_stats,
    is_account_warm,
    load_accounts,
    log_outreach,
    pick_account,
)
from scripts.avito_outreach.message_templates import (
    generate_message,
    generate_message_batch,
    _format_template,
    BUYER_INTEREST_TEMPLATES,
    PARTNERSHIP_TEMPLATES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database with sellers and scored_sellers tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE sellers (
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
        CREATE TABLE scored_sellers (
            seller_id INTEGER PRIMARY KEY,
            score INTEGER NOT NULL,
            is_candidate INTEGER NOT NULL DEFAULT 0,
            score_breakdown TEXT NOT NULL,
            scored_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Insert test sellers
    sellers = [
        (1001, 5001, "iPhone 15 оптом из Китая", 45000, 8, "electronics", "Владивосток", "2026-01-15", 25),
        (1002, 5002, "Куртки зимние партия", 3000, 6, "clothing", "Новосибирск", "2025-06-01", 50),
        (1003, 5003, "Наушники AirPods", 2500, 3, "electronics", "Москва", "2024-01-01", 5),
    ]
    conn.executemany(
        "INSERT INTO sellers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        sellers,
    )

    # Insert scored candidates
    scored = [
        (1001, 85, 1, '{"import_keywords": 20, "many_items": 20, "catalog_photos": 15, "new_account": 15, "import_hub_city": 10}'),
        (1002, 70, 1, '{"many_items": 20, "catalog_photos": 15, "low_price": 15, "import_hub_city": 10}'),
        (1003, 30, 0, '{"few_items": 5}'),
    ]
    conn.executemany(
        "INSERT INTO scored_sellers VALUES (?, ?, ?, ?, datetime('now'))",
        scored,
    )

    _init_outreach_table(conn)
    conn.commit()
    conn.close()

    yield Path(db_path)
    os.unlink(db_path)


@pytest.fixture
def tmp_accounts_file():
    """Create a temporary accounts JSON config."""
    accounts = [
        {
            "login": "test1@mail.ru",
            "password": "pass1",
            "profile_id": "profile-uuid-1",
            "user_id": 111,
            "created_at": "2026-03-01",
            "warm_up_start": "2026-03-10",
            "provider": "gologin",
        },
        {
            "login": "test2@mail.ru",
            "password": "pass2",
            "profile_id": "profile-uuid-2",
            "user_id": 222,
            "created_at": "2026-03-15",
            "warm_up_start": "2026-03-18",
            "provider": "dolphin",
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(accounts, f)
        path = f.name

    yield Path(path)
    os.unlink(path)


SAMPLE_SELLER = {
    "seller_id": 1001,
    "item_id": 5001,
    "title": "iPhone 15 оптом из Китая",
    "price": 45000,
    "category": "electronics",
    "city": "Владивосток",
    "items_count": 25,
    "score": 85,
}


# ---------------------------------------------------------------------------
# message_templates tests
# ---------------------------------------------------------------------------

class TestMessageTemplates:

    def test_format_template_fills_fields(self):
        template = "Привет! Интересует «{title}» в городе {city}."
        result = _format_template(template, SAMPLE_SELLER)
        assert "iPhone 15 оптом из Китая" in result
        assert "Владивосток" in result

    def test_format_template_handles_missing_fields(self):
        template = "Интересует «{title}» — {price} руб."
        result = _format_template(template, {"seller_id": 1})
        assert "ваш товар" in result
        assert "—" in result

    def test_generate_message_without_llm(self):
        msg = generate_message(SAMPLE_SELLER, use_llm=False)
        assert len(msg) > 10
        assert isinstance(msg, str)

    def test_generate_message_llm_fallback_no_key(self):
        """Without API key, should fall back to template."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            msg = generate_message(SAMPLE_SELLER, use_llm=True)
            assert len(msg) > 10

    @patch("scripts.avito_outreach.message_templates._call_claude_api")
    def test_generate_message_uses_llm_when_available(self, mock_api):
        mock_api.return_value = "Здравствуйте! Увидел ваши iPhone 15 оптом — какие условия на партию?"
        msg = generate_message(SAMPLE_SELLER, use_llm=True)
        assert "iPhone" in msg
        mock_api.assert_called_once()

    @patch("scripts.avito_outreach.message_templates._call_claude_api")
    def test_generate_message_llm_failure_falls_back(self, mock_api):
        mock_api.return_value = None
        msg = generate_message(SAMPLE_SELLER, use_llm=True)
        assert len(msg) > 10  # Got a template fallback

    def test_generate_message_batch(self):
        sellers = [SAMPLE_SELLER, {**SAMPLE_SELLER, "seller_id": 1002, "title": "Куртки"}]
        results = generate_message_batch(sellers, use_llm=False)
        assert len(results) == 2
        assert results[0]["seller_id"] == 1001
        assert results[1]["seller_id"] == 1002
        assert all(r["message"] for r in results)

    def test_all_templates_have_valid_placeholders(self):
        """All templates should format without errors using sample data."""
        for template in BUYER_INTEREST_TEMPLATES + PARTNERSHIP_TEMPLATES:
            result = _format_template(template, SAMPLE_SELLER)
            assert len(result) > 10
            assert "{" not in result  # No unformatted placeholders


# ---------------------------------------------------------------------------
# sender tests: account management
# ---------------------------------------------------------------------------

class TestAccountManagement:

    def test_load_accounts(self, tmp_accounts_file):
        accounts = load_accounts(tmp_accounts_file)
        assert len(accounts) == 2
        assert accounts[0].login == "test1@mail.ru"
        assert accounts[0].provider == "gologin"
        assert accounts[1].provider == "dolphin"

    def test_load_accounts_missing_file(self):
        accounts = load_accounts(Path("/nonexistent/accounts.json"))
        assert accounts == []

    def test_is_account_warm_yes(self):
        acc = Account(
            login="test@mail.ru", password="p", profile_id="x",
            warm_up_start=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
        )
        assert is_account_warm(acc) is True

    def test_is_account_warm_no(self):
        acc = Account(
            login="test@mail.ru", password="p", profile_id="x",
            warm_up_start=datetime.now().strftime("%Y-%m-%d"),
        )
        assert is_account_warm(acc) is False

    def test_is_account_warm_no_date(self):
        acc = Account(login="test@mail.ru", password="p", profile_id="x")
        assert is_account_warm(acc) is False

    def test_get_daily_send_count(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)
        # Log some sends
        for i in range(3):
            log_outreach(conn, SendResult(
                seller_id=1000 + i, status="sent",
                message_text="test", account_login="acc1@mail.ru",
            ))
        log_outreach(conn, SendResult(
            seller_id=2000, status="error",
            message_text="test", account_login="acc1@mail.ru",
        ))
        assert get_daily_send_count(conn, "acc1@mail.ru") == 3
        assert get_daily_send_count(conn, "acc2@mail.ru") == 0
        conn.close()

    def test_pick_account_respects_daily_limit(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)

        acc = Account(
            login="limited@mail.ru", password="p", profile_id="x",
            warm_up_start=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
        )
        # Fill up daily limit
        for i in range(15):
            log_outreach(conn, SendResult(
                seller_id=3000 + i, status="sent",
                message_text="test", account_login="limited@mail.ru",
            ))
        result = pick_account([acc], conn, warm_up_mode=False)
        assert result is None
        conn.close()

    def test_pick_account_skips_unwarm(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)
        acc = Account(
            login="new@mail.ru", password="p", profile_id="x",
            warm_up_start=datetime.now().strftime("%Y-%m-%d"),
        )
        result = pick_account([acc], conn, warm_up_mode=False)
        assert result is None  # Not warm yet
        conn.close()

    def test_pick_account_allows_unwarm_in_warmup_mode(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)
        acc = Account(
            login="new@mail.ru", password="p", profile_id="x",
            warm_up_start=datetime.now().strftime("%Y-%m-%d"),
        )
        result = pick_account([acc], conn, warm_up_mode=True)
        assert result is not None
        assert result.login == "new@mail.ru"
        conn.close()


# ---------------------------------------------------------------------------
# sender tests: candidate selection
# ---------------------------------------------------------------------------

class TestCandidateSelection:

    def test_get_candidates_returns_scored(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        _init_outreach_table(conn)

        candidates = get_candidates(conn)
        assert len(candidates) == 2  # Only is_candidate=1
        assert candidates[0]["seller_id"] == 1001  # Highest score first
        assert candidates[1]["seller_id"] == 1002
        conn.close()

    def test_get_candidates_excludes_already_contacted(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        _init_outreach_table(conn)

        # Mark seller 1001 as already contacted
        log_outreach(conn, SendResult(
            seller_id=1001, status="sent",
            message_text="hello", account_login="acc@mail.ru",
        ))

        candidates = get_candidates(conn)
        assert len(candidates) == 1
        assert candidates[0]["seller_id"] == 1002
        conn.close()

    def test_get_candidates_respects_limit(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        conn.row_factory = sqlite3.Row
        _init_outreach_table(conn)

        candidates = get_candidates(conn, limit=1)
        assert len(candidates) == 1
        conn.close()


# ---------------------------------------------------------------------------
# sender tests: outreach logging
# ---------------------------------------------------------------------------

class TestOutreachLogging:

    def test_log_outreach_writes_record(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)

        log_outreach(conn, SendResult(
            seller_id=1001,
            status="sent",
            message_text="Привет! Интересуют iPhone оптом.",
            account_login="acc@mail.ru",
        ))

        row = conn.execute(
            "SELECT * FROM outreach_log WHERE seller_id = 1001"
        ).fetchone()
        assert row is not None
        assert row[4] == "acc@mail.ru"  # account_used
        assert row[5] == "sent"  # status
        conn.close()

    def test_log_outreach_error_with_detail(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)

        log_outreach(conn, SendResult(
            seller_id=1002,
            status="captcha",
            message_text="test message",
            account_login="acc@mail.ru",
            error_detail="Captcha appeared on messenger page",
        ))

        row = conn.execute(
            "SELECT status, error_detail FROM outreach_log WHERE seller_id = 1002"
        ).fetchone()
        assert row[0] == "captcha"
        assert "Captcha" in row[1]
        conn.close()


# ---------------------------------------------------------------------------
# sender tests: stats
# ---------------------------------------------------------------------------

class TestOutreachStats:

    def test_get_outreach_stats(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        _init_outreach_table(conn)

        # Log some results
        for sid, status in [(1001, "sent"), (1002, "sent"), (1003, "captcha")]:
            log_outreach(conn, SendResult(
                seller_id=sid, status=status,
                message_text="msg", account_login="acc@mail.ru",
            ))
        conn.close()

        stats = get_outreach_stats(tmp_db)
        assert stats["total_attempts"] == 3
        assert stats["total_sent"] == 2
        assert stats["by_status"]["sent"] == 2
        assert stats["by_status"]["captcha"] == 1
        assert len(stats["recent_10"]) == 3

    def test_get_outreach_stats_empty_db(self, tmp_db):
        stats = get_outreach_stats(tmp_db)
        assert stats["total_attempts"] == 0
        assert stats["total_sent"] == 0
