#!/usr/bin/env python3
"""Tests for demo pipeline — rates generator and demo creator (no LLM needed)."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.demo_pipeline.rates_generator import generate_rates_json, normalize_transport
from scripts.demo_pipeline.demo_creator import create_demo_instance, slugify, DATA_DIR


# --- Test data: simulated LLM output ---

SAMPLE_RAW_RATES = {
    "company_name": "FastCargo Express",
    "routes": [
        {
            "origin": "Гуанчжоу",
            "destination": "Москва",
            "transports": [
                {
                    "type": "auto",
                    "rate": 2.80,
                    "rate_unit": "kg",
                    "days_min": 18,
                    "days_max": 25,
                    "density_brackets": [
                        {"min_density": 0, "max_density": 99, "rate": 350, "rate_unit": "m3"},
                        {"min_density": 100, "max_density": 199, "rate": 3.20, "rate_unit": "kg"},
                        {"min_density": 200, "max_density": 9999, "rate": 2.80, "rate_unit": "kg"},
                    ]
                },
                {
                    "type": "rail",
                    "rate": 2.30,
                    "rate_unit": "kg",
                    "days_min": 22,
                    "days_max": 30,
                    "density_brackets": []
                },
                {
                    "type": "air",
                    "rate": 6.50,
                    "rate_unit": "kg",
                    "days_min": 5,
                    "days_max": 8,
                    "density_brackets": []
                },
            ]
        },
        {
            "origin": "Иу",
            "destination": "Владивосток",
            "transports": [
                {
                    "type": "auto",
                    "rate": 3.10,
                    "rate_unit": "kg",
                    "days_min": 15,
                    "days_max": 20,
                    "density_brackets": []
                },
            ]
        }
    ],
    "min_weight_kg": 50,
    "currency": "usd",
    "services": {
        "insurance_pct": 5,
        "crating_pct": 35
    },
    "source_url": "https://example-cargo.com/rates"
}

SAMPLE_SIMPLE_RATES = {
    "company_name": "ChinaLogistics",
    "routes": [
        {
            "origin": "Гуанчжоу",
            "destination": "Москва",
            "transports": [
                {"type": "авто", "rate": 3.0, "rate_unit": "kg"},
                {"type": "авиа", "rate": 7.0, "rate_unit": "kg"},
            ]
        }
    ],
    "min_weight_kg": None,
    "currency": "usd",
    "services": {},
}


def test_normalize_transport():
    assert normalize_transport("авто") == "auto"
    assert normalize_transport("ЖД") == "rail"
    assert normalize_transport("авиа") == "air"
    assert normalize_transport("auto") == "auto"
    assert normalize_transport("фура") == "auto"
    print("PASS: test_normalize_transport")


def test_generate_rates_full():
    """Test rates generation with density brackets."""
    rates = generate_rates_json(SAMPLE_RAW_RATES)

    assert rates["company_name"] == "FastCargo Express"
    assert rates["min_weight_kg"] == 50
    assert "Гуанчжоу→Москва" in rates["routes"]
    assert "Иу→Владивосток" in rates["routes"]

    # Check auto transport has density brackets
    auto = rates["routes"]["Гуанчжоу→Москва"]["auto"]
    assert "density_rates" in auto
    assert len(auto["density_rates"]) == 3
    assert auto["density_rates"][0]["rate_per_m3"] == 350
    assert auto["density_rates"][2]["rate_per_kg"] == 2.80
    assert auto["days_min"] == 18

    # Check air transport (flat rate)
    air = rates["routes"]["Гуанчжоу→Москва"]["air"]
    assert air["rate_per_kg"] == 6.50
    assert "density_rates" not in air

    # Check rail (flat rate → single bracket)
    rail = rates["routes"]["Гуанчжоу→Москва"]["rail"]
    assert "density_rates" in rail
    assert len(rail["density_rates"]) == 1
    assert rail["density_rates"][0]["rate_per_kg"] == 2.30

    # Check services
    assert rates["services"]["insurance_pct"] == 5
    assert rates["services"]["crating_pct"] == 35

    print("PASS: test_generate_rates_full")


def test_generate_rates_simple():
    """Test rates generation with simple flat rates + Russian transport names."""
    rates = generate_rates_json(SAMPLE_SIMPLE_RATES)

    assert rates["company_name"] == "ChinaLogistics"
    assert rates["min_weight_kg"] == 30  # default
    assert "Гуанчжоу→Москва" in rates["routes"]

    route = rates["routes"]["Гуанчжоу→Москва"]
    assert "auto" in route
    assert "air" in route

    # Auto should have single density bracket
    auto = route["auto"]
    assert len(auto["density_rates"]) == 1
    assert auto["density_rates"][0]["rate_per_kg"] == 3.0

    # Services should use defaults
    assert rates["services"]["insurance_pct"] == 3  # default
    assert rates["services"]["crating_pct"] == 40  # default

    print("PASS: test_generate_rates_simple")


def test_generate_rates_empty():
    """Test with no routes — should create placeholder."""
    rates = generate_rates_json({"company_name": "Empty Co", "routes": []})
    assert "Гуанчжоу→Москва" in rates["routes"]
    print("PASS: test_generate_rates_empty")


def test_slugify():
    assert slugify("FastCargo Express") == "demo-fastcargo-express"
    assert slugify("КаргоПро") == "demo-kargopro"
    assert slugify("China Logistics 2024") == "demo-china-logistics-2024"
    print("PASS: test_slugify")


def test_create_demo_instance():
    """Test creating a demo instance with a temp directory."""
    rates = generate_rates_json(SAMPLE_RAW_RATES)

    # Use a temp company id to avoid polluting real data
    test_id = "test-demo-pipeline-tmp"
    company_dir = DATA_DIR / test_id

    try:
        result = create_demo_instance(test_id, rates, "https://example.com")

        assert result["ok"] is True
        assert result["company_id"] == test_id
        assert result["company_name"] == "FastCargo Express"

        # Verify files were created
        assert Path(result["rates_path"]).exists()
        assert Path(result["config_path"]).exists()
        assert Path(result["db_path"]).exists()

        # Verify rates.json is valid
        with open(result["rates_path"], "r") as f:
            saved_rates = json.load(f)
        assert saved_rates["company_name"] == "FastCargo Express"
        assert "Гуанчжоу→Москва" in saved_rates["routes"]

        # Verify config.json
        with open(result["config_path"], "r") as f:
            config = json.load(f)
        assert config["is_demo"] is True
        assert config["source_url"] == "https://example.com"

        print("PASS: test_create_demo_instance")
    finally:
        # Cleanup
        if company_dir.exists():
            shutil.rmtree(company_dir)


def test_rates_compatible_with_calculator():
    """Verify generated rates.json is structurally compatible with calculator.py."""
    rates = generate_rates_json(SAMPLE_RAW_RATES)

    # Check all required top-level keys
    assert "company_name" in rates
    assert "currency" in rates
    assert "min_weight_kg" in rates
    assert "routes" in rates
    assert "category_surcharges" in rates
    assert "services" in rates

    # Check currency structure
    assert "usd_cny" in rates["currency"]
    assert "usd_rub" in rates["currency"]
    assert "display" in rates["currency"]

    # Check each route/transport
    for route_key, route_data in rates["routes"].items():
        assert "→" in route_key
        for transport, t_data in route_data.items():
            assert transport in ("auto", "rail", "air")
            assert "days_min" in t_data
            assert "days_max" in t_data
            if transport == "air":
                assert "rate_per_kg" in t_data
            else:
                assert "density_rates" in t_data
                for dr in t_data["density_rates"]:
                    assert "min_density" in dr
                    assert "max_density" in dr
                    assert "rate_per_kg" in dr or "rate_per_m3" in dr

    print("PASS: test_rates_compatible_with_calculator")


if __name__ == "__main__":
    test_normalize_transport()
    test_generate_rates_full()
    test_generate_rates_simple()
    test_generate_rates_empty()
    test_slugify()
    test_create_demo_instance()
    test_rates_compatible_with_calculator()
    print("\n✓ All 7 tests passed!")
