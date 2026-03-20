#!/usr/bin/env python3
"""Tests for truck_manager.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = str(Path(__file__).resolve().parent / "truck_manager.py")


class TruckManagerTest(unittest.TestCase):
    def setUp(self):
        """Create a temp directory and init DB there."""
        self.tmpdir = tempfile.mkdtemp()
        self.company = "test-co"
        # Override DATA_DIR by creating the expected structure
        self.company_dir = Path(self.tmpdir) / "data" / "companies" / self.company
        self.company_dir.mkdir(parents=True)
        # Patch the script's DATA_DIR via environment
        self.env = os.environ.copy()

    def run_cmd(self, *args):
        """Run truck_manager.py with args, return parsed JSON."""
        cmd = [sys.executable, SCRIPT, "--company", self.company] + list(args)
        # We need to patch DATA_DIR. Simplest: use a wrapper that patches before import.
        # Instead, directly use sqlite3 with the same db path logic.
        # Actually, let's just init and test via subprocess with a symlink trick.
        # Better approach: test the functions directly by importing.
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=self.env,
            cwd=str(Path(self.tmpdir))
        )
        return result

    def _init_db(self):
        """Initialize the DB by directly creating it."""
        import sqlite3
        db_path = self.company_dir / "trucks.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trucks (
                id TEXT PRIMARY KEY,
                route TEXT,
                status TEXT DEFAULT 'warehouse',
                status_updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                company_id TEXT DEFAULT 'test-company'
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
        conn.commit()
        conn.close()
        return db_path


class TruckManagerDirectTest(unittest.TestCase):
    """Test truck_manager functions directly by importing and patching DATA_DIR."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.company = "test-co"

        # Import and patch
        import importlib.util
        spec = importlib.util.spec_from_file_location("truck_manager", SCRIPT)
        self.tm = importlib.util.module_from_spec(spec)

        # Patch DATA_DIR before loading
        self.orig_data_dir = None
        spec.loader.exec_module(self.tm)
        self.orig_data_dir = self.tm.DATA_DIR
        self.tm.DATA_DIR = Path(self.tmpdir) / "data" / "companies"
        (self.tm.DATA_DIR / self.company).mkdir(parents=True)

        # Init DB
        self.tm.init_db(self.company)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _capture(self, func, *args, **kwargs):
        """Capture stdout JSON from a function call."""
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            func(*args, **kwargs)
        except SystemExit:
            pass
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        return json.loads(output) if output.strip() else None

    def test_init_db(self):
        db_path = self.tm.get_db_path(self.company)
        self.assertTrue(db_path.exists())

    def test_create_truck(self):
        result = self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self.assertTrue(result["ok"])
        self.assertEqual(result["truck_id"], "025")
        self.assertEqual(result["route"], "Гуанчжоу→Москва")
        self.assertEqual(result["status"], "warehouse")

    def test_create_duplicate_truck(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        result = self._capture(self.tm.create_truck, self.company, "025", "Иу→СПб")
        self.assertFalse(result["ok"])
        self.assertIn("уже существует", result["error"])

    def test_update_status(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        result = self._capture(self.tm.update_status, self.company, "025", "border")
        self.assertTrue(result["ok"])
        self.assertEqual(result["old_status"], "warehouse")
        self.assertEqual(result["new_status"], "border")
        self.assertEqual(result["notify_count"], 0)

    def test_update_status_nonexistent(self):
        result = self._capture(self.tm.update_status, self.company, "999", "border")
        self.assertFalse(result["ok"])
        self.assertIn("не найдена", result["error"])

    def test_add_client(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        result = self._capture(
            self.tm.add_client, self.company, "025", "111222333", "Иван", "кроссовки"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["client_name"], "Иван")

    def test_add_client_duplicate(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван")
        result = self._capture(self.tm.add_client, self.company, "025", "111", "Иван")
        self.assertFalse(result["ok"])
        self.assertIn("уже привязан", result["error"])

    def test_status_with_notifications(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван", "кроссовки")
        self._capture(self.tm.add_client, self.company, "025", "222", "Петя", "одежда")

        result = self._capture(self.tm.update_status, self.company, "025", "departed")
        self.assertTrue(result["ok"])
        self.assertEqual(result["notify_count"], 2)
        self.assertEqual(len(result["clients_to_notify"]), 2)

        # Check notification text includes route
        for notif in result["clients_to_notify"]:
            self.assertIn("Гуанчжоу→Москва", notif["message"])
            self.assertIn("telegram_id", notif)

    def test_list_trucks(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.create_truck, self.company, "026", "Иу→СПб")
        result = self._capture(self.tm.list_trucks, self.company)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)

    def test_list_clients(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван", "кроссовки")
        result = self._capture(self.tm.list_clients, self.company, "025")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["clients"][0]["name"], "Иван")

    def test_lookup_client_found(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван")
        self._capture(self.tm.update_status, self.company, "025", "border")

        result = self._capture(self.tm.lookup_client, self.company, "111")
        self.assertTrue(result["ok"])
        self.assertTrue(result["found"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["trucks"][0]["status"], "border")

    def test_lookup_client_not_found(self):
        result = self._capture(self.tm.lookup_client, self.company, "999999")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertIn("менеджеру", result["message"])

    def test_remove_client(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван")
        result = self._capture(self.tm.remove_client, self.company, "025", "111")
        self.assertTrue(result["ok"])

        # Verify removed
        result = self._capture(self.tm.list_clients, self.company, "025")
        self.assertEqual(result["count"], 0)

    def test_delete_truck(self):
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван")
        result = self._capture(self.tm.delete_truck, self.company, "025")
        self.assertTrue(result["ok"])

        # Verify deleted
        result = self._capture(self.tm.list_trucks, self.company)
        self.assertEqual(result["count"], 0)

    def test_full_lifecycle(self):
        """Full truck lifecycle: create → add clients → statuses → delivered."""
        # Create
        self._capture(self.tm.create_truck, self.company, "025", "Гуанчжоу→Москва")

        # Add clients
        self._capture(self.tm.add_client, self.company, "025", "111", "Иван", "кроссовки, 800 шт")
        self._capture(self.tm.add_client, self.company, "025", "222", "Петя", "одежда, 500 кг")
        self._capture(self.tm.add_client, self.company, "025", "333", "Мария", "косметика")

        # Status updates through lifecycle
        statuses = ["packed", "departed", "border", "customs", "moscow", "delivered"]
        for status in statuses:
            result = self._capture(self.tm.update_status, self.company, "025", status)
            self.assertTrue(result["ok"])
            self.assertEqual(result["new_status"], status)
            self.assertEqual(result["notify_count"], 3)

        # Client lookup shows delivered
        result = self._capture(self.tm.lookup_client, self.company, "111")
        self.assertTrue(result["found"])
        self.assertEqual(result["trucks"][0]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
