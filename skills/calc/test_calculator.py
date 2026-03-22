#!/usr/bin/env python3
"""Tests for cargo calculator — 5 real-world scenarios."""

import json
import sys
from pathlib import Path

# Add project root and parent to path for import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from calculator import CargoParams, calculate, load_rates, adapt_parser_output

RATES_PATH = str(Path(__file__).parent.parent.parent / "data" / "companies" / "test-company" / "rates.json")


def test_basic_clothing():
    """Test 1: 500 кг одежда Гуанчжоу→Москва — базовый расчёт по весу."""
    rates = load_rates(RATES_PATH)
    params = CargoParams(
        product="одежда",
        weight_kg=500,
        volume_m3=2.5,  # density = 200 kg/m3
        origin="Гуанчжоу",
        destination="Москва",
    )
    result = calculate(rates, params)

    assert result["success"], f"Expected success, got: {result.get('error')}"
    assert result["params"]["density"] == 200.0

    # Auto at density 200: rate_per_kg = 2.80 → 500 * 2.80 = 1400
    auto = next(r for r in result["results"] if r["transport"] == "auto")
    assert auto["rate"] == 2.80
    assert auto["cost_usd"] == 1400.0
    assert auto["rate_unit"] == "kg"

    # Rail at density 200: rate_per_kg = 2.30 → 500 * 2.30 = 1150
    rail = next(r for r in result["results"] if r["transport"] == "rail")
    assert rail["rate"] == 2.30
    assert rail["cost_usd"] == 1150.0

    # Air: 6.50/kg → 500 * 6.50 = 3250
    air = next(r for r in result["results"] if r["transport"] == "air")
    assert air["rate"] == 6.50
    assert air["cost_usd"] == 3250.0

    print("PASS: test_basic_clothing")


def test_sneakers_by_piece():
    """Test 2: кроссовки 800 шт по 0.3 кг, 0.0015 м³/шт — расчёт из штучных данных."""
    rates = load_rates(RATES_PATH)
    params = CargoParams(
        product="кроссовки женские",
        pieces=800,
        weight_per_piece_kg=0.3,
        volume_per_piece_m3=0.0015,  # 30*20*12 cm = 7200cm3 = 0.0072, but boxes pack tighter
        price_per_piece_cny=45,
        origin="Гуанчжоу",
        destination="Москва",
    )
    result = calculate(rates, params)

    assert result["success"]
    # weight = 800 * 0.3 = 240 kg
    assert result["params"]["weight_kg"] == 240.0
    # volume = 800 * 0.0015 = 1.2 m3
    assert result["params"]["volume_m3"] == 1.2
    # density = 240 / 1.2 = 200
    assert result["params"]["density"] == 200.0

    # Auto at density 200: rate_per_kg = 2.80 → 240 * 2.80 = 672
    auto = next(r for r in result["results"] if r["transport"] == "auto")
    assert auto["cost_usd"] == 672.0

    # Check purchase cost is in summary
    assert "¥36,000" in result["summary"] or "¥36 000" in result["summary"]

    print("PASS: test_sneakers_by_piece")


def test_light_cargo_volume_rate():
    """Test 3: лёгкий груз (низкая плотность) — расчёт по объёму."""
    rates = load_rates(RATES_PATH)
    params = CargoParams(
        product="подушки",
        weight_kg=100,
        volume_m3=5.0,  # density = 100/5 = 20 kg/m3 — very light
        origin="Гуанчжоу",
        destination="Москва",
    )
    result = calculate(rates, params)

    assert result["success"]
    assert result["params"]["density"] == 20.0

    # Auto at density 20 (0-99 bracket): rate_per_m3 = 350 → 5.0 * 350 = 1750
    auto = next(r for r in result["results"] if r["transport"] == "auto")
    assert auto["rate_unit"] == "m3"
    assert auto["rate"] == 350
    assert auto["cost_usd"] == 1750.0

    # Rail at density 20 (0-99 bracket): rate_per_m3 = 300 → 5.0 * 300 = 1500
    rail = next(r for r in result["results"] if r["transport"] == "rail")
    assert rail["rate_unit"] == "m3"
    assert rail["cost_usd"] == 1500.0

    print("PASS: test_light_cargo_volume_rate")


