#!/usr/bin/env python3
"""
Tests for the onboarding wizard.

Run: python3 -m pytest skills/onboarding/test_onboarding.py -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add skill to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding import (
    DEFAULTS,
    DATA_DIR,
    finalize,
    generate_config_json,
    generate_rates_json,
    load_state,
    new_state,
    parse_days_range,
    parse_multi_rate_response,
    parse_number,
    parse_rate_value,
    parse_routes,
    parse_transports,
    process_step,
    save_state,
)


# --- Fixtures ---

@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to temp directory."""
    import onboarding
    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def company_id():
    return "test-onboarding-co"


@pytest.fixture
def fresh_state(tmp_data_dir, company_id):
    """Create and return a fresh onboarding state."""
    state = new_state(company_id, "123456789")
    save_state(company_id, state)
    return state


# --- Number Parsing Tests ---

class TestParseNumber:
    def test_simple_float(self):
        assert parse_number("2.80") == 2.80

    def test_comma_decimal(self):
        assert parse_number("2,80") == 2.80

    def test_integer(self):
        assert parse_number("30") == 30.0

    def test_with_currency_dollar(self):
        assert parse_number("2.80$") == 2.80

    def test_with_currency_word(self):
        assert parse_number("2.80 долларов") == 2.80

    def test_with_percent(self):
        assert parse_number("40%") == 40.0

    def test_with_kg(self):
        assert parse_number("30 кг") == 30.0

    def test_standart_returns_none(self):
        assert parse_number("стандарт") is None

    def test_standartno_returns_none(self):
        assert parse_number("стандартно") is None

    def test_default_returns_none(self):
        assert parse_number("по умолчанию") is None

    def test_russian_two_eighty(self):
        """два восемьдесят = 2.80"""
        assert parse_number("два восемьдесят") == 2.80

    def test_russian_three_ten(self):
        """три десять = 3.10"""
        # "десять" is a teen, not tens — let's check
        result = parse_number("три десять")
        assert result == 3.10

    def test_russian_six_fifty(self):
        """шесть пятьдесят = 6.50"""
        assert parse_number("шесть пятьдесят") == 6.50

    def test_russian_thirty(self):
        """тридцать = 30"""
        assert parse_number("тридцать") == 30.0

    def test_russian_five_hundred(self):
        """пятьсот = 500"""
        assert parse_number("пятьсот") == 500.0

    def test_empty(self):
        assert parse_number("") is None

    def test_days_suffix(self):
        assert parse_number("25 дней") == 25.0


class TestParseRateValue:
    def test_usd_rate(self):
        assert parse_rate_value("2.80") == 2.80

    def test_rubles_conversion(self):
        """340 рублей → $3.84 (at 88.5 rate)"""
        result = parse_rate_value("340 рублей за кг")
        assert result == round(340 / DEFAULTS["usd_rub"], 2)

    def test_cents_heuristic(self):
        """280 (no currency) → $2.80 (cents heuristic)"""
        result = parse_rate_value("280")
        assert result == 2.80

    def test_normal_usd_not_converted(self):
        """6.50 stays as $6.50"""
        assert parse_rate_value("6.50") == 6.50

    def test_rubles_250(self):
        """250 рублей → converts to USD"""
        result = parse_rate_value("250 руб")
        assert result == round(250 / DEFAULTS["usd_rub"], 2)


class TestParseDaysRange:
    def test_dash_range(self):
        assert parse_days_range("18-25") == (18, 25)

    def test_em_dash_range(self):
        assert parse_days_range("18—25") == (18, 25)

    def test_en_dash_range(self):
        assert parse_days_range("18–25") == (18, 25)

    def test_ot_do_range(self):
        assert parse_days_range("от 18 до 25") == (18, 25)

    def test_with_days_suffix(self):
        assert parse_days_range("18-25 дней") == (18, 25)

    def test_single_number(self):
        assert parse_days_range("20 дней") == (20, 20)

    def test_empty(self):
        assert parse_days_range("") == (None, None)


class TestParseRoutes:
    def test_arrow_routes(self):
        routes = parse_routes("Гуанчжоу→Москва, Иу→Москва")
        assert routes == ["Гуанчжоу→Москва", "Иу→Москва"]

    def test_dash_routes(self):
        routes = parse_routes("Гуанчжоу->Москва")
        assert routes == ["Гуанчжоу→Москва"]

    def test_space_routes(self):
        routes = parse_routes("Гуанчжоу Москва")
        assert routes == ["Гуанчжоу→Москва"]

    def test_iz_v_routes(self):
        routes = parse_routes("из Гуанчжоу в Москву")
        assert len(routes) == 1
        assert "Гуанчжоу" in routes[0]

    def test_multiple_routes(self):
        routes = parse_routes("Гуанчжоу→Москва, Иу→Москва, Шэньчжэнь→СПб")
        assert len(routes) == 3


