#!/usr/bin/env python3
"""Tests for rate_manager.py"""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "rate_manager.py")


def load_module():
    spec = importlib.util.spec_from_file_location("rate_manager", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RateManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.company = "test-co"
        self.rm = load_module()
        self.rm.DATA_DIR = Path(self.tmpdir) / "data" / "companies"
        (self.rm.DATA_DIR / self.company).mkdir(parents=True)
        # Init rates
        self._capture(self.rm.init_rates, self.company)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture(self, func, *args, **kwargs):
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            func(*args, **kwargs)
        except SystemExit:
            pass
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return json.loads(output) if output.strip() else None

    def test_init_rates(self):
        path = self.rm.get_rates_path(self.company)
        self.assertTrue(path.exists())
        with open(path) as f:
            data = json.load(f)
        self.assertIn("routes", data)
        self.assertIn("Гуанчжоу→Москва", data["routes"])

    def test_init_duplicate(self):
        result = self._capture(self.rm.init_rates, self.company)
        self.assertFalse(result["ok"])
        self.assertIn("уже существует", result["error"])

    def test_show_rates(self):
        result = self._capture(self.rm.show_rates, self.company)
        self.assertTrue(result["ok"])
        self.assertIn("rates", result)
        self.assertEqual(result["rates"]["company_name"], "test-co")

    def test_show_route(self):
        result = self._capture(self.rm.show_route, self.company, "Гуанчжоу→Москва")
        self.assertTrue(result["ok"])
        self.assertIn("auto", result["data"])
        self.assertIn("rail", result["data"])
        self.assertIn("air", result["data"])

    def test_show_route_fuzzy(self):
        result = self._capture(self.rm.show_route, self.company, "гуанчжоу")
        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "Гуанчжоу→Москва")

    def test_show_route_not_found(self):
        result = self._capture(self.rm.show_route, self.company, "НесуществующийМаршрут")
        self.assertFalse(result["ok"])

    def test_update_simple_rate_air(self):
        result = self._capture(self.rm.update_simple_rate, self.company, "Гуанчжоу→Москва", "air", 7.00)
        self.assertTrue(result["ok"])
        self.assertEqual(result["old_rate"], 6.50)
        self.assertEqual(result["new_rate"], 7.00)

        # Verify persisted
        with open(self.rm.get_rates_path(self.company)) as f:
            data = json.load(f)
        self.assertEqual(data["routes"]["Гуанчжоу→Москва"]["air"]["rate_per_kg"], 7.00)

    def test_update_simple_rate_auto_all(self):
        """When updating auto without density, all density rates update."""
        result = self._capture(self.rm.update_simple_rate, self.company, "Гуанчжоу→Москва", "авто", 2.90)
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["old_rate"], list)

        # Verify all density rates updated
        with open(self.rm.get_rates_path(self.company)) as f:
            data = json.load(f)
        for dr in data["routes"]["Гуанчжоу→Москва"]["auto"]["density_rates"]:
            rate = dr.get("rate_per_kg") or dr.get("rate_per_m3")
            self.assertEqual(rate, 2.90)

    def test_update_rate_by_density(self):
        result = self._capture(self.rm.update_rate, self.company, "Гуанчжоу→Москва", "auto", 200, 3.00)
        self.assertTrue(result["ok"])
        self.assertEqual(result["old_rate"], 2.80)
        self.assertEqual(result["new_rate"], 3.00)

        # Verify only the target density changed
        with open(self.rm.get_rates_path(self.company)) as f:
            data = json.load(f)
        for dr in data["routes"]["Гуанчжоу→Москва"]["auto"]["density_rates"]:
            if dr["min_density"] == 200:
                self.assertEqual(dr["rate_per_kg"], 3.00)
            elif dr["min_density"] == 400:
                self.assertEqual(dr["rate_per_kg"], 1.80)  # unchanged

    def test_add_route(self):
        result = self._capture(self.rm.add_route, self.company, "Иу→СПб", "авто", 3.10, 20, 30)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "auto")

        # Verify
        with open(self.rm.get_rates_path(self.company)) as f:
            data = json.load(f)
        self.assertIn("Иу→СПб", data["routes"])
        self.assertEqual(data["routes"]["Иу→СПб"]["auto"]["rate_per_kg"], 3.10)

    def test_add_route_duplicate_transport(self):
        self._capture(self.rm.add_route, self.company, "Иу→СПб", "авто", 3.10, 20, 30)
        result = self._capture(self.rm.add_route, self.company, "Иу→СПб", "авто", 3.50, 20, 30)
        self.assertFalse(result["ok"])
        self.assertIn("уже существует", result["error"])

    def test_add_multiple_transports_to_route(self):
        self._capture(self.rm.add_route, self.company, "Иу→СПб", "авто", 3.10, 20, 30)
        result = self._capture(self.rm.add_route, self.company, "Иу→СПб", "авиа", 7.50, 5, 7)
        self.assertTrue(result["ok"])

        with open(self.rm.get_rates_path(self.company)) as f:
            data = json.load(f)
        self.assertIn("auto", data["routes"]["Иу→СПб"])
        self.assertIn("air", data["routes"]["Иу→СПб"])

    def test_transport_name_mapping(self):
        """Russian transport names map to English keys."""
        # авто → auto
        result = self._capture(self.rm.update_simple_rate, self.company, "Гуанчжоу→Москва", "авто", 2.90)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "auto")

        # жд → rail
        result = self._capture(self.rm.update_simple_rate, self.company, "Гуанчжоу→Москва", "жд", 2.50)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "rail")

        # авиа → air
        result = self._capture(self.rm.update_simple_rate, self.company, "Гуанчжоу→Москва", "авиа", 7.00)
        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "air")


if __name__ == "__main__":
    unittest.main()
