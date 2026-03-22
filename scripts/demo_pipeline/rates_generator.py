#!/usr/bin/env python3
"""
rates_generator.py — Convert scraped rate data into valid rates.json format.

Takes the raw output from rate_scraper.py and produces a rates.json
compatible with skills/calc/calculator.py.

Usage:
    python -m scripts.demo_pipeline.rates_generator <raw_rates.json> [--output rates.json]
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Default values (same as onboarding.py)
DEFAULTS = {
    "usd_cny": 7.25,
    "usd_rub": 88.5,
    "crating_pct": 40,
    "palletizing_pct": 16,
    "insurance_pct": 3,
    "min_weight_kg": 30,
    "inspection_cny_per_hour": 150,
    "repackaging_usd_per_unit": 3.5,
}

# Default delivery days by transport type
DEFAULT_DAYS = {
    "auto": (18, 25),
    "rail": (20, 30),
    "air": (5, 10),
}


def normalize_transport(t: str) -> str:
    """Normalize transport type to auto/rail/air."""
    aliases = {
        "авто": "auto", "фура": "auto", "машина": "auto", "auto": "auto",
        "жд": "rail", "поезд": "rail", "rail": "rail",
        "авиа": "air", "самолёт": "air", "самолет": "air", "air": "air",
    }
    return aliases.get(t.lower().strip(), t.lower().strip())


def build_transport_entry(transport_data: dict) -> dict:
    """Build a single transport entry for rates.json."""
    t_type = normalize_transport(transport_data["type"])
    days_min = transport_data.get("days_min") or DEFAULT_DAYS.get(t_type, (15, 25))[0]
    days_max = transport_data.get("days_max") or DEFAULT_DAYS.get(t_type, (15, 25))[1]

    brackets = transport_data.get("density_brackets", [])

    if t_type == "air":
        # Air uses flat rate per kg, no density brackets
        rate = transport_data.get("rate", 6.50)
        return {
            "rate_per_kg": rate,
            "days_min": days_min,
            "days_max": days_max,
        }

    if brackets:
        # Has density-based brackets
        density_rates = []
        for b in brackets:
            entry = {
                "min_density": b.get("min_density", 0),
                "max_density": b.get("max_density", 9999),
            }
            if b.get("rate_unit") == "m3":
                entry["rate_per_m3"] = b["rate"]
            else:
                entry["rate_per_kg"] = b["rate"]
            density_rates.append(entry)
        return {
            "density_rates": density_rates,
            "days_min": days_min,
            "days_max": days_max,
        }

    # Simple flat rate — create single bracket covering all densities
    rate = transport_data.get("rate", 3.0)
    rate_unit = transport_data.get("rate_unit", "kg")
    rate_key = "rate_per_m3" if rate_unit == "m3" else "rate_per_kg"
    return {
        "density_rates": [
            {"min_density": 0, "max_density": 9999, rate_key: rate},
        ],
        "days_min": days_min,
        "days_max": days_max,
    }


def generate_rates_json(raw_data: dict) -> dict:
    """Convert raw scraped data to valid rates.json."""
    routes = {}

    for route in raw_data.get("routes", []):
        origin = route.get("origin", "Гуанчжоу")
        destination = route.get("destination", "Москва")
        route_key = f"{origin}→{destination}"

        route_entry = {}
        for transport in route.get("transports", []):
            t_type = normalize_transport(transport["type"])
            route_entry[t_type] = build_transport_entry(transport)

        routes[route_key] = route_entry

    # If no routes were extracted, create a default placeholder
    if not routes:
        routes["Гуанчжоу→Москва"] = {
            "auto": {
                "density_rates": [
                    {"min_density": 0, "max_density": 9999, "rate_per_kg": 3.0},
                ],
                "days_min": 18,
                "days_max": 25,
            }
        }

    services_raw = raw_data.get("services", {})
    min_weight = raw_data.get("min_weight_kg") or DEFAULTS["min_weight_kg"]

    return {
        "company_name": raw_data.get("company_name", "Demo Company"),
        "currency": {
            "usd_cny": DEFAULTS["usd_cny"],
            "usd_rub": DEFAULTS["usd_rub"],
            "display": "usd",
        },
        "min_weight_kg": min_weight,
        "routes": routes,
        "category_surcharges": {
            "electronics": 1.5,
            "cosmetics": 1.0,
            "fragile": 1.2,
        },
        "services": {
            "crating_pct": services_raw.get("crating_pct") or DEFAULTS["crating_pct"],
            "palletizing_pct": DEFAULTS["palletizing_pct"],
            "insurance_pct": services_raw.get("insurance_pct") or DEFAULTS["insurance_pct"],
            "inspection_cny_per_hour": DEFAULTS["inspection_cny_per_hour"],
            "repackaging_usd_per_unit": DEFAULTS["repackaging_usd_per_unit"],
        },
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate rates.json from raw scraped data")
    parser.add_argument("input", help="Path to raw scraped rates JSON")
    parser.add_argument("--output", "-o", help="Output rates.json path")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    rates = generate_rates_json(raw_data)
    output = json.dumps(rates, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved rates.json to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