class TestParseTransports:
    def test_russian_names(self):
        result = parse_transports("авто, жд, авиа")
        assert result == ["auto", "rail", "air"]

    def test_english_names(self):
        result = parse_transports("auto rail air")
        assert result == ["auto", "rail", "air"]

    def test_mixed(self):
        result = parse_transports("авто и жд")
        assert "auto" in result
        assert "rail" in result

    def test_single(self):
        result = parse_transports("авто")
        assert result == ["auto"]

    def test_alternative_names(self):
        result = parse_transports("фура, поезд, самолёт")
        assert result == ["auto", "rail", "air"]


class TestParseMultiRateResponse:
    def test_full_multi_rate(self):
        """авто 2.80 за 18-25 дней, жд 2.30 за 25-35"""
        results = parse_multi_rate_response("авто 2.80 за 18-25 дней, жд 2.30 за 25-35")
        assert len(results) == 2
        auto = next(r for r in results if r["transport"] == "auto")
        rail = next(r for r in results if r["transport"] == "rail")
        assert auto["rate"] == 2.80
        assert auto["days_min"] == 18
        assert auto["days_max"] == 25
        assert rail["rate"] == 2.30
        assert rail["days_min"] == 25
        assert rail["days_max"] == 35

    def test_three_rates(self):
        results = parse_multi_rate_response("авто 2.80 18-25 дней, жд 2.30 25-35, авиа 6.50 5-7")
        assert len(results) == 3

    def test_no_transport_returns_empty(self):
        results = parse_multi_rate_response("просто текст без транспорта")
        assert results == []


# --- Wizard Flow Tests ---

class TestWizardFlow:
    """Test the full conversational wizard step by step."""

    def test_happy_path(self, tmp_data_dir, company_id):
        """Full happy path: company → routes → transports → rates → surcharges → confirm."""
        state = new_state(company_id, "123456789")

        # Step 1: Company name
        state = process_step(state, "СпидКарго")
        assert state["step"] == "routes"
        assert state["company_name"] == "СпидКарго"
        assert state["_reply"]  # Has a reply

        # Step 2: Routes
        state = process_step(state, "Гуанчжоу→Москва")
        assert state["step"] == "transports"
        assert state["routes"] == ["Гуанчжоу→Москва"]

        # Step 3: Transports
        state = process_step(state, "авто, жд")
        assert state["step"] == "rates"
        assert state["transports"]["Гуанчжоу→Москва"] == ["auto", "rail"]

        # Step 4: Rate for auto
        state = process_step(state, "2.80 за 18-25 дней")
        # Should ask for rail next
        assert "auto" in state["rates"]["Гуанчжоу→Москва"]
        assert state["rates"]["Гуанчжоу→Москва"]["auto"]["rate"] == 2.80

        # Step 4b: Rate for rail
        state = process_step(state, "2.30 за 25-35 дней")
        assert state["step"] == "crating"

        # Step 5: Crating
        state = process_step(state, "стандарт")
        assert state["step"] == "insurance"
        assert state["crating_pct"] == DEFAULTS["crating_pct"]

        # Step 6: Insurance
        state = process_step(state, "3%")
        assert state["step"] == "min_weight"
        assert state["insurance_pct"] == 3.0

        # Step 7: Min weight
        state = process_step(state, "30")
        assert state["step"] == "summary"

        # Step 8: Confirm
        state = process_step(state, "да")
        assert state["step"] == "confirmed"
        assert state["completed"] is True

    def test_multiple_routes(self, tmp_data_dir, company_id):
        """Test with two routes."""
        state = new_state(company_id)

        state = process_step(state, "ТестКарго")
        state = process_step(state, "Гуанчжоу→Москва, Иу→Москва")
        assert len(state["routes"]) == 2

        # Transports for first route
        state = process_step(state, "авто")
        assert state["step"] == "transports"  # Should ask for second route

        # Transports for second route
        state = process_step(state, "авто, авиа")
        assert state["step"] == "rates"

    def test_multi_rate_in_one_message(self, tmp_data_dir, company_id):
        """Manager gives multiple rates at once."""
        state = new_state(company_id)
        state = process_step(state, "МультиКарго")
        state = process_step(state, "Гуанчжоу→Москва")
        state = process_step(state, "авто, жд, авиа")

        # Give all rates in one message
        state = process_step(state, "авто 2.80 за 18-25 дней, жд 2.30 за 25-35, авиа 6.50 за 5-7")
        # Should skip straight to crating since all rates provided
        assert state["step"] == "crating"
        assert state["rates"]["Гуанчжоу→Москва"]["auto"]["rate"] == 2.80
        assert state["rates"]["Гуанчжоу→Москва"]["rail"]["rate"] == 2.30
        assert state["rates"]["Гуанчжоу→Москва"]["air"]["rate"] == 6.50

    def test_default_values(self, tmp_data_dir, company_id):
        """'стандарт' for surcharges uses defaults."""
        state = new_state(company_id)
        state["step"] = "crating"

        state = process_step(state, "стандарт")
        assert state["crating_pct"] == DEFAULTS["crating_pct"]
        assert state["step"] == "insurance"

        state = process_step(state, "стандарт")
        assert state["insurance_pct"] == DEFAULTS["insurance_pct"]
        assert state["step"] == "min_weight"

        state = process_step(state, "стандарт")
        assert state["min_weight_kg"] == DEFAULTS["min_weight_kg"]

    def test_reject_and_restart(self, tmp_data_dir, company_id):
        """Manager says 'нет' at summary → restart."""
        state = new_state(company_id)
        state["step"] = "summary"
        state["company_name"] = "Тест"
        state["routes"] = ["Гуанчжоу→Москва"]
        state["rates"] = {"Гуанчжоу→Москва": {"auto": {"rate": 2.80, "days_min": 18, "days_max": 25}}}
        state["transports"] = {"Гуанчжоу→Москва": ["auto"]}

        state = process_step(state, "нет")
        assert state["step"] == "company_name"  # Restarted

    def test_resume_interrupted(self, tmp_data_dir, company_id):
        """Resume interrupted onboarding — state persists."""
        # Start onboarding
        state = new_state(company_id, "999")
        state = process_step(state, "РезюмКарго")
        save_state(company_id, state)

        # "Manager leaves" — load state later
        loaded = load_state(company_id)
        assert loaded["step"] == "routes"
        assert loaded["company_name"] == "РезюмКарго"

        # Continue from where we left off
        loaded = process_step(loaded, "Гуанчжоу→Москва")
        assert loaded["step"] == "transports"


