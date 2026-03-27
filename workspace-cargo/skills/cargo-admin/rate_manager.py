#!/usr/bin/env python3
"""
rate_manager.py — CLI for managing cargo company rates.

Usage:
    python3 rate_manager.py show [--company <id>]
    python3 rate_manager.py show-config [--company <id>]
    python3 rate_manager.py update-rate <route> <transport> <rate_per_kg> <rate_per_m3> [--company <id>]
    python3 rate_manager.py add-route <route> <transport> <rate_per_kg> <rate_per_m3> [--days-min N] [--days-max N] [--company <id>]
    python3 rate_manager.py update-currency <pair> <value> [--company <id>]
    python3 rate_manager.py update-service <key> <value> [--company <id>]
    python3 rate_manager.py update-surcharge <category> <value> [--company <id>]
    python3 rate_manager.py init [--company <id>]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.access import require_manager
from skills.common.logger import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"

TRANSPORT_LABELS = {
    "express": "Быстрая",
    "medium": "Средняя",
    "standard": "Долгая",
    "auto": "Авто",
    "rail": "ЖД",
    "air": "Авиа",
}

TRANSPORT_EMOJI = {
    "express": "🚀",
    "medium": "🚛",
    "standard": "📦",
    "auto": "🚛",
    "rail": "🚂",
    "air": "✈️",
}

TRANSPORT_MAP = {
    "авто": "auto", "автомобиль": "auto", "фура": "auto", "машина": "auto",
    "жд": "rail", "железка": "rail", "поезд": "rail",
    "авиа": "air", "самолёт": "air", "самолет": "air",
    "быстрая": "express", "экспресс": "express",
    "средняя": "medium",
    "долгая": "standard", "обычная": "standard",
    "auto": "auto", "rail": "rail", "air": "air",
    "express": "express", "medium": "medium", "standard": "standard",
}


def get_rates_path(company_id: str) -> Path:
    return DATA_DIR / company_id / "rates.json"


def load_rates(company_id: str) -> dict:
    path = get_rates_path(company_id)
    if not path.exists():
        print(json.dumps({"ok": False, "error": f"Файл ставок не найден: {path}"}))
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_rates(company_id: str, data: dict):
    path = get_rates_path(company_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_transport(name: str) -> str:
    return TRANSPORT_MAP.get(name.lower(), name.lower())


def find_route(routes: dict, query: str) -> str | None:
    """Fuzzy-match route name."""
    for r in routes:
        if r == query or r.replace(" ", "") == query.replace(" ", ""):
            return r
        if query.lower() in r.lower():
            return r
    return None


def _format_transport_line(key: str, config: dict) -> str:
    """Format one transport as a human-readable line."""
    label = TRANSPORT_LABELS.get(key, key)
    emoji = TRANSPORT_EMOJI.get(key, "📦")
    days = f"{config['days_min']}–{config['days_max']} дн"

    if "rate_per_kg" in config and "rate_per_m3" in config:
        return f"{emoji} {label} ({days}) — ${config['rate_per_kg']:g}/кг · ${config['rate_per_m3']:g}/м³"
    elif "rate_per_kg" in config:
        return f"{emoji} {label} ({days}) — ${config['rate_per_kg']:g}/кг"
    elif "density_rates" in config:
        # Legacy format — show range
        rates = config["density_rates"]
        kg_rates = [r["rate_per_kg"] for r in rates if "rate_per_kg" in r]
        m3_rates = [r["rate_per_m3"] for r in rates if "rate_per_m3" in r]
        parts = []
        if kg_rates:
            parts.append(f"${min(kg_rates):g}–{max(kg_rates):g}/кг")
        if m3_rates:
            parts.append(f"${m3_rates[0]:g}/м³")
        return f"{emoji} {label} ({days}) — {' · '.join(parts)}"
    return f"{emoji} {label} ({days})"


def show_rates(company_id: str):
    """Show rates in a human-friendly format."""
    rates = load_rates(company_id)
    company = rates.get("company_name", company_id)
    lines = [f"📊 Ставки {company}", ""]

    for route_name, transports in rates.get("routes", {}).items():
        lines.append(f"**{route_name}:**")
        for t_key, t_config in transports.items():
            lines.append(f"  {_format_transport_line(t_key, t_config)}")
        lines.append("")

    currency = rates.get("currency", {})
    min_w = rates.get("min_weight_kg", 0)
    lines.append(f"💱 $1 = ¥{currency.get('usd_cny', '?')} = {currency.get('usd_rub', '?')}₽ | Мин. вес: {min_w} кг")

    formatted = "\n".join(lines)
    print(json.dumps({"ok": True, "formatted": formatted, "rates": rates}))


def show_config(company_id: str):
    """Show global configuration (currency, services, surcharges)."""
    rates = load_rates(company_id)
    config = {
        "currency": rates.get("currency", {}),
        "services": rates.get("services", {}),
        "category_surcharges": rates.get("category_surcharges", {})
    }
    print(json.dumps({"ok": True, "config": config}))


def update_rate(company_id: str, route: str, transport: str, rate_per_kg: float, rate_per_m3: float):
    """Update transport rates (two numbers: $/kg and $/m³)."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    matched_route = find_route(routes, route)
    if not matched_route:
        print(json.dumps({
            "ok": False,
            "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"
        }))
        sys.exit(1)

    transport_key = resolve_transport(transport)
    route_data = routes[matched_route]

    if transport_key not in route_data:
        print(json.dumps({
            "ok": False,
            "error": f"Транспорт '{transport}' не найден. Доступные: {list(route_data.keys())}"
        }))
        sys.exit(1)

    old_config = route_data[transport_key]
    old_kg = old_config.get("rate_per_kg", "?")
    old_m3 = old_config.get("rate_per_m3", "?")

    # Update to new simplified format, preserving days
    route_data[transport_key] = {
        "rate_per_kg": rate_per_kg,
        "rate_per_m3": rate_per_m3,
        "days_min": old_config.get("days_min", 15),
        "days_max": old_config.get("days_max", 25),
    }

    save_rates(company_id, rates)

    label = TRANSPORT_LABELS.get(transport_key, transport_key)
    print(json.dumps({
        "ok": True,
        "route": matched_route,
        "transport": transport_key,
        "old": {"rate_per_kg": old_kg, "rate_per_m3": old_m3},
        "new": {"rate_per_kg": rate_per_kg, "rate_per_m3": rate_per_m3},
        "formatted": f"✅ {label}: ${rate_per_kg:g}/кг · ${rate_per_m3:g}/м³",
    }))


