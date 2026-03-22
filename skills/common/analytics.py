"""Usage analytics: per-company and cross-company metrics from dialog_logs and rate_counts.

Queries:
- Calculations count, unique clients, top routes per company
- Time-based filtering: day / week / month / custom
- Owner-level summary across all companies
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

LOG_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "logs.db"
RATE_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "rate_limits.db"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"


def _get_log_conn() -> sqlite3.Connection | None:
    if not LOG_DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(LOG_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _get_rate_conn() -> sqlite3.Connection | None:
    if not RATE_DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(RATE_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _period_filter(period: str) -> str:
    """Return SQL datetime threshold for a period name."""
    if period == "day":
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    elif period == "week":
        return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    elif period == "month":
        return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    elif period == "all":
        return "2000-01-01 00:00:00"
    else:
        return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")


def get_company_stats(company_id: str, period: str = "month") -> dict:
    """Get usage stats for a single company.

    Returns:
        {
            "total_requests": int,
            "calculations": int,
            "unique_clients": int,
            "by_skill": {skill: count},
            "top_routes": [(route, count)],
            "recent_calcs": [{user_id, params, timestamp}],
            "period": str,
        }
    """
    conn = _get_log_conn()
    if not conn:
        return {"total_requests": 0, "calculations": 0, "unique_clients": 0,
                "by_skill": {}, "top_routes": [], "recent_calcs": [], "period": period}

    since = _period_filter(period)

    try:
        # Total requests
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM dialog_logs WHERE company_id = ? AND timestamp >= ?",
            (company_id, since),
        ).fetchone()["cnt"]

        # Calculations only
        calcs = conn.execute(
            "SELECT COUNT(*) as cnt FROM dialog_logs WHERE company_id = ? AND skill_name = 'calc' AND timestamp >= ?",
            (company_id, since),
        ).fetchone()["cnt"]

        # Unique clients
        unique = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as cnt FROM dialog_logs WHERE company_id = ? AND timestamp >= ?",
            (company_id, since),
        ).fetchone()["cnt"]

        # By skill
        by_skill_rows = conn.execute(
            "SELECT skill_name, COUNT(*) as cnt FROM dialog_logs WHERE company_id = ? AND timestamp >= ? GROUP BY skill_name ORDER BY cnt DESC",
            (company_id, since),
        ).fetchall()
        by_skill = {row["skill_name"]: row["cnt"] for row in by_skill_rows}

        # Top routes (parse calc message JSON)
        route_rows = conn.execute(
            "SELECT message FROM dialog_logs WHERE company_id = ? AND skill_name = 'calc' AND timestamp >= ?",
            (company_id, since),
        ).fetchall()

        route_counts: dict[str, int] = {}
        for row in route_rows:
            try:
                params = json.loads(row["message"])
                route = params.get("route", "unknown")
                route_counts[route] = route_counts.get(route, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_routes = sorted(route_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        # Recent calculations (last 5)
        recent_rows = conn.execute(
            "SELECT user_id, message, timestamp FROM dialog_logs WHERE company_id = ? AND skill_name = 'calc' ORDER BY timestamp DESC LIMIT 5",
            (company_id,),
        ).fetchall()

        recent_calcs = []
        for row in recent_rows:
            entry = {"user_id": row["user_id"], "timestamp": row["timestamp"]}
            try:
                params = json.loads(row["message"])
                entry["product"] = params.get("product", "?")[:30]
                entry["weight_kg"] = params.get("weight_kg") or (
                    (params.get("weight_per_piece_kg", 0) or 0) * (params.get("pieces", 1) or 1)
                )
                entry["route"] = params.get("route", "?")
            except (json.JSONDecodeError, TypeError):
                entry["raw"] = (row["message"] or "")[:40]
            recent_calcs.append(entry)

        return {
            "total_requests": total,
            "calculations": calcs,
            "unique_clients": unique,
            "by_skill": by_skill,
            "top_routes": top_routes,
            "recent_calcs": recent_calcs,
            "period": period,
        }
    finally:
        conn.close()


def get_owner_summary(period: str = "month") -> dict:
    """Get cross-company summary for the bot owner.

    Returns:
        {
            "total_requests": int,
            "total_calculations": int,
            "total_unique_clients": int,
            "companies": [{company_id, requests, calculations, unique_clients}],
            "period": str,
        }
    """
    conn = _get_log_conn()
    if not conn:
        return {"total_requests": 0, "total_calculations": 0,
                "total_unique_clients": 0, "companies": [], "period": period}

    since = _period_filter(period)

    try:
        # Overall totals
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM dialog_logs WHERE timestamp >= ?",
            (since,),
        ).fetchone()["cnt"]

        total_calcs = conn.execute(
            "SELECT COUNT(*) as cnt FROM dialog_logs WHERE skill_name = 'calc' AND timestamp >= ?",
            (since,),
        ).fetchone()["cnt"]

        total_unique = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as cnt FROM dialog_logs WHERE timestamp >= ?",
            (since,),
        ).fetchone()["cnt"]

        # Per-company breakdown
        company_rows = conn.execute("""
            SELECT company_id,
                   COUNT(*) as requests,
                   SUM(CASE WHEN skill_name = 'calc' THEN 1 ELSE 0 END) as calculations,
                   COUNT(DISTINCT user_id) as unique_clients
            FROM dialog_logs
            WHERE timestamp >= ?
            GROUP BY company_id
            ORDER BY requests DESC
        """, (since,)).fetchall()

        companies = [
            {
                "company_id": row["company_id"],
                "requests": row["requests"],
                "calculations": row["calculations"],
                "unique_clients": row["unique_clients"],
            }
            for row in company_rows
        ]

        return {
            "total_requests": total,
            "total_calculations": total_calcs,
            "total_unique_clients": total_unique,
            "companies": companies,
            "period": period,
        }
    finally:
        conn.close()


def format_company_stats(company_id: str, stats: dict) -> str:
    """Format company stats as Telegram-friendly markdown."""
    period_labels = {"day": "за сегодня", "week": "за неделю", "month": "за месяц", "all": "за всё время"}
    period_label = period_labels.get(stats["period"], f"за {stats['period']}")

    lines = [f"📊 **Аналитика {company_id}** ({period_label})\n"]

    lines.append(f"Всего запросов: {stats['total_requests']}")
    lines.append(f"Расчётов: {stats['calculations']}")
    lines.append(f"Уникальных клиентов: {stats['unique_clients']}")

    if stats["by_skill"]:
        skill_labels = {"calc": "Расчёты", "admin": "Управление", "status": "Фуры",
                        "onboarding": "Онбординг", "parser": "Парсер 1688"}
        lines.append("\n**По типам:**")
        for skill, count in stats["by_skill"].items():
            label = skill_labels.get(skill, skill)
            lines.append(f"  {label}: {count}")

    if stats["top_routes"]:
        lines.append("\n**Топ маршруты:**")
        for route, count in stats["top_routes"]:
            lines.append(f"  {route}: {count} расч.")

    if stats["recent_calcs"]:
        lines.append("\n**Последние расчёты:**")
        for calc in stats["recent_calcs"]:
            ts = calc["timestamp"][11:16] if calc.get("timestamp") and len(calc["timestamp"]) > 16 else "?"
            if "product" in calc:
                weight = f", {calc['weight_kg']:g} кг" if calc.get("weight_kg") else ""
                lines.append(f"  {ts} — {calc['product']}{weight}")
            else:
                lines.append(f"  {ts} — {calc.get('raw', '?')}")

    return "\n".join(lines)


def format_owner_summary(stats: dict) -> str:
    """Format owner summary as Telegram-friendly markdown."""
    period_labels = {"day": "за сегодня", "week": "за неделю", "month": "за месяц", "all": "за всё время"}
    period_label = period_labels.get(stats["period"], f"за {stats['period']}")

    lines = [f"📊 **Сводка по всем компаниям** ({period_label})\n"]

    lines.append(f"Всего запросов: {stats['total_requests']}")
    lines.append(f"Расчётов: {stats['total_calculations']}")
    lines.append(f"Уникальных клиентов: {stats['total_unique_clients']}")

    if stats["companies"]:
        lines.append("\n**По компаниям:**")
        for c in stats["companies"]:
            lines.append(
                f"  **{c['company_id']}** — {c['requests']} запр., "
                f"{c['calculations']} расч., {c['unique_clients']} клиент."
            )

    return "\n".join(lines)