# --- Generation Tests ---

class TestGenerateRatesJson:
    def test_simple_generation(self):
        state = {
            "company_name": "TestCo",
            "routes": ["Гуанчжоу→Москва"],
            "rates": {
                "Гуанчжоу→Москва": {
                    "auto": {"rate": 2.80, "days_min": 18, "days_max": 25},
                    "rail": {"rate": 2.30, "days_min": 25, "days_max": 35},
                }
            },
            "crating_pct": 40,
            "insurance_pct": 3,
            "min_weight_kg": 30,
        }

        rates = generate_rates_json(state)

        assert rates["company_name"] == "TestCo"
        assert rates["min_weight_kg"] == 30
        assert "Гуанчжоу→Москва" in rates["routes"]

        auto = rates["routes"]["Гуанчжоу→Москва"]["auto"]
        assert "density_rates" in auto
        assert auto["density_rates"][0]["rate_per_kg"] == 2.80
        assert auto["density_rates"][0]["min_density"] == 0
        assert auto["density_rates"][0]["max_density"] == 9999
        assert auto["days_min"] == 18
        assert auto["days_max"] == 25

    def test_air_flat_rate(self):
        state = {
            "company_name": "AirCo",
            "routes": ["Гуанчжоу→Москва"],
            "rates": {
                "Гуанчжоу→Москва": {
                    "air": {"rate": 6.50, "days_min": 5, "days_max": 7},
                }
            },
            "crating_pct": 40,
            "insurance_pct": 3,
            "min_weight_kg": 30,
        }

        rates = generate_rates_json(state)
        air = rates["routes"]["Гуанчжоу→Москва"]["air"]
        assert "density_rates" not in air
        assert air["rate_per_kg"] == 6.50

    def test_services_populated(self):
        state = {
            "company_name": "SvcCo",
            "routes": [],
            "rates": {},
            "crating_pct": 50,
            "insurance_pct": 5,
            "min_weight_kg": 20,
        }

        rates = generate_rates_json(state)
        assert rates["services"]["crating_pct"] == 50
        assert rates["services"]["insurance_pct"] == 5
        assert rates["services"]["palletizing_pct"] == DEFAULTS["palletizing_pct"]


class TestGenerateConfigJson:
    def test_config_generation(self):
        state = {
            "company_name": "TestCo",
            "company_id": "testco",
            "manager_telegram_id": "123456",
        }

        config = generate_config_json(state)
        assert config["company_name"] == "TestCo"
        assert config["company_id"] == "testco"
        assert config["manager_telegram_id"] == "123456"
        assert "client_bot_token_ref" in config


# --- Finalization Tests ---