def update_rate_kg(company_id: str, route: str, transport: str, rate_per_kg: float):
    """Update only the $/kg rate, keeping $/m³ unchanged."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    matched_route = find_route(routes, route)
    if not matched_route:
        print(json.dumps({"ok": False, "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"}))
        sys.exit(1)

    transport_key = resolve_transport(transport)
    if transport_key not in routes[matched_route]:
        print(json.dumps({"ok": False, "error": f"Транспорт '{transport}' не найден."}))
        sys.exit(1)

    config = routes[matched_route][transport_key]
    old_kg = config.get("rate_per_kg", "?")
    config["rate_per_kg"] = rate_per_kg
    # Remove legacy density_rates if present
    config.pop("density_rates", None)

    save_rates(company_id, rates)

    label = TRANSPORT_LABELS.get(transport_key, transport_key)
    m3 = config.get("rate_per_m3", "—")
    m3_str = f" · ${m3:g}/м³" if isinstance(m3, (int, float)) else ""
    print(json.dumps({
        "ok": True,
        "formatted": f"✅ {label}: ${rate_per_kg:g}/кг{m3_str} (было ${old_kg}/кг)",
    }))


def update_rate_m3(company_id: str, route: str, transport: str, rate_per_m3: float):
    """Update only the $/m³ rate, keeping $/kg unchanged."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    matched_route = find_route(routes, route)
    if not matched_route:
        print(json.dumps({"ok": False, "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"}))
        sys.exit(1)

    transport_key = resolve_transport(transport)
    if transport_key not in routes[matched_route]:
        print(json.dumps({"ok": False, "error": f"Транспорт '{transport}' не найден."}))
        sys.exit(1)

    config = routes[matched_route][transport_key]
    old_m3 = config.get("rate_per_m3", "?")
    config["rate_per_m3"] = rate_per_m3
    config.pop("density_rates", None)

    save_rates(company_id, rates)

    label = TRANSPORT_LABELS.get(transport_key, transport_key)
    kg = config.get("rate_per_kg", "—")
    kg_str = f"${kg:g}/кг · " if isinstance(kg, (int, float)) else ""
    print(json.dumps({
        "ok": True,
        "formatted": f"✅ {label}: {kg_str}${rate_per_m3:g}/м³ (было ${old_m3}/м³)",
    }))


