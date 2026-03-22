#!/usr/bin/env python3
"""
rate_manager.py — CLI for managing cargo company rates.

Usage:
    python3 rate_manager.py show [--company <id>]
    python3 rate_manager.py update-rate <route> <transport> <density_min> <new_rate> [--company <id>]
    python3 rate_manager.py update-simple-rate <route> <transport> <new_rate> [--company <id>]
    python3 rate_manager.py add-route <route> <transport> <rate> [--days-min N] [--days-max N] [--company <id>]
    python3 rate_manager.py show-route <route> [--company <id>]
    python3 rate_manager.py init [--company <id>]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.access import require_manager
from skills.common.analytics import get_company_stats, get_owner_summary, format_company_stats, format_owner_summary
from skills.common.logger import logger

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"


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


def show_rates(company_id: str):
    rates = load_rates(company_id)
    print(json.dumps({"ok": True, "rates": rates}))


def show_route(company_id: str, route: str):
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    # Try exact match first, then fuzzy
    matched_route = None
    for r in routes:
        if r == route or r.replace(" ", "") == route.replace(" ", ""):
            matched_route = r
            break
        if route.lower() in r.lower():
            matched_route = r
            break

    if not matched_route:
        print(json.dumps({
            "ok": False,
            "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"
        }))
        sys.exit(1)

    print(json.dumps({"ok": True, "route": matched_route, "data": routes[matched_route]}))


def show_config(company_id: str):
    """Show global configuration (currency, services, surcharges)."""
    rates = load_rates(company_id)
    config = {
        "currency": rates.get("currency", {}),
        "services": rates.get("services", {}),
        "category_surcharges": rates.get("category_surcharges", {})
    }
    print(json.dumps({"ok": True, "config": config}))


def update_currency(company_id: str, pair: str, value: float):
    """Update currency exchange rate."""
    rates = load_rates(company_id)
    if "currency" not in rates:
        rates["currency"] = {}
    
    pair = pair.lower()
    if pair not in ["usd_cny", "usd_rub"]:
        print(json.dumps({"ok": False, "error": f"Неизвестная валютная пара: {pair}. Доступны: usd_cny, usd_rub"}))
        sys.exit(1)
        
    old_value = rates["currency"].get(pair)
    rates["currency"][pair] = value
    save_rates(company_id, rates)
    
    print(json.dumps({
        "ok": True, 
        "pair": pair, 
        "old_value": old_value, 
        "new_value": value
    }))


def update_service(company_id: str, key: str, value: float):
    """Update service cost or percentage."""
    rates = load_rates(company_id)
    if "services" not in rates:
        rates["services"] = {}
        
    key = key.lower()
    known_services = [
        "insurance_pct", "crating_pct", "palletizing_pct", 
        "inspection_cny_per_hour", "repackaging_usd_per_unit"
    ]
    
    # Allow new keys, but warn if it looks like a typo of known ones
    
    old_value = rates["services"].get(key)
    rates["services"][key] = value
    save_rates(company_id, rates)
    
    print(json.dumps({
        "ok": True, 
        "service": key, 
        "old_value": old_value, 
        "new_value": value
    }))


def update_surcharge(company_id: str, category: str, value: float):
    """Update category surcharge multiplier."""
    rates = load_rates(company_id)
    if "category_surcharges" not in rates:
        rates["category_surcharges"] = {}
        
    category = category.lower()
    old_value = rates["category_surcharges"].get(category)
    rates["category_surcharges"][category] = value
    save_rates(company_id, rates)
    
    print(json.dumps({
        "ok": True, 
        "category": category, 
        "old_value": old_value, 
        "new_value": value
    }))


def update_rate(company_id: str, route: str, transport: str, density_min: int, new_rate: float):
    """Update a rate in density_rates for a specific transport on a route."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    # Find route (fuzzy match)
    matched_route = None
    for r in routes:
        if r == route or r.replace(" ", "") == route.replace(" ", ""):
            matched_route = r
            break
        if route.lower() in r.lower():
            matched_route = r
            break

    if not matched_route:
        print(json.dumps({
            "ok": False,
            "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"
        }))
        sys.exit(1)

    transport_lower = transport.lower()
    transport_map = {"авто": "auto", "жд": "rail", "авиа": "air", "auto": "auto", "rail": "rail", "air": "air"}
    transport_key = transport_map.get(transport_lower, transport_lower)

    route_data = routes[matched_route]
    if transport_key not in route_data:
        print(json.dumps({
            "ok": False,
            "error": f"Транспорт '{transport}' не найден на маршруте {matched_route}. Доступные: {list(route_data.keys())}"
        }))
        sys.exit(1)

    transport_data = route_data[transport_key]
    density_rates = transport_data.get("density_rates", [])

    updated = False
    for dr in density_rates:
        if dr.get("min_density") == density_min:
            if "rate_per_kg" in dr:
                old_rate = dr["rate_per_kg"]
                dr["rate_per_kg"] = new_rate
            elif "rate_per_m3" in dr:
                old_rate = dr["rate_per_m3"]
                dr["rate_per_m3"] = new_rate
            else:
                old_rate = None
            updated = True
            break

    if not updated:
        print(json.dumps({
            "ok": False,
            "error": f"Диапазон плотности с min_density={density_min} не найден"
        }))
        sys.exit(1)

    save_rates(company_id, rates)
    print(json.dumps({
        "ok": True,
        "route": matched_route,
        "transport": transport_key,
        "density_min": density_min,
        "old_rate": old_rate,
        "new_rate": new_rate,
    }))


