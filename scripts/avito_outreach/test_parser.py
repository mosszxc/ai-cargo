#!/usr/bin/env python3
"""Tests for Avito seller parser."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.avito_outreach.parser import (
    CATEGORIES,
    CITIES,
    AvitoClient,
    AvitoParser,
    ParseStats,
    SellerDB,
    SellerRecord,
    _backoff_delay,
    extract_seller_from_item,
)


class TestSellerDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = SellerDB(db_path=Path(self.tmp.name))

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def _make_record(self, seller_id=1001, **kwargs):
        defaults = dict(
            seller_id=seller_id,
            item_id=5001,
            title="Test item",
            price=1500,
            photos_count=3,
            category="electronics",
            city="Москва",
            seller_reg_date="2023-01-15",
            items_count=42,
        )
        defaults.update(kwargs)
        return SellerRecord(**defaults)

    def test_insert_new_seller(self):
        rec = self._make_record()
        is_new = self.db.upsert_seller(rec)
        self.assertTrue(is_new)
        self.assertEqual(self.db.count(), 1)

    def test_update_existing_seller(self):
        rec = self._make_record()
        self.db.upsert_seller(rec)

        rec2 = self._make_record(price=2000, title="Updated item")
        is_new = self.db.upsert_seller(rec2)
        self.assertFalse(is_new)
        self.assertEqual(self.db.count(), 1)

        # Verify updated fields
        with sqlite3.connect(self.tmp.name) as conn:
            row = conn.execute("SELECT price, title FROM sellers WHERE seller_id = 1001").fetchone()
        self.assertEqual(row[0], 2000)
        self.assertEqual(row[1], "Updated item")

    def test_dedup_by_seller_id(self):
        for i in range(3):
            rec = self._make_record(item_id=5000 + i, title=f"Item {i}")
            self.db.upsert_seller(rec)
        self.assertEqual(self.db.count(), 1)

    def test_multiple_sellers(self):
        for sid in [1001, 1002, 1003]:
            self.db.upsert_seller(self._make_record(seller_id=sid))
        self.assertEqual(self.db.count(), 3)

    def test_stats(self):
        self.db.upsert_seller(self._make_record(seller_id=1, category="electronics", city="Москва"))
        self.db.upsert_seller(self._make_record(seller_id=2, category="clothing", city="Москва"))
        self.db.upsert_seller(self._make_record(seller_id=3, category="electronics", city="Владивосток"))

        stats = self.db.stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_city"]["Москва"], 2)
        self.assertEqual(stats["by_category"]["electronics"], 2)


class TestExtractSeller(unittest.TestCase):
    def test_basic_extraction(self):
        item = {
            "userId": 12345,
            "itemId": 67890,
            "title": "iPhone 15 Pro Max",
            "priceDetailed": {"value": 95000},
            "images": [{"url": "img1.jpg"}, {"url": "img2.jpg"}],
            "seller": {
                "registrationDate": "2022-05-10",
                "itemsCount": 150,
            },
        }
        rec = extract_seller_from_item(item, "electronics", "Москва")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.seller_id, 12345)
        self.assertEqual(rec.item_id, 67890)
        self.assertEqual(rec.price, 95000)
        self.assertEqual(rec.photos_count, 2)
        self.assertEqual(rec.category, "electronics")
        self.assertEqual(rec.city, "Москва")
        self.assertEqual(rec.items_count, 150)

    def test_missing_seller_id(self):
        item = {"itemId": 1, "title": "test"}
        rec = extract_seller_from_item(item, "electronics", "Москва")
        self.assertIsNone(rec)

    def test_fallback_price_field(self):
        item = {"userId": 1, "id": 2, "title": "test", "price": 500}
        rec = extract_seller_from_item(item, "clothing", "Владивосток")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.price, 500)
        self.assertEqual(rec.item_id, 2)

    def test_no_images(self):
        item = {"userId": 1, "itemId": 2, "title": "test"}
        rec = extract_seller_from_item(item, "beauty", "Москва")
        self.assertEqual(rec.photos_count, 0)

    def test_title_truncation(self):
        item = {"userId": 1, "itemId": 2, "title": "x" * 1000}
        rec = extract_seller_from_item(item, "auto_parts", "Москва")
        self.assertEqual(len(rec.title), 500)


class TestBackoffDelay(unittest.TestCase):
    def test_increases_with_attempt(self):
        d0 = _backoff_delay(0, base=2.0, jitter=0.0)
        d1 = _backoff_delay(1, base=2.0, jitter=0.0)
        d2 = _backoff_delay(2, base=2.0, jitter=0.0)
        self.assertEqual(d0, 2.0)
        self.assertEqual(d1, 4.0)
        self.assertEqual(d2, 8.0)

    def test_jitter_range(self):
        for _ in range(20):
            d = _backoff_delay(0, base=1.0, jitter=1.0)
            self.assertGreaterEqual(d, 1.0)
            self.assertLessEqual(d, 2.0)


class TestAvitoClient(unittest.TestCase):
    @patch("scripts.avito_outreach.parser.urllib.request.OpenerDirector.open")
    def test_fetch_items_success(self, mock_open):
        response_data = {"items": [{"userId": 1, "itemId": 2, "title": "test"}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        client = AvitoClient()
        result = client.fetch_items(category_id=6, city_id=637640, page=1)
        self.assertIsNotNone(result)
        self.assertIn("items", result)


class TestAvitoParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = SellerDB(db_path=Path(self.tmp.name))

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_dry_run_no_db_writes(self):
        mock_client = MagicMock()
        mock_client.fetch_items.return_value = {
            "items": [
                {"userId": 100, "itemId": 1, "title": "Item 1", "price": 500},
                {"userId": 200, "itemId": 2, "title": "Item 2", "price": 1000},
            ]
        }

        parser = AvitoParser(
            categories={"electronics": 6},
            cities={"moscow": {"id": 637640, "name": "Москва"}},
            db=self.db,
            client=mock_client,
            max_pages=1,
        )
        stats = parser.run(dry_run=True)

        self.assertEqual(stats.sellers_new, 2)
        self.assertEqual(self.db.count(), 0)  # dry run — nothing written

    @patch("scripts.avito_outreach.parser.time.sleep")
    def test_pagination_stops_on_empty(self, mock_sleep):
        call_count = 0

        def mock_fetch(cat_id, city_id, page, max_retries=3):
            nonlocal call_count
            call_count += 1
            if page == 1:
                # Return exactly ITEMS_PER_PAGE items so parser continues to page 2
                return {"items": [{"userId": i, "itemId": i, "title": f"Item {i}"} for i in range(50)]}
            return {"items": []}

        mock_client = MagicMock()
        mock_client.fetch_items.side_effect = mock_fetch

        parser = AvitoParser(
            categories={"electronics": 6},
            cities={"moscow": {"id": 637640, "name": "Москва"}},
            db=self.db,
            client=mock_client,
            max_pages=10,
        )
        stats = parser.run()

        self.assertEqual(stats.pages_fetched, 2)  # page 1 full, page 2 empty
        self.assertEqual(stats.items_seen, 50)

    @patch("scripts.avito_outreach.parser.time.sleep")
    def test_full_flow_persists_sellers(self, mock_sleep):
        mock_client = MagicMock()
        mock_client.fetch_items.return_value = {
            "items": [
                {"userId": 100, "itemId": 1, "title": "Seller 100 item", "price": 500,
                 "images": [{"url": "a.jpg"}], "seller": {"registrationDate": "2023-01-01", "itemsCount": 10}},
                {"userId": 200, "itemId": 2, "title": "Seller 200 item", "price": 1000,
                 "images": [], "seller": {}},
            ]
        }

        parser = AvitoParser(
            categories={"electronics": 6},
            cities={"moscow": {"id": 637640, "name": "Москва"}},
            db=self.db,
            client=mock_client,
            max_pages=1,
        )
        stats = parser.run()

        self.assertEqual(stats.sellers_new, 2)
        self.assertEqual(self.db.count(), 2)

        db_stats = self.db.stats()
        self.assertEqual(db_stats["by_city"]["Москва"], 2)
        self.assertEqual(db_stats["by_category"]["electronics"], 2)


class TestConfig(unittest.TestCase):
    def test_categories_have_ids(self):
        for key, cat_id in CATEGORIES.items():
            self.assertIsInstance(cat_id, int)
            self.assertGreater(cat_id, 0)

    def test_cities_have_ids_and_names(self):
        for key, city in CITIES.items():
            self.assertIn("id", city)
            self.assertIn("name", city)
            self.assertIsInstance(city["id"], int)


if __name__ == "__main__":
    unittest.main()
