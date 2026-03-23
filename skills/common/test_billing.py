"""Tests for billing module: limits, grace period, month reset."""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from skills.common.billing import BillingManager, PLAN_LIMITS, GRACE_PERIOD_DAYS


@pytest.fixture
def bm(tmp_path):
    """BillingManager with a temp DB."""
    db_path = tmp_path / "test_billing.db"
    manager = BillingManager(db_path=db_path)
    return manager


class TestGetSubscription:
    def test_no_subscription(self, bm):
        assert bm.get_subscription("nonexistent") is None

    def test_create_and_get(self, bm):
        bm.create_subscription("comp1", plan="business")
        sub = bm.get_subscription("comp1")
        assert sub is not None
        assert sub["plan"] == "business"
        assert sub["status"] == "active"
        assert sub["calc_count_month"] == 0


class TestCheckLimit:
    def test_no_subscription(self, bm):
        result = bm.check_limit("nonexistent")
        assert result["allowed"] is False
        assert "не найдена" in result["reason"]

    def test_active_under_limit(self, bm):
        bm.create_subscription("comp1", plan="start")
        result = bm.check_limit("comp1")
        assert result["allowed"] is True
        assert result["warning"] is False

    def test_blocked_status(self, bm):
        bm.create_subscription("comp1")
        bm.update_subscription("comp1", status="blocked")
        result = bm.check_limit("comp1")
        assert result["allowed"] is False
        assert "заблокирована" in result["reason"]

    def test_expired_status(self, bm):
        bm.create_subscription("comp1")
        bm.update_subscription("comp1", status="expired")
        result = bm.check_limit("comp1")
        assert result["allowed"] is False

    def test_warning_at_80_percent(self, bm):
        bm.create_subscription("comp1", plan="start")
        # Set usage to 80% of 300 = 240
        conn = bm._get_conn()
        bm._ensure_db(conn)
        conn.execute(
            "UPDATE subscriptions SET calc_count_month = 240 WHERE company_id = 'comp1'"
        )
        conn.commit()
        conn.close()
        result = bm.check_limit("comp1")
        assert result["allowed"] is True
        assert result["warning"] is True

    def test_over_limit_still_allowed(self, bm):
        bm.create_subscription("comp1", plan="start")
        conn = bm._get_conn()
        bm._ensure_db(conn)
        conn.execute(
            "UPDATE subscriptions SET calc_count_month = 305 WHERE company_id = 'comp1'"
        )
        conn.commit()
        conn.close()
        result = bm.check_limit("comp1")
        assert result["allowed"] is True
        assert result["over_limit"] is True

    def test_grace_period_allowed_with_warning(self, bm):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        bm.create_subscription("comp1", paid_until=yesterday)
        result = bm.check_limit("comp1")
        assert result["allowed"] is True
        assert result["warning"] is True

    def test_grace_period_expired(self, bm):
        long_ago = (datetime.now() - timedelta(days=GRACE_PERIOD_DAYS + 1)).strftime("%Y-%m-%d")
        bm.create_subscription("comp1", paid_until=long_ago)
        result = bm.check_limit("comp1")
        assert result["allowed"] is False
        assert "grace period" in result["reason"]

    def test_month_reset(self, bm):
        bm.create_subscription("comp1", plan="start")
        conn = bm._get_conn()
        bm._ensure_db(conn)
        conn.execute(
            "UPDATE subscriptions SET calc_count_month = 250, month = '2020-01' WHERE company_id = 'comp1'"
        )
        conn.commit()
        conn.close()
        result = bm.check_limit("comp1")
        # After reset, usage is 0, so no warning
        assert result["allowed"] is True
        assert result["warning"] is False
        # Verify counter was reset
        sub = bm.get_subscription("comp1")
        assert sub["calc_count_month"] == 0


class TestIncrementUsage:
    def test_no_subscription(self, bm):
        result = bm.increment_usage("nonexistent")
        assert result["ok"] is False

    def test_normal_increment(self, bm):
        bm.create_subscription("comp1", plan="start")
        result = bm.increment_usage("comp1")
        assert result["ok"] is True
        assert result["calc_count_month"] == 1

    def test_multiple_increments(self, bm):
        bm.create_subscription("comp1", plan="start")
        for i in range(5):
            result = bm.increment_usage("comp1")
        assert result["calc_count_month"] == 5

    def test_overage_tracking(self, bm):
        bm.create_subscription("comp1", plan="start")
        conn = bm._get_conn()
        bm._ensure_db(conn)
        conn.execute(
            "UPDATE subscriptions SET calc_count_month = 300 WHERE company_id = 'comp1'"
        )
        conn.commit()
        conn.close()
        result = bm.increment_usage("comp1")
        assert result["ok"] is True
        assert result["calc_count_month"] == 301
        assert result["overage_count"] == 1

    def test_month_reset_on_increment(self, bm):
        bm.create_subscription("comp1", plan="start")
        conn = bm._get_conn()
        bm._ensure_db(conn)
        conn.execute(
            "UPDATE subscriptions SET calc_count_month = 250, month = '2020-01' WHERE company_id = 'comp1'"
        )
        conn.commit()
        conn.close()
        result = bm.increment_usage("comp1")
        assert result["ok"] is True
        assert result["calc_count_month"] == 1
        assert result["overage_count"] == 0


class TestGetUsageStats:
    def test_no_subscription(self, bm):
        assert bm.get_usage_stats("nonexistent") is None

    def test_stats_format(self, bm):
        bm.create_subscription("comp1", plan="business")
        bm.increment_usage("comp1")
        stats = bm.get_usage_stats("comp1")
        assert stats["plan"] == "business"
        assert stats["count"] == 1
        assert stats["limit"] == 1000
        assert stats["usage_percent"] == 0.1
        assert stats["overage_count"] == 0


class TestUpdateSubscription:
    def test_update_plan(self, bm):
        bm.create_subscription("comp1", plan="start")
        assert bm.update_subscription("comp1", plan="pro") is True
        sub = bm.get_subscription("comp1")
        assert sub["plan"] == "pro"

    def test_update_nonexistent(self, bm):
        assert bm.update_subscription("nonexistent", plan="pro") is False

    def test_disallowed_fields_ignored(self, bm):
        bm.create_subscription("comp1")
        assert bm.update_subscription("comp1", calc_count_month=9999) is False


class TestPlanLimits:
    def test_all_plans_defined(self):
        assert "start" in PLAN_LIMITS
        assert "business" in PLAN_LIMITS
        assert "pro" in PLAN_LIMITS
        assert PLAN_LIMITS["start"] == 300
        assert PLAN_LIMITS["business"] == 1000
        assert PLAN_LIMITS["pro"] == 3000