def update_simple_rate(company_id: str, route: str, transport: str, new_rate: float):
    """Update a flat rate (like air which has rate_per_kg without density_rates)."""
    rates = load_rates(company_id)
    routes = rates.get("routes", {})

    matched_route = None
    for r in routes:
        if r == route or r.replace(" ", "") == route.replace(" ", ""):
            matched_route = r
            break
        if route.lower() in r.lower():
            matched_route = r
            break

    if not matched_route:
        print(json.dumps({
            "ok": False,
            "error": f"Маршрут '{route}' не найден. Доступные: {list(routes.keys())}"
        }))
        sys.exit(1)

    transport_lower = transport.lower()
    transport_map = {"авто": "auto", "жд": "rail", "авиа": "air", "auto": "auto", "rail": "rail", "air": "air"}
    transport_key = transport_map.get(transport_lower, transport_lower)

    route_data = routes[matched_route]
    if transport_key not in route_data:
        print(json.dumps({
            "ok": False,
            "error": f"Транспорт '{transport}' не найден. Доступные: {list(route_data.keys())}"
        }))
        sys.exit(1)

    transport_data = route_data[transport_key]

    if "rate_per_kg" in transport_data:
        old_rate = transport_data["rate_per_kg"]
        transport_data["rate_per_kg"] = new_rate
    elif "density_rates" in transport_data:
        # Update all density rates (when manager says "обнови авто до 2.90" without specifying density)
        old_rates = []
        for dr in transport_data["density_rates"]:
            if "rate_per_kg" in dr:
                old_rates.append(dr["rate_per_kg"])
                dr["rate_per_kg"] = new_rate
            elif "rate_per_m3" in dr:
                old_rates.append(dr["rate_per_m3"])
                dr["rate_per_m3"] = new_rate
        old_rate = old_rates
    else:
        old_rate = None

    save_rates(company_id, rates)
    print(json.dumps({
        "ok": True,
        "route": matched_route,
        "transport": transport_key,
        "old_rate": old_rate,
        "new_rate": new_rate,
    }))


def add_route(company_id: str, route: str, transport: str, rate: float, days_min: int, days_max: int):
    """Add a new route+transport with a simple rate."""
    rates = load_rates(company_id)
    if "routes" not in rates:
        rates["routes"] = {}

    transport_lower = transport.lower()
    transport_map = {"авто": "auto", "жд": "rail", "авиа": "air", "auto": "auto", "rail": "rail", "air": "air"}
    transport_key = transport_map.get(transport_lower, transport_lower)

    if route not in rates["routes"]:
        rates["routes"][route] = {}

    if transport_key in rates["routes"][route]:
        print(json.dumps({
            "ok": False,
            "error": f"Транспорт '{transport_key}' уже существует на маршруте '{route}'. Используйте update-rate для обновления."
        }))
        sys.exit(1)

    rates["routes"][route][transport_key] = {
        "rate_per_kg": rate,
        "days_min": days_min,
        "days_max": days_max,
    }

    save_rates(company_id, rates)
    print(json.dumps({
        "ok": True,
        "route": route,
        "transport": transport_key,
        "rate": rate,
        "days_min": days_min,
        "days_max": days_max,
    }))


