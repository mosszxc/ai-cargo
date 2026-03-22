"""Calculation history storage.

Stores structured calculation results per user per company.
Uses SQLite, same pattern as logger.py and rate_limiter.py.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

HISTORY_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "history.db"


class CalculationHistory:
    def __init__(self, db_path: Path = HISTORY_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calculations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    company_id TEXT NOT NULL,
                    product TEXT,
                    params_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    summary TEXT,
                    total_usd REAL,
                    cheapest_transport TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_calc_user_company
                ON calculations (user_id, company_id, id DESC)
            """)
            conn.commit()

    def save(
        self,
        user_id: str,
        company_id: str,
        params: dict,
        result: dict,
    ) -> int:
        """Save a successful calculation. Returns the row id."""
        # Extract summary fields for quick display
        product = params.get("product", "груз")
        summary = result.get("summary", "")

        # Find cheapest transport
        results_list = result.get("results", [])
        cheapest_transport = None
        total_usd = None
        if results_list:
            cheapest = min(results_list, key=lambda r: r.get("total_usd", float("inf")))
            cheapest_transport = cheapest.get("transport")
            total_usd = cheapest.get("total_usd")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """INSERT INTO calculations
                   (user_id, company_id, product, params_json, result_json,
                    summary, total_usd, cheapest_transport)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(user_id),
                    company_id,
                    product,
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    summary,
                    total_usd,
                    cheapest_transport,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_recent(
        self,
        user_id: str,
        company_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Get last N calculations for a user."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, product, params_json, total_usd,
                          cheapest_transport, created_at
                   FROM calculations
                   WHERE user_id = ? AND company_id = ?
                   ORDER BY id DESC
                   LIMIT ?""",
                (str(user_id), company_id, limit),
            ).fetchall()

        return [
            {
                "id": row["id"],
                "product": row["product"],
                "params": json.loads(row["params_json"]),
                "total_usd": row["total_usd"],
                "cheapest_transport": row["cheapest_transport"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_by_id(self, calc_id: int, user_id: str) -> Optional[dict]:
        """Get a specific calculation by id (with user ownership check)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT id, user_id, company_id, product, params_json,
                          result_json, summary, total_usd,
                          cheapest_transport, created_at
                   FROM calculations
                   WHERE id = ? AND user_id = ?""",
                (calc_id, str(user_id)),
            ).fetchone()

        if not row:
            return None

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "company_id": row["company_id"],
            "product": row["product"],
            "params": json.loads(row["params_json"]),
            "result": json.loads(row["result_json"]),
            "summary": row["summary"],
            "total_usd": row["total_usd"],
            "cheapest_transport": row["cheapest_transport"],
            "created_at": row["created_at"],
        }

    def format_history_list(self, records: list[dict]) -> str:
        """Format history records as a readable Telegram message."""
        if not records:
            return "У вас пока нет расчётов."

        lines = ["**Ваши последние расчёты:**\n"]
        for i, rec in enumerate(records, 1):
            params = rec["params"]
            weight = params.get("weight_kg", "?")
            route = f"{params.get('origin', '?')}→{params.get('destination', '?')}"
            transport = rec.get("cheapest_transport", "?")
            total = rec.get("total_usd")
            total_str = f"${total:,.0f}" if total else "—"
            date = rec["created_at"][:10] if rec.get("created_at") else ""

            lines.append(
                f"{i}. **{rec['product']}** — {weight} кг, {route}\n"
                f"   {transport}: {total_str} | {date}\n"
                f"   /recalc_{rec['id']}"
            )

        lines.append("\nДля пересчёта по текущим ставкам — нажмите на ссылку расчёта.")
        return "\n".join(lines)


# Global instance
history = CalculationHistory()