def update_currency(company_id: str, pair: str, value: float):
    rates = load_rates(company_id)
    if "currency" not in rates:
        rates["currency"] = {}
    pair = pair.lower()
    if pair not in ["usd_cny", "usd_rub"]:
        print(json.dumps({"ok": False, "error": f"Неизвестная пара: {pair}. Доступны: usd_cny, usd_rub"}))
        sys.exit(1)
    old_value = rates["currency"].get(pair)
    rates["currency"][pair] = value
    save_rates(company_id, rates)
    print(json.dumps({"ok": True, "pair": pair, "old_value": old_value, "new_value": value}))


def update_service(company_id: str, key: str, value: float):
    rates = load_rates(company_id)
    if "services" not in rates:
        rates["services"] = {}
    key = key.lower()
    old_value = rates["services"].get(key)
    rates["services"][key] = value
    save_rates(company_id, rates)
    print(json.dumps({"ok": True, "service": key, "old_value": old_value, "new_value": value}))


def update_surcharge(company_id: str, category: str, value: float):
    rates = load_rates(company_id)
    if "category_surcharges" not in rates:
        rates["category_surcharges"] = {}
    category = category.lower()
    old_value = rates["category_surcharges"].get(category)
    rates["category_surcharges"][category] = value
    save_rates(company_id, rates)
    print(json.dumps({"ok": True, "category": category, "old_value": old_value, "new_value": value}))


def add_route(company_id: str, route: str, transport: str, rate_per_kg: float, rate_per_m3: float,
              days_min: int, days_max: int):
    rates = load_rates(company_id)
    if "routes" not in rates:
        rates["routes"] = {}

    transport_key = resolve_transport(transport)

    if route not in rates["routes"]:
        rates["routes"][route] = {}

    if transport_key in rates["routes"][route]:
        print(json.dumps({
            "ok": False,
            "error": f"Транспорт '{transport_key}' уже есть на маршруте '{route}'. Используйте update-rate."
        }))
        sys.exit(1)

    rates["routes"][route][transport_key] = {
        "rate_per_kg": rate_per_kg,
        "rate_per_m3": rate_per_m3,
        "days_min": days_min,
        "days_max": days_max,
    }

    save_rates(company_id, rates)

    label = TRANSPORT_LABELS.get(transport_key, transport_key)
    print(json.dumps({
        "ok": True,
        "route": route,
        "transport": transport_key,
        "formatted": f"✅ Добавлен: {route} — {label} ${rate_per_kg:g}/кг · ${rate_per_m3:g}/м³ ({days_min}–{days_max} дн)",
    }))


