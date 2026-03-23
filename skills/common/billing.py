"""Billing / plan management for cargo companies.

Tracks company plans (pilot, starter, business, pro).
Pilot plan: 100 total calculations, 14 days — whichever comes first.
Companies without a plan record are treated as unlimited (legacy/paying).
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

BILLING_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "billing.db"

PLAN_DEFAULTS = {
    "pilot": {"calc_limit": 100, "duration_days": 14},
    "starter": {"calc_limit": 300, "duration_days": 30},
    "business": {"calc_limit": 1000, "duration_days": 30},
    "pro": {"calc_limit": 3000, "duration_days": 30},
}

PILOT_EXPIRED_MSG = (
    "Пилотный период завершён. "
    "Свяжитесь с нами для подключения тарифа и продолжения работы."
)


class Billing:
    def __init__(self, db_path: Path = BILLING_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS company_plans (
                    company_id TEXT PRIMARY KEY,
                    plan TEXT NOT NULL,
                    calc_limit INTEGER NOT NULL,
                    calc_used INTEGER DEFAULT 0,
                    started_at DATETIME NOT NULL,
                    expires_at DATETIME NOT NULL
                )
            """)
            conn.commit()

    def activate_pilot(self, company_id: str, now: Optional[datetime] = None) -> dict:
        """Activate pilot plan for a company. Returns plan info."""
        now = now or datetime.now()
        defaults = PLAN_DEFAULTS["pilot"]
        expires_at = now + timedelta(days=defaults["duration_days"])

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO company_plans (company_id, plan, calc_limit, calc_used, started_at, expires_at)
                VALUES (?, 'pilot', ?, 0, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    plan = 'pilot',
                    calc_limit = ?,
                    calc_used = 0,
                    started_at = ?,
                    expires_at = ?
            """, (
                company_id,
                defaults["calc_limit"],
                now.isoformat(),
                expires_at.isoformat(),
                defaults["calc_limit"],
                now.isoformat(),
                expires_at.isoformat(),
            ))
            conn.commit()

        return {
            "company_id": company_id,
            "plan": "pilot",
            "calc_limit": defaults["calc_limit"],
            "calc_used": 0,
            "started_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    def get_plan(self, company_id: str) -> Optional[dict]:
        """Get current plan for a company. Returns None if no plan (unlimited)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM company_plans WHERE company_id = ?",
                (company_id,),
            ).fetchone()

        if not row:
            return None

        return {
            "company_id": row["company_id"],
            "plan": row["plan"],
            "calc_limit": row["calc_limit"],
            "calc_used": row["calc_used"],
            "started_at": row["started_at"],
            "expires_at": row["expires_at"],
        }

    def check_allowance(self, company_id: str, now: Optional[datetime] = None) -> dict:
        """Check if company can perform a calculation.

        Returns:
            {"allowed": True, "remaining": N, "plan": "pilot"}
            or {"allowed": True, "plan": None}  -- no plan = unlimited
            or {"allowed": False, "reason": "expired"|"limit", "error": "..."}
        """
        plan = self.get_plan(company_id)

        if plan is None:
            return {"allowed": True, "plan": None}

        now = now or datetime.now()
        expires_at = datetime.fromisoformat(plan["expires_at"])

        if now >= expires_at:
            return {
                "allowed": False,
                "reason": "expired",
                "plan": plan["plan"],
                "error": PILOT_EXPIRED_MSG,
            }

        if plan["calc_used"] >= plan["calc_limit"]:
            return {
                "allowed": False,
                "reason": "limit",
                "plan": plan["plan"],
                "error": PILOT_EXPIRED_MSG,
            }

        remaining = plan["calc_limit"] - plan["calc_used"]
        return {
            "allowed": True,
            "plan": plan["plan"],
            "remaining": remaining,
            "calc_used": plan["calc_used"],
            "calc_limit": plan["calc_limit"],
        }

    def increment_usage(self, company_id: str) -> int:
        """Increment calc_used counter. Returns new count."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE company_plans SET calc_used = calc_used + 1 WHERE company_id = ?",
                (company_id,),
            )
            conn.commit()

            row = conn.execute(
                "SELECT calc_used FROM company_plans WHERE company_id = ?",
                (company_id,),
            ).fetchone()

        return row[0] if row else 0

    def upgrade_plan(self, company_id: str, plan: str, now: Optional[datetime] = None) -> dict:
        """Upgrade a company to a paid plan."""
        if plan not in PLAN_DEFAULTS:
            raise ValueError(f"Unknown plan: {plan}. Available: {list(PLAN_DEFAULTS.keys())}")

        now = now or datetime.now()
        defaults = PLAN_DEFAULTS[plan]
        expires_at = now + timedelta(days=defaults["duration_days"])

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO company_plans (company_id, plan, calc_limit, calc_used, started_at, expires_at)
                VALUES (?, ?, ?, 0, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    plan = ?,
                    calc_limit = ?,
                    calc_used = 0,
                    started_at = ?,
                    expires_at = ?
            """, (
                company_id,
                plan,
                defaults["calc_limit"],
                now.isoformat(),
                expires_at.isoformat(),
                plan,
                defaults["calc_limit"],
                now.isoformat(),
                expires_at.isoformat(),
            ))
            conn.commit()

        return {
            "company_id": company_id,
            "plan": plan,
            "calc_limit": defaults["calc_limit"],
            "calc_used": 0,
            "started_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    def remove_plan(self, company_id: str) -> bool:
        """Remove plan (make company unlimited). Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM company_plans WHERE company_id = ?",
                (company_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def format_status(self, company_id: str) -> str:
        """Format plan status as a human-readable message."""
        check = self.check_allowance(company_id)

        if check["plan"] is None:
            return "Тарифный план: безлимитный"

        plan = self.get_plan(company_id)
        plan_labels = {"pilot": "Пилот (бесплатно)", "starter": "Старт", "business": "Бизнес", "pro": "Про"}
        label = plan_labels.get(plan["plan"], plan["plan"])

        if not check["allowed"]:
            return f"Тарифный план: {label}\nСтатус: {check['error']}"

        remaining = check["remaining"]
        expires = plan["expires_at"][:10]
        return (
            f"Тарифный план: {label}\n"
            f"Расчётов использовано: {plan['calc_used']}/{plan['calc_limit']}\n"
            f"Осталось: {remaining}\n"
            f"Действует до: {expires}"
        )


# Global instance
billing = Billing()
