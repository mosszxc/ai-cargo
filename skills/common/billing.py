"""Billing: subscriptions, usage limits, and grace period logic.

Central DB at data/billing.db stores:
- subscriptions: company plans, usage counters, and payment status
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "billing.db"

PLAN_LIMITS = {
    "start": 300,
    "business": 1000,
    "pro": 3000,
}

GRACE_PERIOD_DAYS = 3
WARNING_THRESHOLD = 0.8


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection | None = None):
    """Create subscriptions table if it doesn't exist."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            company_id TEXT PRIMARY KEY,
            plan TEXT NOT NULL DEFAULT 'start',
            start_date TEXT NOT NULL,
            paid_until TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            calc_count_month INTEGER NOT NULL DEFAULT 0,
            overage_count INTEGER NOT NULL DEFAULT 0,
            month TEXT NOT NULL
        );
    """)
    conn.commit()
    if own_conn:
        conn.close()


class BillingManager:

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DB_PATH

    def _get_conn(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self, conn: sqlite3.Connection):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                company_id TEXT PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'start',
                start_date TEXT NOT NULL,
                paid_until TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                calc_count_month INTEGER NOT NULL DEFAULT 0,
                overage_count INTEGER NOT NULL DEFAULT 0,
                month TEXT NOT NULL
            );
        """)

    def get_subscription(self, company_id: str, conn: sqlite3.Connection | None = None) -> dict | None:
        """Get current subscription for a company. Returns dict or None."""
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
            self._ensure_db(conn)
        row = conn.execute(
            "SELECT * FROM subscriptions WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        if own_conn:
            conn.close()
        if row:
            return {k: row[k] for k in row.keys()}
        return None

    def create_subscription(self, company_id: str, plan: str = "start",
                            paid_until: str | None = None,
                            conn: sqlite3.Connection | None = None) -> dict:
        """Create a new subscription. Returns the subscription dict."""
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
            self._ensure_db(conn)
        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        if paid_until is None:
            paid_until = (now + timedelta(days=30)).strftime("%Y-%m-%d")
        try:
            conn.execute(
                """INSERT INTO subscriptions
                   (company_id, plan, start_date, paid_until, status, calc_count_month, overage_count, month)
                   VALUES (?, ?, ?, ?, 'active', 0, 0, ?)""",
                (company_id, plan, now.strftime("%Y-%m-%d"), paid_until, current_month),
            )
            conn.commit()
        finally:
            if own_conn:
                conn.close()
        return self.get_subscription(company_id)

    def check_limit(self, company_id: str, conn: sqlite3.Connection | None = None) -> dict:
        """Check if company can make a calculation.

        Returns dict with:
            allowed: bool - can proceed
            warning: bool - approaching limit or grace period
            reason: str - explanation if not allowed
        """
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
            self._ensure_db(conn)
        try:
            sub = self.get_subscription(company_id, conn)
            if not sub:
                return {"allowed": False, "warning": False, "reason": "Подписка не найдена"}

            if sub["status"] == "blocked":
                return {"allowed": False, "warning": False, "reason": "Подписка заблокирована"}

            today = datetime.now().date()
            paid_until = datetime.strptime(sub["paid_until"], "%Y-%m-%d").date()

            if sub["status"] == "expired":
                return {"allowed": False, "warning": False, "reason": "Подписка истекла"}

            # Grace period check
            in_grace = False
            if today > paid_until:
                grace_end = paid_until + timedelta(days=GRACE_PERIOD_DAYS)
                if today <= grace_end:
                    in_grace = True
                else:
                    return {"allowed": False, "warning": False, "reason": "Подписка истекла (grace period закончился)"}

            # Reset month counter if new month
            current_month = datetime.now().strftime("%Y-%m")
            if sub["month"] != current_month:
                conn.execute(
                    "UPDATE subscriptions SET calc_count_month = 0, overage_count = 0, month = ? WHERE company_id = ?",
                    (current_month, company_id),
                )
                conn.commit()
                sub["calc_count_month"] = 0
                sub["overage_count"] = 0

            limit = PLAN_LIMITS.get(sub["plan"], 300)
            usage = sub["calc_count_month"]
            at_warning = usage >= limit * WARNING_THRESHOLD
            over_limit = usage >= limit

            warning = in_grace or at_warning

            return {"allowed": True, "warning": warning, "over_limit": over_limit, "reason": None}
        finally:
            if own_conn:
                conn.close()

    def increment_usage(self, company_id: str, conn: sqlite3.Connection | None = None) -> dict:
        """Increment calc_count_month. Resets counter on new month. Tracks overage.

        Returns updated usage stats.
        """
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
            self._ensure_db(conn)
        try:
            sub = self.get_subscription(company_id, conn)
            if not sub:
                return {"ok": False, "error": "Подписка не найдена"}

            current_month = datetime.now().strftime("%Y-%m")

            # Reset on new month
            if sub["month"] != current_month:
                conn.execute(
                    "UPDATE subscriptions SET calc_count_month = 1, overage_count = 0, month = ? WHERE company_id = ?",
                    (current_month, company_id),
                )
                conn.commit()
                return {"ok": True, "calc_count_month": 1, "overage_count": 0}

            limit = PLAN_LIMITS.get(sub["plan"], 300)
            new_count = sub["calc_count_month"] + 1

            if new_count > limit:
                conn.execute(
                    "UPDATE subscriptions SET calc_count_month = ?, overage_count = overage_count + 1 WHERE company_id = ?",
                    (new_count, company_id),
                )
                conn.commit()
                updated = self.get_subscription(company_id, conn)
                return {"ok": True, "calc_count_month": new_count, "overage_count": updated["overage_count"]}
            else:
                conn.execute(
                    "UPDATE subscriptions SET calc_count_month = ? WHERE company_id = ?",
                    (new_count, company_id),
                )
                conn.commit()
                return {"ok": True, "calc_count_month": new_count, "overage_count": sub["overage_count"]}
        finally:
            if own_conn:
                conn.close()

    def get_usage_stats(self, company_id: str, conn: sqlite3.Connection | None = None) -> dict | None:
        """Get current usage statistics.

        Returns dict with count, limit, usage_percent, overage_count or None.
        """
        own_conn = conn is None
        if own_conn:
            conn = self._get_conn()
            self._ensure_db(conn)
        try:
            sub = self.get_subscription(company_id, conn)
            if not sub:
                return None

            limit = PLAN_LIMITS.get(sub["plan"], 300)
            count = sub["calc_count_month"]
            pct = round(count / limit * 100, 1) if limit > 0 else 0

            return {
                "company_id": company_id,
                "plan": sub["plan"],
                "count": count,
                "limit": limit,
                "usage_percent": pct,
                "overage_count": sub["overage_count"],
                "status": sub["status"],
                "paid_until": sub["paid_until"],
            }
        finally:
            if own_conn:
                conn.close()

    def update_subscription(self, company_id: str, **kwargs) -> bool:
        """Update subscription fields. Returns True if updated."""
        allowed = {"plan", "paid_until", "status"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        conn = self._get_conn()
        self._ensure_db(conn)
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [company_id]
        cursor = conn.execute(f"UPDATE subscriptions SET {sets} WHERE company_id = ?", vals)
        conn.commit()
        conn.close()
        return cursor.rowcount > 0


# Module-level singleton for convenience
billing = BillingManager()
