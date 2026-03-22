#!/usr/bin/env python3
"""
pipeline.py — Full auto-demo pipeline: URL → working demo instance.

Orchestrates: scrape → generate rates.json → create demo instance → output demo link.

Usage:
    python -m scripts.demo_pipeline.pipeline <url> [--company-id <id>]
    python -m scripts.demo_pipeline.pipeline --text-file <path> [--company-id <id>]
    python -m scripts.demo_pipeline.pipeline --manual '{"company_name": "...", "routes": [...]}'

Examples:
    # From a cargo company website
    python -m scripts.demo_pipeline.pipeline https://example-cargo.com/rates

    # From a text file (copy-pasted Telegram channel rates)
    python -m scripts.demo_pipeline.pipeline --text-file /tmp/cargo_rates.txt

    # Manual JSON input (skip scraping)
    python -m scripts.demo_pipeline.pipeline --manual rates_raw.json
"""

import json
import sys
import time
from pathlib import Path

from scripts.demo_pipeline.rate_scraper import scrape_rates, scrape_from_text
from scripts.demo_pipeline.rates_generator import generate_rates_json
from scripts.demo_pipeline.demo_creator import create_demo_instance, slugify


def run_pipeline(
    url: str = "",
    text_file: str = "",
    manual_json: str = "",
    company_id: str = "",
    api_key: str = "",
) -> dict:
    """
    Run the full demo pipeline.

    Args:
        url: Website/channel URL to scrape
        text_file: Path to text file with rates
        manual_json: Path to pre-scraped JSON or raw JSON string
        company_id: Override company ID (default: auto-generate from name)
        api_key: Anthropic API key (default: from env)

    Returns:
        Dict with demo instance details and demo link info.
    """
    start = time.time()
    source_url = url

    # Step 1: Get raw rate data
    print("=" * 60, file=sys.stderr)
    print("Step 1/4: Extracting rates...", file=sys.stderr)

    if manual_json:
        # Load from file or parse as JSON string
        path = Path(manual_json)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw_rates = json.load(f)
        else:
            raw_rates = json.loads(manual_json)
    elif text_file:
        with open(text_file, "r", encoding="utf-8") as f:
            text = f.read()
        raw_rates = scrape_from_text(text, api_key or None)
    elif url:
        raw_rates = scrape_rates(url, api_key or None)
    else:
        return {"ok": False, "error": "Provide --url, --text-file, or --manual"}

    print(f"  Company: {raw_rates.get('company_name', '?')}", file=sys.stderr)
    print(f"  Routes found: {len(raw_rates.get('routes', []))}", file=sys.stderr)

    # Step 2: Generate valid rates.json
    print("Step 2/4: Generating rates.json...", file=sys.stderr)
    rates_json = generate_rates_json(raw_rates)
    print(f"  Routes in rates.json: {list(rates_json['routes'].keys())}", file=sys.stderr)

    # Step 3: Create demo instance
    print("Step 3/4: Creating demo instance...", file=sys.stderr)
    cid = company_id or slugify(raw_rates.get("company_name", "unknown"))
    result = create_demo_instance(cid, rates_json, source_url)

    if not result.get("ok"):
        return result

    # Step 4: Generate demo info
    print("Step 4/4: Generating demo link...", file=sys.stderr)
    demo_info = generate_demo_info(result)

    elapsed = time.time() - start
    print("=" * 60, file=sys.stderr)
    print(f"Done in {elapsed:.1f}s", file=sys.stderr)

    return {
        "ok": True,
        "company_id": cid,
        "company_name": rates_json.get("company_name"),
        "routes": list(rates_json["routes"].keys()),
        "company_dir": result["company_dir"],
        "rates_path": result["rates_path"],
        "demo_info": demo_info,
        "elapsed_seconds": round(elapsed, 1),
    }


def generate_demo_info(creation_result: dict) -> dict:
    """Generate demo access info for the client."""
    cid = creation_result["company_id"]
    company_name = creation_result["company_name"]

    return {
        "company_id": cid,
        "calc_command": f"python3 skills/calc/calculator.py data/companies/{cid}/rates.json",
        "example_calc": (
            f'python3 skills/calc/calculator.py data/companies/{cid}/rates.json '
            f'\'{{"product":"одежда","weight_kg":500,"origin":"Гуанчжоу","destination":"Москва"}}\''
        ),
        "bot_usage": (
            f"Для подключения к Telegram-боту:\n"
            f"1. Установить company_id = '{cid}' в конфиге бота\n"
            f"2. Бот будет использовать ставки из data/companies/{cid}/rates.json"
        ),
        "message_template": (
            f"Здравствуйте! Подготовили демо калькулятора доставки "
            f"для {company_name}.\n\n"
            f"Бот рассчитает стоимость доставки по вашим ставкам — "
            f"авто, ЖД, авиа с учётом плотности груза.\n\n"
            f"Можете протестировать: отправьте боту описание груза "
            f"(вес, объём, маршрут) и получите расчёт."
        ),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Auto-demo pipeline: URL → working demo instance"
    )
    parser.add_argument("url", nargs="?", help="URL to scrape rates from")
    parser.add_argument("--text-file", help="Path to text file with rates")
    parser.add_argument("--manual", help="Path to pre-scraped JSON or raw JSON string")
    parser.add_argument("--company-id", default="", help="Override company ID")
    args = parser.parse_args()

    if not args.url and not args.text_file and not args.manual:
        parser.error("Provide URL, --text-file, or --manual")

    result = run_pipeline(
        url=args.url or "",
        text_file=args.text_file or "",
        manual_json=args.manual or "",
        company_id=args.company_id,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
