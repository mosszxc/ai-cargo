#!/usr/bin/env python3
"""
demo_creator.py — Create a demo company instance with rates, config, and DB.

Sets up the full data directory structure so the bot can serve
calculations for this demo company immediately.

Usage:
    python -m scripts.demo_pipeline.demo_creator <company_id> <rates_json_path>
    python -m scripts.demo_pipeline.demo_creator <company_id> --from-raw <raw_rates.json>
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "companies"
TRUCK_MANAGER = PROJECT_ROOT / "skills" / "status" / "truck_manager.py"

sys.path.insert(0, str(PROJECT_ROOT))
from skills.common.billing import Billing


def slugify(name: str) -> str:
    """Create a URL-safe company ID from name."""
    slug = name.lower().strip()
    # Transliterate common Russian chars
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    result = ""
    for c in slug:
        result += translit.get(c, c)
    result = re.sub(r"[^a-z0-9]+", "-", result).strip("-")
    return f"demo-{result}" if result else f"demo-{int(datetime.now().timestamp())}"


def create_demo_instance(
    company_id: str,
    rates: dict,
    source_url: str = "",
) -> dict:
    """Create a full demo company directory with rates.json, config.json, and DB."""
    company_dir = DATA_DIR / company_id
    company_dir.mkdir(parents=True, exist_ok=True)

    # Save rates.json
    rates_path = company_dir / "rates.json"
    with open(rates_path, "w", encoding="utf-8") as f:
        json.dump(rates, f, ensure_ascii=False, indent=2)

    # Generate config.json
    config = {
        "company_name": rates.get("company_name", company_id),
        "company_id": company_id,
        "is_demo": True,
        "source_url": source_url,
        "created_at": datetime.now().isoformat(),
        "manager_telegram_id": "",
        "client_bot_token_ref": "TG_CLIENT_BOT_TOKEN",
        "manager_bot_token_ref": "TG_MANAGER_BOT_TOKEN",
    }
    config_path = company_dir / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    # Initialize trucks.db
    db_path = company_dir / "trucks.db"
    db_result = _init_db(db_path, company_id)

    # Activate pilot plan (100 free calculations, 14 days)
    billing_instance = Billing()
    pilot_info = billing_instance.activate_pilot(company_id)

    return {
        "ok": True,
        "company_id": company_id,
        "company_name": rates.get("company_name", company_id),
        "company_dir": str(company_dir),
        "rates_path": str(rates_path),
        "config_path": str(config_path),
        "db_path": str(db_path),
        "db_init": db_result,
        "pilot": pilot_info,
        "routes": list(rates.get("routes", {}).keys()),
    }


def _init_db(db_path: Path, company_id: str) -> dict:
    """Initialize SQLite database for the demo company."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trucks (
                id TEXT PRIMARY KEY,
                route TEXT,
                status TEXT DEFAULT 'warehouse',
                status_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                company_id TEXT
            );
            CREATE TABLE IF NOT EXISTS truck_clients (
                truck_id TEXT,
                client_telegram_id TEXT,
                client_name TEXT,
                cargo_description TEXT,
                FOREIGN KEY (truck_id) REFERENCES trucks(id)
            );
            CREATE INDEX IF NOT EXISTS idx_truck_clients_truck ON truck_clients(truck_id);
            CREATE INDEX IF NOT EXISTS idx_truck_clients_tg ON truck_clients(client_telegram_id);
        """)
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create demo company instance")
    parser.add_argument("company_id", help="Company ID (or 'auto' to generate from name)")
    parser.add_argument("rates_json", help="Path to rates.json file")
    parser.add_argument("--source-url", default="", help="Source URL where rates were scraped from")
    args = parser.parse_args()

    with open(args.rates_json, "r", encoding="utf-8") as f:
        rates = json.load(f)

    company_id = args.company_id
    if company_id == "auto":
        company_id = slugify(rates.get("company_name", "unknown"))

    result = create_demo_instance(company_id, rates, args.source_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
