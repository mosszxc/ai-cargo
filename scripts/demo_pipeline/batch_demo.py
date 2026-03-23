#!/usr/bin/env python3
"""
batch_demo.py — Generate demo instances for all target companies.

Reads target_companies.json, attempts to scrape rates from each company's
website/channel, falls back to pre-researched rates if scraping fails,
and creates demo instances for all companies.

Usage:
    python -m scripts.demo_pipeline.batch_demo [--scrape] [--company-id <id>]

Options:
    --scrape        Attempt live scraping before fallback (requires ANTHROPIC_API_KEY)
    --company-id    Process only a specific company from the target list
    --dry-run       Show what would be created without writing files
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

from scripts.demo_pipeline.rates_generator import generate_rates_json
from scripts.demo_pipeline.demo_creator import create_demo_instance
from scripts.demo_pipeline.pipeline import generate_demo_info

TARGET_FILE = Path(__file__).parent / "target_companies.json"


def load_targets(company_id: Optional[str] = None) -> list:
    """Load target companies config."""
    with open(TARGET_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)
    if company_id:
        targets = [t for t in targets if t["company_id"] == company_id]
    return targets


def try_scrape(target: dict, api_key: Optional[str] = None) -> Optional[dict]:
    """Attempt to scrape rates from target's source. Returns None on failure."""
    source_url = target.get("source_url", "")
    source_type = target.get("source_type", "website")

    if source_type == "telegram":
        print(f"  Skipping scrape for Telegram source: {source_url}", file=sys.stderr)
        return None

    if not source_url or not source_url.startswith("http"):
        return None

    try:
        from scripts.demo_pipeline.rate_scraper import scrape_rates
        print(f"  Scraping: {source_url}", file=sys.stderr)
        raw = scrape_rates(source_url, api_key)
        routes = raw.get("routes", [])
        if not routes:
            print(f"  No routes extracted from scraping", file=sys.stderr)
            return None
        print(f"  Scraped {len(routes)} routes", file=sys.stderr)
        return raw
    except Exception as e:
        print(f"  Scrape failed: {e}", file=sys.stderr)
        return None


def process_target(target: dict, scrape: bool = False, dry_run: bool = False) -> dict:
    """Process a single target company: scrape or fallback → generate → create demo."""
    company_id = target["company_id"]
    company_name = target["company_name"]
    source_url = target.get("source_url", "")

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Processing: {company_name} ({company_id})", file=sys.stderr)

    # Step 1: Get raw rates (scrape or fallback)
    raw_rates = None
    source = "fallback"

    if scrape:
        raw_rates = try_scrape(target)
        if raw_rates:
            source = "scraped"

    if raw_rates is None:
        raw_rates = target.get("fallback_rates")
        if not raw_rates:
            return {"ok": False, "company_id": company_id, "error": "No rates available"}

    # Step 2: Generate valid rates.json
    rates_json = generate_rates_json(raw_rates)
    routes = list(rates_json["routes"].keys())
    print(f"  Source: {source}", file=sys.stderr)
    print(f"  Routes: {routes}", file=sys.stderr)

    if dry_run:
        return {
            "ok": True,
            "company_id": company_id,
            "company_name": company_name,
            "source": source,
            "routes": routes,
            "dry_run": True,
        }

    # Step 3: Create demo instance
    result = create_demo_instance(company_id, rates_json, source_url)
    if not result.get("ok"):
        return result

    # Step 4: Generate demo info
    demo_info = generate_demo_info(result)

    return {
        "ok": True,
        "company_id": company_id,
        "company_name": company_name,
        "source": source,
        "routes": routes,
        "company_dir": result["company_dir"],
        "rates_path": result["rates_path"],
        "demo_info": demo_info,
    }


def run_batch(
    scrape: bool = False,
    company_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Run batch demo generation for all target companies."""
    start = time.time()
    targets = load_targets(company_id)

    if not targets:
        return {"ok": False, "error": f"No targets found (filter: {company_id})"}

    results = []
    success = 0
    failed = 0

    for target in targets:
        result = process_target(target, scrape=scrape, dry_run=dry_run)
        results.append(result)
        if result.get("ok"):
            success += 1
        else:
            failed += 1

    elapsed = time.time() - start

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Batch complete: {success} ok, {failed} failed in {elapsed:.1f}s", file=sys.stderr)

    return {
        "ok": failed == 0,
        "total": len(results),
        "success": success,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 1),
        "results": results,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch demo generation for target companies")
    parser.add_argument("--scrape", action="store_true",
                        help="Attempt live scraping before fallback")
    parser.add_argument("--company-id", default=None,
                        help="Process only this company")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without creating files")
    args = parser.parse_args()

    result = run_batch(
        scrape=args.scrape,
        company_id=args.company_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
