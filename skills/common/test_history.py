#!/usr/bin/env python3
"""Tests for calculation history storage."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from skills.common.history import CalculationHistory


def make_history(tmp_dir):
    """Create a CalculationHistory with a temp DB."""
    db_path = Path(tmp_dir) / "test_history.db"
    return CalculationHistory(db_path=db_path)


def sample_params():
    return {
        "product": "кроссовки",
        "weight_kg": 240,
        "volume_m3": 1.2,
        "pieces": 800,
        "origin": "Гуанчжоу",
        "destination": "Москва",
        "special": [],
    }


def sample_result():
    return {
        "success": True,
        "summary": "**кроссовки**\n800 шт | 240 кг",
        "results": [
            {"transport": "auto", "rate": 2.80, "rate_unit": "kg",
             "cost_usd": 672.0, "surcharges": {}, "total_usd": 672.0, "days": "18–25"},
            {"transport": "air", "rate": 6.50, "rate_unit": "kg",
             "cost_usd": 1560.0, "surcharges": {}, "total_usd": 1560.0, "days": "5–7"},
        ],
        "params": sample_params(),
    }


def test_save_and_retrieve():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_history(tmp)

        calc_id = h.save("user123", "test-company", sample_params(), sample_result())
        assert calc_id > 0

        records = h.get_recent("user123", "test-company")
        assert len(records) == 1
        assert records[0]["product"] == "кроссовки"
        assert records[0]["total_usd"] == 672.0
        assert records[0]["cheapest_transport"] == "auto"

    print("PASS: test_save_and_retrieve")


def test_get_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_history(tmp)
        calc_id = h.save("user123", "test-company", sample_params(), sample_result())

        rec = h.get_by_id(calc_id, "user123")
        assert rec is not None
        assert rec["params"]["weight_kg"] == 240
        assert rec["result"]["success"] is True

        # Wrong user — should return None
        assert h.get_by_id(calc_id, "other-user") is None

    print("PASS: test_get_by_id")


def test_multiple_records_ordering():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_history(tmp)

        params1 = sample_params()
        params1["product"] = "первый"
        h.save("user1", "co", params1, sample_result())

        params2 = sample_params()
        params2["product"] = "второй"
        h.save("user1", "co", params2, sample_result())

        params3 = sample_params()
        params3["product"] = "третий"
        h.save("user1", "co", params3, sample_result())

        records = h.get_recent("user1", "co", limit=2)
        assert len(records) == 2
        # Most recent first
        assert records[0]["product"] == "третий"
        assert records[1]["product"] == "второй"

    print("PASS: test_multiple_records_ordering")


def test_user_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_history(tmp)

        h.save("alice", "co", sample_params(), sample_result())
        h.save("bob", "co", sample_params(), sample_result())

        assert len(h.get_recent("alice", "co")) == 1
        assert len(h.get_recent("bob", "co")) == 1
        assert len(h.get_recent("charlie", "co")) == 0

    print("PASS: test_user_isolation")


def test_format_history_list():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_history(tmp)

        # Empty
        assert "пока нет" in h.format_history_list([])

        h.save("user1", "co", sample_params(), sample_result())
        records = h.get_recent("user1", "co")
        text = h.format_history_list(records)

        assert "кроссовки" in text
        assert "240" in text
        assert "Гуанчжоу→Москва" in text
        assert "/recalc_" in text

    print("PASS: test_format_history_list")


if __name__ == "__main__":
    test_save_and_retrieve()
    test_get_by_id()
    test_multiple_records_ordering()
    test_user_isolation()
    test_format_history_list()
    print("\n=== ALL HISTORY TESTS PASSED ===")