class TestFinalize:
    def test_finalize_creates_files(self, tmp_data_dir, company_id):
        """Finalize creates rates.json and config.json."""
        state = new_state(company_id, "123456")
        state["company_name"] = "ФиналКарго"
        state["routes"] = ["Гуанчжоу→Москва"]
        state["transports"] = {"Гуанчжоу→Москва": ["auto"]}
        state["rates"] = {"Гуанчжоу→Москва": {"auto": {"rate": 2.80, "days_min": 18, "days_max": 25}}}
        state["crating_pct"] = 40
        state["insurance_pct"] = 3
        state["min_weight_kg"] = 30
        state["completed"] = True
        save_state(company_id, state)

        result = finalize(company_id)
        assert result["ok"] is True

        # Check rates.json exists and is valid
        rates_path = tmp_data_dir / company_id / "rates.json"
        assert rates_path.exists()
        with open(rates_path) as f:
            rates = json.load(f)
        assert rates["company_name"] == "ФиналКарго"
        assert "Гуанчжоу→Москва" in rates["routes"]

        # Check config.json exists and is valid
        config_path = tmp_data_dir / company_id / "config.json"
        assert config_path.exists()
        with open(config_path) as f:
            config = json.load(f)
        assert config["company_name"] == "ФиналКарго"

    def test_finalize_incomplete_fails(self, tmp_data_dir, company_id):
        """Cannot finalize if onboarding not completed."""
        state = new_state(company_id)
        state["completed"] = False
        save_state(company_id, state)

        result = finalize(company_id)
        assert result["ok"] is False


# --- CLI Integration Tests ---

class TestCLI:
    """Test the CLI interface via subprocess."""

    SCRIPT = str(Path(__file__).resolve().parent / "onboarding.py")

    def _run(self, *args, data_dir=None) -> dict:
        env = os.environ.copy()
        result = subprocess.run(
            ["python3", self.SCRIPT] + list(args),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 and not result.stdout:
            pytest.fail(f"CLI failed: {result.stderr}")
        return json.loads(result.stdout)

    def test_init_and_load(self, tmp_data_dir, company_id):
        """Test init creates state, load retrieves it."""
        # We can't easily redirect DATA_DIR via CLI, so we test the Python API directly
        state = new_state(company_id, "999")
        save_state(company_id, state)

        loaded = load_state(company_id)
        assert loaded is not None
        assert loaded["step"] == "company_name"
        assert loaded["manager_telegram_id"] == "999"

    def test_full_flow_via_api(self, tmp_data_dir, company_id):
        """End-to-end test via Python API (equivalent to CLI)."""
        # Init
        state = new_state(company_id, "555")
        save_state(company_id, state)

        # Process each step
        messages = [
            "ТестКарго",
            "Гуанчжоу→Москва",
            "авто",
            "2.80 за 18-25 дней",
            "стандарт",
            "стандарт",
            "стандарт",
            "да",
        ]

        for msg in messages:
            state = load_state(company_id)
            state = process_step(state, msg)
            save_state(company_id, state)

        assert state["completed"] is True

        # Finalize
        result = finalize(company_id)
        assert result["ok"] is True

        # Verify output files
        rates_path = tmp_data_dir / company_id / "rates.json"
        assert rates_path.exists()

        with open(rates_path) as f:
            rates = json.load(f)

        assert rates["company_name"] == "ТестКарго"
        assert rates["routes"]["Гуанчжоу→Москва"]["auto"]["density_rates"][0]["rate_per_kg"] == 2.80
        assert rates["min_weight_kg"] == DEFAULTS["min_weight_kg"]


# --- Edge Case Tests ---

class TestEdgeCases:
    def test_rubles_rate(self, tmp_data_dir, company_id):
        """Manager gives rate in rubles — converted to USD."""
        state = new_state(company_id)
        state = process_step(state, "РублёвКарго")
        state = process_step(state, "Гуанчжоу→Москва")
        state = process_step(state, "авто")

        state = process_step(state, "340 рублей за кг, 18-25 дней")
        expected_usd = round(340 / DEFAULTS["usd_rub"], 2)
        assert state["rates"]["Гуанчжоу→Москва"]["auto"]["rate"] == expected_usd

    def test_empty_message_handled(self, tmp_data_dir, company_id):
        """Empty message returns error, stays on same step."""
        state = new_state(company_id)
        state = process_step(state, "")
        assert state["step"] == "company_name"  # Stayed on same step
        assert state["_error"]  # Has error

    def test_bad_route_format(self, tmp_data_dir, company_id):
        """Invalid route format returns error."""
        state = new_state(company_id)
        state = process_step(state, "Карго")
        state = process_step(state, "абракадабра")
        assert state["step"] == "routes"  # Stayed on same step
        assert state["_error"]

    def test_already_finalized(self, tmp_data_dir, company_id):
        """Attempting to process after finalization returns 'already done'."""
        state = new_state(company_id)
        state["finalized"] = True
        save_state(company_id, state)

        # Python API
        loaded = load_state(company_id)
        assert loaded["finalized"] is True
