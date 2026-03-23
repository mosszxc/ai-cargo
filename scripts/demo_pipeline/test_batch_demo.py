#!/usr/bin/env python3
"""Tests for batch demo pipeline and target company processing."""

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.demo_pipeline.batch_demo import (
    load_targets,
    process_target,
    run_batch,
    TARGET_FILE,
)
from scripts.demo_pipeline.rates_generator import generate_rates_json
from scripts.demo_pipeline.demo_creator import DATA_DIR


# --- Target config tests ---


def test_target_file_exists():
    assert TARGET_FILE.exists(), f"target_companies.json not found at {TARGET_FILE}"
    print("PASS: test_target_file_exists")


def test_target_file_valid_json():
    targets = load_targets()
    assert isinstance(targets, list)
    assert len(targets) == 5
    print("PASS: test_target_file_valid_json")


def test_target_companies_have_required_fields():
    targets = load_targets()
    for t in targets:
        assert "company_id" in t, f"Missing company_id in {t}"
        assert "company_name" in t, f"Missing company_name in {t}"
        assert "source_url" in t, f"Missing source_url in {t}"
        assert "fallback_rates" in t, f"Missing fallback_rates in {t}"
        assert t["company_id"].startswith("demo-"), f"company_id should start with 'demo-': {t['company_id']}"

        fb = t["fallback_rates"]
        assert "company_name" in fb
        assert "routes" in fb
        assert len(fb["routes"]) > 0, f"No routes in fallback for {t['company_id']}"
    print("PASS: test_target_companies_have_required_fields")


def test_target_company_ids_unique():
    targets = load_targets()
    ids = [t["company_id"] for t in targets]
    assert len(ids) == len(set(ids)), f"Duplicate company IDs: {ids}"
    print("PASS: test_target_company_ids_unique")


def test_load_targets_with_filter():
    targets = load_targets("demo-1kargo")
    assert len(targets) == 1
    assert targets[0]["company_name"] == "1Карго"
    print("PASS: test_load_targets_with_filter")


def test_load_targets_nonexistent_filter():
    targets = load_targets("nonexistent-company")
    assert len(targets) == 0
    print("PASS: test_load_targets_nonexistent_filter")


# --- Rate generation from fallback ---


def test_fallback_rates_generate_valid_rates_json():
    """Each company's fallback rates should produce valid rates.json."""
    targets = load_targets()
    for t in targets:
        rates = generate_rates_json(t["fallback_rates"])
        assert rates["company_name"] == t["fallback_rates"]["company_name"]
        assert len(rates["routes"]) > 0

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

        assert "currency" in rates
        assert "services" in rates
        assert "category_surcharges" in rates
    print("PASS: test_fallback_rates_generate_valid_rates_json")


# --- Batch processing tests ---


def test_process_target_dry_run():
    targets = load_targets()
    result = process_target(targets[0], scrape=False, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["source"] == "fallback"
    assert len(result["routes"]) > 0
    print("PASS: test_process_target_dry_run")


def test_batch_dry_run():
    result = run_batch(scrape=False, dry_run=True)
    assert result["ok"] is True
    assert result["total"] == 5
    assert result["success"] == 5
    assert result["failed"] == 0
    assert len(result["results"]) == 5
    for r in result["results"]:
        assert r["dry_run"] is True
    print("PASS: test_batch_dry_run")


def test_batch_single_company_dry_run():
    result = run_batch(scrape=False, company_id="demo-raketa-cn", dry_run=True)
    assert result["ok"] is True
    assert result["total"] == 1
    assert result["results"][0]["company_name"] == "Raketa CN"
    print("PASS: test_batch_single_company_dry_run")


def test_process_target_creates_files():
    """Test actual file creation for a single target."""
    targets = load_targets("demo-cargo-sssr-80")
    target = targets[0]
    test_id = "test-batch-tmp-sssr80"
    target_copy = {**target, "company_id": test_id}
    company_dir = DATA_DIR / test_id

    try:
        result = process_target(target_copy, scrape=False, dry_run=False)
        assert result["ok"] is True
        assert result["company_id"] == test_id
        assert result["source"] == "fallback"

        # Verify files
        assert Path(result["rates_path"]).exists()
        assert (company_dir / "config.json").exists()
        assert (company_dir / "trucks.db").exists()

        # Verify rates.json content
        with open(result["rates_path"], "r", encoding="utf-8") as f:
            rates = json.load(f)
        assert rates["company_name"] == "Карго СССР 80"
        assert "Гуанчжоу→Москва" in rates["routes"]

        # Verify config
        with open(company_dir / "config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["is_demo"] is True

        # Verify demo_info
        assert "demo_info" in result
        assert "calc_command" in result["demo_info"]
        assert "message_template" in result["demo_info"]

        print("PASS: test_process_target_creates_files")
    finally:
        if company_dir.exists():
            shutil.rmtree(company_dir)


def test_all_companies_expected():
    """Verify we have the exact 5 target companies from AICA-64."""
    targets = load_targets()
    names = {t["company_name"] for t in targets}
    expected = {"Карго СССР 80", "1Карго", "РусКит-Транзит", "Raketa CN", "PinGo Cargo"}
    assert names == expected, f"Expected {expected}, got {names}"
    print("PASS: test_all_companies_expected")


def test_all_companies_have_guangzhou_moscow_route():
    """All companies should have at least Guangzhou→Moscow route."""
    targets = load_targets()
    for t in targets:
        rates = generate_rates_json(t["fallback_rates"])
        assert "Гуанчжоу→Москва" in rates["routes"], (
            f"{t['company_name']} missing Гуанчжоу→Москва route"
        )
    print("PASS: test_all_companies_have_guangzhou_moscow_route")


def test_rate_values_realistic():
    """Sanity check that rate values are in realistic range for China→Russia cargo."""
    targets = load_targets()
    for t in targets:
        rates = generate_rates_json(t["fallback_rates"])
        for route_key, route_data in rates["routes"].items():
            for transport, t_data in route_data.items():
                if transport == "air":
                    rate = t_data["rate_per_kg"]
                    assert 4.0 <= rate <= 12.0, (
                        f"{t['company_name']} air rate {rate} out of range"
                    )
                else:
                    for dr in t_data["density_rates"]:
                        if "rate_per_kg" in dr:
                            assert 1.0 <= dr["rate_per_kg"] <= 5.0, (
                                f"{t['company_name']} kg rate {dr['rate_per_kg']} out of range"
                            )
                        if "rate_per_m3" in dr:
                            assert 200 <= dr["rate_per_m3"] <= 500, (
                                f"{t['company_name']} m3 rate {dr['rate_per_m3']} out of range"
                            )
    print("PASS: test_rate_values_realistic")


if __name__ == "__main__":
    test_target_file_exists()
    test_target_file_valid_json()
    test_target_companies_have_required_fields()
    test_target_company_ids_unique()
    test_load_targets_with_filter()
    test_load_targets_nonexistent_filter()
    test_fallback_rates_generate_valid_rates_json()
    test_process_target_dry_run()
    test_batch_dry_run()
    test_batch_single_company_dry_run()
    test_process_target_creates_files()
    test_all_companies_expected()
    test_all_companies_have_guangzhou_moscow_route()
    test_rate_values_realistic()
    print(f"\n✓ All 14 tests passed!")