def delete_route(company_id: str, route: str, transport: str = None):
    """Delete a route or a transport from a route."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    matched = find_route(routes, route)
    if not matched:
        print(json.dumps({"ok": False, "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"}))
        sys.exit(1)

    if transport:
        # Delete specific transport from route
        t_key = resolve_transport(transport)
        if t_key not in routes[matched]:
            print(json.dumps({"ok": False, "error": f"Транспорт '{transport}' не найден на маршруте {matched}."}))
            sys.exit(1)
        del routes[matched][t_key]
        label = TRANSPORT_LABELS.get(t_key, t_key)
        # If route has no transports left, delete route too
        if not routes[matched]:
            del routes[matched]
            msg = f"✅ Удалён {label} с маршрута {matched}. Маршрут пуст — тоже удалён."
        else:
            msg = f"✅ Удалён {label} с маршрута {matched}."
    else:
        # Delete entire route
        del routes[matched]
        msg = f"✅ Маршрут {matched} удалён."

    save_rates(company_id, rates)
    print(json.dumps({"ok": True, "formatted": msg}))


def show_stats(company_id: str):
    """Show usage stats from dialog logs."""
    import sqlite3
    log_db = Path(__file__).resolve().parent.parent.parent / "data" / "logs.db"
    if not log_db.exists():
        print(json.dumps({"ok": True, "formatted": "📊 Пока нет данных — логи пусты."}))
        return

    with sqlite3.connect(log_db) as conn:
        conn.row_factory = sqlite3.Row

        # Total by skill
        by_skill = conn.execute("""
            SELECT skill_name, COUNT(*) as cnt
            FROM dialog_logs
            WHERE company_id = ?
            GROUP BY skill_name
            ORDER BY cnt DESC
        """, (company_id,)).fetchall()

        # Today
        today = conn.execute("""
            SELECT COUNT(*) as cnt FROM dialog_logs
            WHERE company_id = ? AND date(timestamp) = date('now')
        """, (company_id,)).fetchone()["cnt"]

        # This week
        week = conn.execute("""
            SELECT COUNT(*) as cnt FROM dialog_logs
            WHERE company_id = ? AND timestamp >= datetime('now', '-7 days')
        """, (company_id,)).fetchone()["cnt"]

        # This month
        month = conn.execute("""
            SELECT COUNT(*) as cnt FROM dialog_logs
            WHERE company_id = ? AND timestamp >= datetime('now', '-30 days')
        """, (company_id,)).fetchone()["cnt"]

        # Last 5 calcs
        recent = conn.execute("""
            SELECT user_id, message, timestamp
            FROM dialog_logs
            WHERE company_id = ? AND skill_name = 'calc'
            ORDER BY timestamp DESC LIMIT 5
        """, (company_id,)).fetchall()

    lines = ["📊 **Статистика**\n"]
    lines.append(f"Сегодня: {today} запросов")
    lines.append(f"За неделю: {week}")
    lines.append(f"За месяц: {month}\n")

    if by_skill:
        skill_labels = {"calc": "Расчёты", "admin": "Управление", "status": "Фуры"}
        lines.append("**По типам:**")
        for row in by_skill:
            label = skill_labels.get(row["skill_name"], row["skill_name"])
            lines.append(f"  {label}: {row['cnt']}")
        lines.append("")

    if recent:
        lines.append("**Последние расчёты:**")
        for row in recent:
            ts = row["timestamp"][11:16] if row["timestamp"] and len(row["timestamp"]) > 16 else "?"
            msg_raw = row["message"] or ""
            # Parse JSON params to human-readable
            try:
                params = json.loads(msg_raw)
                product = params.get("product", "?")
                pieces = params.get("pieces")
                weight = params.get("weight_kg") or (
                    (params.get("weight_per_piece_kg", 0) or 0) * (pieces or 1)
                )
                parts = [product[:30]]
                if pieces:
                    parts.append(f"{pieces} шт")
                if weight:
                    parts.append(f"{weight:g} кг")
                desc = ", ".join(parts)
            except (json.JSONDecodeError, TypeError):
                desc = msg_raw[:40]
            lines.append(f"  {ts} — {desc}")

    formatted = "\n".join(lines)
    print(json.dumps({"ok": True, "formatted": formatted}))


def show_route(company_id: str, route: str):
    rates = load_rates(company_id)
    routes = rates.get("routes", {})
    matched = find_route(routes, route)
    if not matched:
        print(json.dumps({"ok": False, "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"}))
        sys.exit(1)

    lines = [f"**{matched}:**"]
    for t_key, t_config in routes[matched].items():
        lines.append(f"  {_format_transport_line(t_key, t_config)}")

    print(json.dumps({"ok": True, "route": matched, "formatted": "\n".join(lines), "data": routes[matched]}))


def init_rates(company_id: str):
    path = get_rates_path(company_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print(json.dumps({"ok": False, "error": f"Файл уже существует: {path}"}))
        sys.exit(1)

    default_rates = {
        "company_name": company_id,
        "currency": {"usd_cny": 7.25, "usd_rub": 88.5, "display": "usd"},
        "min_weight_kg": 30,
        "routes": {
            "Гуанчжоу→Москва": {
                "express": {"rate_per_kg": 1.80, "rate_per_m3": 350, "days_min": 15, "days_max": 18},
                "medium": {"rate_per_kg": 1.60, "rate_per_m3": 310, "days_min": 18, "days_max": 25},
                "standard": {"rate_per_kg": 1.50, "rate_per_m3": 300, "days_min": 25, "days_max": 30},
            }
        },
        "category_surcharges": {"electronics": 1.5, "cosmetics": 1.0, "fragile": 1.2},
        "services": {
            "crating_pct": 40, "palletizing_pct": 16, "insurance_pct": 3,
            "inspection_cny_per_hour": 150, "repackaging_usd_per_unit": 3.5
        }
    }

    save_rates(company_id, default_rates)
    print(json.dumps({"ok": True, "path": str(path)}))


def main():
    parser = argparse.ArgumentParser(description="Rate manager for cargo companies")
    parser.add_argument("--company", default="test-company", help="Company ID")
    parser.add_argument("--caller-id", default="", help="Telegram ID")
    parser.add_argument("--no-auth", action="store_true", help="Skip access control (CLI/test)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show", help="Show all rates")

    p_sr = subparsers.add_parser("show-route", help="Show rates for a route")
    p_sr.add_argument("route")

    subparsers.add_parser("show-config", help="Show global config")
    subparsers.add_parser("stats", help="Show usage statistics")

    p_ur = subparsers.add_parser("update-rate", help="Update transport rate ($/kg $/m³)")
    p_ur.add_argument("route")
    p_ur.add_argument("transport")
    p_ur.add_argument("rate_per_kg", type=float, help="Rate per kg")
    p_ur.add_argument("rate_per_m3", type=float, help="Rate per m³")

    p_urk = subparsers.add_parser("update-rate-kg", help="Update only $/kg rate")
    p_urk.add_argument("route")
    p_urk.add_argument("transport")
    p_urk.add_argument("rate_per_kg", type=float)

    p_urm = subparsers.add_parser("update-rate-m3", help="Update only $/m³ rate")
    p_urm.add_argument("route")
    p_urm.add_argument("transport")
    p_urm.add_argument("rate_per_m3", type=float)

    p_uc = subparsers.add_parser("update-currency", help="Update exchange rate")
    p_uc.add_argument("pair")
    p_uc.add_argument("value", type=float)

    p_usv = subparsers.add_parser("update-service", help="Update service cost")
    p_usv.add_argument("key")
    p_usv.add_argument("value", type=float)

    p_usc = subparsers.add_parser("update-surcharge", help="Update category surcharge")
    p_usc.add_argument("category")
    p_usc.add_argument("value", type=float)

    p_dr = subparsers.add_parser("delete-route", help="Delete route or transport")
    p_dr.add_argument("route")
    p_dr.add_argument("--transport", default=None, help="Delete only this transport (keep route)")

    p_ar = subparsers.add_parser("add-route", help="Add new route+transport")
    p_ar.add_argument("route")
    p_ar.add_argument("transport")
    p_ar.add_argument("rate_per_kg", type=float)
    p_ar.add_argument("rate_per_m3", type=float)
    p_ar.add_argument("--days-min", type=int, default=15)
    p_ar.add_argument("--days-max", type=int, default=25)

    subparsers.add_parser("init", help="Create default rates.json")

    args = parser.parse_args()

    if not args.no_auth:
        denied = require_manager(args.caller_id, args.company)
        if denied:
            print(json.dumps(denied))
            sys.exit(1)

    if args.caller_id:
        cmd_args = " ".join(sys.argv[1:])
        logger.log(args.caller_id, args.company, "admin", f"{args.command} {cmd_args}", "")

    if args.command == "show":
        show_rates(args.company)
    elif args.command == "show-route":
        show_route(args.company, args.route)
    elif args.command == "show-config":
        show_config(args.company)
    elif args.command == "stats":
        show_stats(args.company)
    elif args.command == "delete-route":
        delete_route(args.company, args.route, args.transport)
    elif args.command == "update-rate":
        update_rate(args.company, args.route, args.transport, args.rate_per_kg, args.rate_per_m3)
    elif args.command == "update-rate-kg":
        update_rate_kg(args.company, args.route, args.transport, args.rate_per_kg)
    elif args.command == "update-rate-m3":
        update_rate_m3(args.company, args.route, args.transport, args.rate_per_m3)
    elif args.command == "update-currency":
        update_currency(args.company, args.pair, args.value)
    elif args.command == "update-service":
        update_service(args.company, args.key, args.value)
    elif args.command == "update-surcharge":
        update_surcharge(args.company, args.category, args.value)
    elif args.command == "add-route":
        add_route(args.company, args.route, args.transport, args.rate_per_kg, args.rate_per_m3,
                  args.days_min, args.days_max)
    elif args.command == "init":
        init_rates(args.company)


if __name__ == "__main__":
    main()