def init_rates(company_id: str):
    """Create a default rates.json for a company."""
    path = get_rates_path(company_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        print(json.dumps({"ok": False, "error": f"Файл уже существует: {path}"}))
        sys.exit(1)

    default_rates = {
        "company_name": company_id,
        "currency": {
            "usd_cny": 7.25,
            "usd_rub": 88.5,
            "display": "usd"
        },
        "min_weight_kg": 30,
        "routes": {
            "Гуанчжоу→Москва": {
                "auto": {
                    "density_rates": [
                        {"min_density": 400, "max_density": 9999, "rate_per_kg": 1.80},
                        {"min_density": 200, "max_density": 399, "rate_per_kg": 2.80},
                        {"min_density": 100, "max_density": 199, "rate_per_kg": 3.50},
                        {"min_density": 0, "max_density": 99, "rate_per_m3": 350}
                    ],
                    "days_min": 18,
                    "days_max": 25
                },
                "rail": {
                    "density_rates": [
                        {"min_density": 200, "max_density": 9999, "rate_per_kg": 2.30},
                        {"min_density": 0, "max_density": 199, "rate_per_m3": 300}
                    ],
                    "days_min": 25,
                    "days_max": 35
                },
                "air": {
                    "rate_per_kg": 6.50,
                    "days_min": 5,
                    "days_max": 7
                }
            }
        },
        "category_surcharges": {
            "electronics": 1.5,
            "cosmetics": 1.0,
            "fragile": 1.2
        },
        "services": {
            "crating_pct": 40,
            "palletizing_pct": 16,
            "insurance_pct": 3,
            "inspection_cny_per_hour": 150,
            "repackaging_usd_per_unit": 3.5
        }
    }

    save_rates(company_id, default_rates)
    print(json.dumps({"ok": True, "path": str(path)}))


def main():
    parser = argparse.ArgumentParser(description="Rate manager for cargo companies")
    parser.add_argument("--company", default="test-company", help="Company ID")
    parser.add_argument("--caller-id", default="", help="Telegram ID of caller for access control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # show
    subparsers.add_parser("show", help="Show all rates")

    # show-route
    p_sr = subparsers.add_parser("show-route", help="Show rates for a route")
    p_sr.add_argument("route", help="Route name")

    # show-config
    subparsers.add_parser("show-config", help="Show global configuration (currency, services)")

    # update-currency
    p_uc = subparsers.add_parser("update-currency", help="Update exchange rate (usd_cny/usd_rub)")
    p_uc.add_argument("pair", help="Currency pair (usd_cny, usd_rub)")
    p_uc.add_argument("value", type=float, help="New rate")

    # update-service
    p_usv = subparsers.add_parser("update-service", help="Update service cost (insurance, crating)")
    p_usv.add_argument("key", help="Service key (insurance_pct, crating_pct)")
    p_usv.add_argument("value", type=float, help="New value")

    # update-surcharge
    p_usc = subparsers.add_parser("update-surcharge", help="Update category surcharge")
    p_usc.add_argument("category", help="Category name (electronics, fragile)")
    p_usc.add_argument("value", type=float, help="Multiplier (e.g. 1.5)")

    # update-rate (density-specific)
    p_ur = subparsers.add_parser("update-rate", help="Update rate for density range")
    p_ur.add_argument("route", help="Route name")
    p_ur.add_argument("transport", help="Transport type: auto/rail/air/авто/жд/авиа")
    p_ur.add_argument("density_min", type=int, help="Min density of range to update")
    p_ur.add_argument("new_rate", type=float, help="New rate value")

    # update-simple-rate (flat or all density rates)
    p_us = subparsers.add_parser("update-simple-rate", help="Update flat rate or all density rates")
    p_us.add_argument("route", help="Route name")
    p_us.add_argument("transport", help="Transport type")
    p_us.add_argument("new_rate", type=float, help="New rate value")

    # add-route
    p_ar = subparsers.add_parser("add-route", help="Add new route+transport")
    p_ar.add_argument("route", help="Route name, e.g. 'Иу→СПб'")
    p_ar.add_argument("transport", help="Transport type")
    p_ar.add_argument("rate", type=float, help="Rate per kg")
    p_ar.add_argument("--days-min", type=int, default=15, help="Min delivery days")
    p_ar.add_argument("--days-max", type=int, default=25, help="Max delivery days")

    # init
    subparsers.add_parser("init", help="Create default rates.json")

    # analytics (per-company)
    p_an = subparsers.add_parser("analytics", help="Usage analytics for this company")
    p_an.add_argument("--period", default="month", choices=["day", "week", "month", "all"],
                       help="Time period: day/week/month/all")

    # analytics-all (owner: cross-company)
    p_aa = subparsers.add_parser("analytics-all", help="Cross-company analytics (owner only)")
    p_aa.add_argument("--period", default="month", choices=["day", "week", "month", "all"],
                       help="Time period: day/week/month/all")

    args = parser.parse_args()

    # Access control: all rate_manager commands are manager-only
    denied = require_manager(args.caller_id, args.company)
    if denied:
        print(json.dumps(denied))
        sys.exit(1)

    # Log the command
    if args.caller_id:
        cmd_args = " ".join(sys.argv[1:])
        logger.log(args.caller_id, args.company, "admin", f"{args.command} {cmd_args}", "")

    if args.command == "show":
        show_rates(args.company)
    elif args.command == "show-route":
        show_route(args.company, args.route)
    elif args.command == "show-config":
        show_config(args.company)
    elif args.command == "update-currency":
        update_currency(args.company, args.pair, args.value)
    elif args.command == "update-service":
        update_service(args.company, args.key, args.value)
    elif args.command == "update-surcharge":
        update_surcharge(args.company, args.category, args.value)
    elif args.command == "update-rate":
        update_rate(args.company, args.route, args.transport, args.density_min, args.new_rate)
    elif args.command == "update-simple-rate":
        update_simple_rate(args.company, args.route, args.transport, args.new_rate)
    elif args.command == "add-route":
        add_route(args.company, args.route, args.transport, args.rate, args.days_min, args.days_max)
    elif args.command == "init":
        init_rates(args.company)
    elif args.command == "analytics":
        stats = get_company_stats(args.company, args.period)
        formatted = format_company_stats(args.company, stats)
        print(json.dumps({"ok": True, "formatted": formatted, "data": stats}))
    elif args.command == "analytics-all":
        stats = get_owner_summary(args.period)
        formatted = format_owner_summary(stats)
        print(json.dumps({"ok": True, "formatted": formatted, "data": stats}))


if __name__ == "__main__":
    main()
