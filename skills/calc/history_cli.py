#!/usr/bin/env python3
"""Calculation history CLI.

Usage:
  python history_cli.py list --caller-id <id> --company <company>
  python history_cli.py recalc <calc_id> --caller-id <id> --company <company> --rates <path>

list   — show last 10 calculations for the user
recalc — re-run a past calculation with current rates
"""

import argparse
import json
import sys
from pathlib import Path

from skills.common.history import history
from skills.common.rate_limiter import limiter
from skills.common.logger import logger


def cmd_list(args):
    records = history.get_recent(args.caller_id, args.company, limit=10)
    output = history.format_history_list(records)
    print(json.dumps({"success": True, "summary": output}, ensure_ascii=False, indent=2))


def cmd_recalc(args):
    from skills.calc.calculator import CargoParams, calculate, load_rates

    record = history.get_by_id(args.calc_id, args.caller_id)
    if not record:
        print(json.dumps({
            "success": False,
            "error": "Расчёт не найден или принадлежит другому пользователю.",
        }))
        sys.exit(1)

    # Rate limit check
    check = limiter.check(args.caller_id, args.company, "calc")
    if not check["allowed"]:
        print(json.dumps({"success": False, "error": check["error"]}))
        sys.exit(1)

    rates = load_rates(args.rates)
    old_params = record["params"]
    params = CargoParams(**{
        k: v for k, v in old_params.items()
        if k in CargoParams.__dataclass_fields__
    })

    result = calculate(rates, params)

    # Log and save
    response_text = result.get("summary", result.get("error", ""))
    logger.log(args.caller_id, args.company, "calc", json.dumps(old_params, ensure_ascii=False), response_text)
    if result["success"]:
        limiter.increment(args.caller_id, args.company, "calc")
        calc_id = history.save(args.caller_id, args.company, old_params, result)
        result["calc_id"] = calc_id
        result["recalc_from"] = record["id"]

    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(description="Calculation history CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # list
    ls = sub.add_parser("list", help="Show recent calculations")
    ls.add_argument("--caller-id", required=True, help="Telegram user ID")
    ls.add_argument("--company", required=True, help="Company ID")

    # recalc
    rc = sub.add_parser("recalc", help="Re-calculate from history")
    rc.add_argument("calc_id", type=int, help="Calculation ID to repeat")
    rc.add_argument("--caller-id", required=True, help="Telegram user ID")
    rc.add_argument("--company", required=True, help="Company ID")
    rc.add_argument("--rates", required=True, help="Path to rates.json")

    args = p.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "recalc":
        cmd_recalc(args)


if __name__ == "__main__":
    main()
