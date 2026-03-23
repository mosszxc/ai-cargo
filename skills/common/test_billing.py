#!/usr/bin/env python3
"""Tests for billing / pilot plan management."""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.billing import Billing, PILOT_EXPIRED_MSG


def test_no_plan_is_unlimited():
    """Company without a plan should be allowed (unlimited)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    result = b.check_allowance("unknown-company")
    assert result["allowed"] is True
    assert result["plan"] is None

    print("PASS: test_no_plan_is_unlimited")


def test_activate_pilot():
    """Pilot plan should be created with correct defaults."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    now = datetime(2026, 3, 1, 12, 0, 0)
    info = b.activate_pilot("co1", now=now)

    assert info["plan"] == "pilot"
    assert info["calc_limit"] == 100
    assert info["calc_used"] == 0
    assert info["expires_at"] == "2026-03-15T12:00:00"

    plan = b.get_plan("co1")
    assert plan["plan"] == "pilot"
    assert plan["calc_limit"] == 100

    print("PASS: test_activate_pilot")


def test_pilot_allows_within_limits():
    """Pilot should allow calculations within 100 limit."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    now = datetime(2026, 3, 1, 12, 0, 0)
    b.activate_pilot("co1", now=now)

    check = b.check_allowance("co1", now=now)
    assert check["allowed"] is True
    assert check["remaining"] == 100

    # Use some
    for _ in range(50):
        b.increment_usage("co1")

    check = b.check_allowance("co1", now=now)
    assert check["allowed"] is True
    assert check["remaining"] == 50

    print("PASS: test_pilot_allows_within_limits")


def test_pilot_blocks_at_limit():
    """Pilot should block after 100 calculations."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    now = datetime(2026, 3, 1, 12, 0, 0)
    b.activate_pilot("co1", now=now)

    for _ in range(100):
        b.increment_usage("co1")

    check = b.check_allowance("co1", now=now)
    assert check["allowed"] is False
    assert check["reason"] == "limit"
    assert check["error"] == PILOT_EXPIRED_MSG

    print("PASS: test_pilot_blocks_at_limit")


def test_pilot_blocks_after_expiry():
    """Pilot should block after 14 days even if calcs remain."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    start = datetime(2026, 3, 1, 12, 0, 0)
    b.activate_pilot("co1", now=start)

    # Day 15 — expired
    future = start + timedelta(days=15)
    check = b.check_allowance("co1", now=future)
    assert check["allowed"] is False
    assert check["reason"] == "expired"

    # Day 13 — still valid
    within = start + timedelta(days=13)
    check = b.check_allowance("co1", now=within)
    assert check["allowed"] is True

    print("PASS: test_pilot_blocks_after_expiry")


def test_upgrade_plan():
    """Upgrading from pilot to starter should reset usage."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    now = datetime(2026, 3, 1, 12, 0, 0)
    b.activate_pilot("co1", now=now)

    for _ in range(100):
        b.increment_usage("co1")

    # Blocked on pilot
    check = b.check_allowance("co1", now=now)
    assert check["allowed"] is False

    # Upgrade to starter
    info = b.upgrade_plan("co1", "starter", now=now)
    assert info["plan"] == "starter"
    assert info["calc_limit"] == 300

    # Now allowed again
    check = b.check_allowance("co1", now=now)
    assert check["allowed"] is True
    assert check["remaining"] == 300

    print("PASS: test_upgrade_plan")


def test_remove_plan_makes_unlimited():
    """Removing plan should make company unlimited."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    b.activate_pilot("co1")
    assert b.get_plan("co1") is not None

    removed = b.remove_plan("co1")
    assert removed is True

    check = b.check_allowance("co1")
    assert check["allowed"] is True
    assert check["plan"] is None

    print("PASS: test_remove_plan_makes_unlimited")


def test_separate_companies():
    """Different companies have independent plans."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    now = datetime(2026, 3, 1, 12, 0, 0)
    b.activate_pilot("co1", now=now)
    b.activate_pilot("co2", now=now)

    for _ in range(100):
        b.increment_usage("co1")

    # co1 blocked, co2 still fine
    assert b.check_allowance("co1", now=now)["allowed"] is False
    assert b.check_allowance("co2", now=now)["allowed"] is True

    print("PASS: test_separate_companies")


def test_format_status():
    """Status formatting should work for pilot and unlimited."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    # Unlimited
    status = b.format_status("no-plan-co")
    assert "безлимитный" in status

    # Pilot — activate now so it doesn't expire during test
    b.activate_pilot("co1")
    for _ in range(10):
        b.increment_usage("co1")

    status = b.format_status("co1")
    assert "Пилот" in status
    assert "10/100" in status

    print("PASS: test_format_status")


def test_increment_returns_count():
    """Increment should return current count."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        b = Billing(db_path=Path(f.name))

    b.activate_pilot("co1")
    assert b.increment_usage("co1") == 1
    assert b.increment_usage("co1") == 2
    assert b.increment_usage("co1") == 3

    print("PASS: test_increment_returns_count")


if __name__ == "__main__":
    test_no_plan_is_unlimited()
    test_activate_pilot()
    test_pilot_allows_within_limits()
    test_pilot_blocks_at_limit()
    test_pilot_blocks_after_expiry()
    test_upgrade_plan()
    test_remove_plan_makes_unlimited()
    test_separate_companies()
    test_format_status()
    test_increment_returns_count()
    print("\n=== ALL BILLING TESTS PASSED ===")
