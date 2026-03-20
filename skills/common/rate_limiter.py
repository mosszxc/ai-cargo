"""SQLite-based rate limiter for cargo skills.

Tracks request counts per user per company per month.
Configurable limits per skill.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

LIMITER_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "rate_limits.db"

# Default limits per skill per user per month
DEFAULT_LIMITS = {
    "calc": 100,       # 100 calculations/month per user
    "parser": 50,      # 50 1688 parses/month per user
    "status": 500,     # 500 status lookups/month per user
    "admin": 200,      # 200 admin operations/month per manager
    "onboarding": 10,  # 10 onboarding attempts/month
}


class RateLimiter:
    def __init__(self, db_path: Path = LIMITER_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_counts (
                    user_id TEXT,
                    company_id TEXT,
                    skill TEXT,
                    month TEXT,
                    count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, company_id, skill, month)
                )
            """)
            conn.commit()

    def _current_month(self) -> str:
        return datetime.now().strftime("%Y-%m")

    def check(self, user_id: str, company_id: str, skill: str) -> dict:
        """Check if user is within rate limit.

        Returns:
            {"allowed": True, "count": N, "limit": M}
            or {"allowed": False, "count": N, "limit": M, "error": "..."}
        """
        month = self._current_month()
        limit = DEFAULT_LIMITS.get(skill, 100)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT count FROM rate_counts WHERE user_id=? AND company_id=? AND skill=? AND month=?",
                (str(user_id), company_id, skill, month),
            ).fetchone()

        count = row[0] if row else 0

        if count >= limit:
            return {
                "allowed": False,
                "count": count,
                "limit": limit,
                "error": f"Достигнут лимит запросов ({limit}/мес) для этой функции. Обратитесь к менеджеру для повышения.",
            }

        return {"allowed": True, "count": count, "limit": limit}

    def increment(self, user_id: str, company_id: str, skill: str) -> int:
        """Increment counter and return new count."""
        month = self._current_month()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO rate_counts (user_id, company_id, skill, month, count)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(user_id, company_id, skill, month)
                DO UPDATE SET count = count + 1
            """, (str(user_id), company_id, skill, month))
            conn.commit()

            row = conn.execute(
                "SELECT count FROM rate_counts WHERE user_id=? AND company_id=? AND skill=? AND month=?",
                (str(user_id), company_id, skill, month),
            ).fetchone()

        return row[0] if row else 1

    def get_usage(self, user_id: str, company_id: str) -> dict:
        """Get all usage stats for a user."""
        month = self._current_month()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT skill, count FROM rate_counts WHERE user_id=? AND company_id=? AND month=?",
                (str(user_id), company_id, month),
            ).fetchall()

        usage = {}
        for skill, count in rows:
            limit = DEFAULT_LIMITS.get(skill, 100)
            usage[skill] = {"count": count, "limit": limit}

        return usage


# Global instance
limiter = RateLimiter()