def test_fragile_with_crating():
    """Test 4: хрупкий груз — обрешётка +40% + наценка fragile 1.2x."""
    rates = load_rates(RATES_PATH)
    params = CargoParams(
        product="посуда керамическая",
        weight_kg=300,
        volume_m3=1.5,  # density = 200
        origin="Гуанчжоу",
        destination="Москва",
        special=["fragile"],
    )
    result = calculate(rates, params)

    assert result["success"]

    # Auto base: 300 * 2.80 = 840
    auto = next(r for r in result["results"] if r["transport"] == "auto")
    assert auto["cost_usd"] == 840.0

    # Fragile surcharges:
    # - category multiplier 1.2 → surcharge = 840 * 0.2 = 168
    # - crating 40% → surcharge = 840 * 0.4 = 336
    assert "наценка (fragile)" in auto["surcharges"]
    assert auto["surcharges"]["наценка (fragile)"] == 168.0
    assert "обрешётка" in auto["surcharges"]
    assert auto["surcharges"]["обрешётка"] == 336.0

    # Total = 840 + 168 + 336 = 1344
    assert auto["total_usd"] == 1344.0

    print("PASS: test_fragile_with_crating")


def test_route_not_found():
    """Test 5: маршрут не найден + отсутствие веса."""
    rates = load_rates(RATES_PATH)

    # Unknown route
    params = CargoParams(
        product="мебель",
        weight_kg=1000,
        origin="Шэньчжэнь",
        destination="Санкт-Петербург",
    )
    result = calculate(rates, params)
    assert not result["success"]
    assert "нет ставок" in result["error"]

    # No weight
    params2 = CargoParams(
        product="что-то",
        origin="Гуанчжоу",
        destination="Москва",
    )
    result2 = calculate(rates, params2)
    assert not result2["success"]
    assert "вес" in result2["error"].lower()

    # Below minimum weight
    params3 = CargoParams(
        product="образцы",
        weight_kg=10,  # min is 30
        origin="Гуанчжоу",
        destination="Москва",
    )
    result3 = calculate(rates, params3)
    assert result3["success"]  # Still calculates but with warning
    assert "минимальный вес" in result3["summary"].lower()

    print("PASS: test_route_not_found")


def test_yiwu_route():
    """Bonus test: Иу→Москва route (only auto available)."""
    rates = load_rates(RATES_PATH)
    params = CargoParams(
        product="игрушки",
        weight_kg=400,
        volume_m3=2.0,  # density = 200
        origin="Иу",
        destination="Москва",
    )
    result = calculate(rates, params)

    assert result["success"]
    # Only auto available on this route
    assert len(result["results"]) == 1
    assert result["results"][0]["transport"] == "auto"

    # Density 200 → rate_per_kg = 3.00 → 400 * 3.00 = 1200
    assert result["results"][0]["cost_usd"] == 1200.0

    print("PASS: test_yiwu_route")


def test_adapt_parser_output():
    """Test parser→calculator adapter with structured price_cny."""
    # Structured price with variants
    parser_result = {
        "success": True,
        "title": "Кроссовки женские",
        "price_cny": {"min": 45.0, "max": 60.0, "variants": [
            {"name": "36-39", "price": 45.0},
            {"name": "40-44", "price": 60.0},
        ]},
        "weight_kg": 0.3,
        "dimensions": {"l": 30, "w": 20, "h": 12},
        "offer_id": "790251400429",
    }

    params = adapt_parser_output(parser_result, pieces=500)
    assert params["product"] == "Кроссовки женские"
    assert params["price_per_piece_cny"] == 45.0  # uses min
    assert params["weight_per_piece_kg"] == 0.3
    assert params["pieces"] == 500
    assert abs(params["volume_per_piece_m3"] - 0.0072) < 0.0001

    # Flat price (backward compat)
    parser_flat = {
        "success": True,
        "title": "Футболка",
        "price_cny": 25.0,
        "weight_kg": 0.2,
    }
    params2 = adapt_parser_output(parser_flat, pieces=100)
    assert params2["price_per_piece_cny"] == 25.0

    # E2E: parser output → adapt → calculate
    rates = load_rates(RATES_PATH)
    calc_params = CargoParams(**{k: v for k, v in params.items() if k in CargoParams.__dataclass_fields__})
    result = calculate(rates, calc_params)
    assert result["success"], f"E2E failed: {result.get('error')}"
    assert result["params"]["weight_kg"] == 150.0  # 500 * 0.3

    print("PASS: test_adapt_parser_output")


if __name__ == "__main__":
    test_basic_clothing()
    test_sneakers_by_piece()
    test_light_cargo_volume_rate()
    test_fragile_with_crating()
    test_route_not_found()
    test_yiwu_route()
    test_adapt_parser_output()
    print("\n=== ALL TESTS PASSED ===")
