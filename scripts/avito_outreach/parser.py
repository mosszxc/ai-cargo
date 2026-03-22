#!/usr/bin/env python3
"""
Avito seller parser — collects listings via Avito internal JSON API.

Collects sellers across specified categories and cities, stores in SQLite
for subsequent scoring (import-from-China detection).

Usage:
  python -m scripts.avito_outreach.parser [--categories electronics,clothing] [--cities moscow,vladivostok]
  python scripts/avito_outreach/parser.py --dry-run

Env vars:
  AVITO_PROXY_URL  — residential proxy URL (http://user:pass@host:port)
  AVITO_DB_PATH    — SQLite database path (default: data/avito_sellers.db)
  AVITO_MAX_PAGES  — max pages per category/city combo (default: 50)
"""

import json
import logging
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("avito_parser")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AVITO_API_URL = "https://www.avito.ru/web/1/main/items"

CATEGORIES = {
    "electronics": 6,     # Электроника
    "clothing": 27,       # Одежда, обувь, аксессуары
    "beauty": 88,         # Красота и здоровье
    "auto_parts": 9,      # Автотовары
}

CITIES = {
    "moscow": {"id": 637640, "name": "Москва"},
    "vladivostok": {"id": 653240, "name": "Владивосток"},
    "novosibirsk": {"id": 653070, "name": "Новосибирск"},
    "yekaterinburg": {"id": 653040, "name": "Екатеринбург"},
}

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "avito_sellers.db"
ITEMS_PER_PAGE = 50
DEFAULT_MAX_PAGES = 50

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SellerRecord:
    seller_id: int
    item_id: int
    title: str
    price: Optional[int]
    photos_count: int
    category: str
    city: str
    seller_reg_date: Optional[str]
    items_count: Optional[int]


@dataclass
class ParseStats:
    pages_fetched: int = 0
    items_seen: int = 0
    sellers_new: int = 0
    sellers_updated: int = 0
    errors: int = 0
    categories_done: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# SQLite storage
# ---------------------------------------------------------------------------

class SellerDB:
    """SQLite storage for parsed Avito sellers."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sellers (
        seller_id INTEGER PRIMARY KEY,
        item_id INTEGER,
        title TEXT,
        price INTEGER,
        photos_count INTEGER DEFAULT 0,
        category TEXT,
        city TEXT,
        seller_reg_date TEXT,
        items_count INTEGER,
        first_seen_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_sellers_category ON sellers(category);
    CREATE INDEX IF NOT EXISTS idx_sellers_city ON sellers(city);
    CREATE INDEX IF NOT EXISTS idx_sellers_items_count ON sellers(items_count);
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path(os.environ.get("AVITO_DB_PATH", str(DEFAULT_DB_PATH)))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(self.SCHEMA)

    def upsert_seller(self, rec: SellerRecord) -> bool:
        """Insert or update a seller. Returns True if new, False if updated."""
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT seller_id FROM sellers WHERE seller_id = ?",
                (rec.seller_id,),
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE sellers SET
                        item_id = ?, title = ?, price = ?, photos_count = ?,
                        category = ?, city = ?, seller_reg_date = ?,
                        items_count = ?, updated_at = datetime('now')
                    WHERE seller_id = ?
                """, (
                    rec.item_id, rec.title, rec.price, rec.photos_count,
                    rec.category, rec.city, rec.seller_reg_date,
                    rec.items_count, rec.seller_id,
                ))
                return False
            else:
                conn.execute("""
                    INSERT INTO sellers
                        (seller_id, item_id, title, price, photos_count,
                         category, city, seller_reg_date, items_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rec.seller_id, rec.item_id, rec.title, rec.price,
                    rec.photos_count, rec.category, rec.city,
                    rec.seller_reg_date, rec.items_count,
                ))
                return True

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0]

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0]
            by_city = dict(conn.execute(
                "SELECT city, COUNT(*) FROM sellers GROUP BY city"
            ).fetchall())
            by_cat = dict(conn.execute(
                "SELECT category, COUNT(*) FROM sellers GROUP BY category"
            ).fetchall())
        return {"total": total, "by_city": by_city, "by_category": by_cat}


# ---------------------------------------------------------------------------
# HTTP client with proxy + retry
# ---------------------------------------------------------------------------

class AvitoClient:
    """HTTP client for Avito API with proxy support and exponential backoff."""

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url or os.environ.get("AVITO_PROXY_URL")
        self._session_ua = random.choice(USER_AGENTS)

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers = []
        if self.proxy_url:
            proxy_handler = urllib.request.ProxyHandler({
                "http": self.proxy_url,
                "https": self.proxy_url,
            })
            handlers.append(proxy_handler)
        return urllib.request.build_opener(*handlers)

    def fetch_items(
        self,
        category_id: int,
        city_id: int,
        page: int = 1,
        max_retries: int = 3,
    ) -> Optional[dict]:
        """Fetch a page of items from Avito API with exponential backoff.

        Returns parsed JSON response or None on failure.
        """
        params = {
            "categoryId": category_id,
            "locationId": city_id,
            "page": page,
            "limit": ITEMS_PER_PAGE,
            "sort": "date",
        }
        url = f"{AVITO_API_URL}?{urllib.parse.urlencode(params)}"

        headers = {
            "User-Agent": self._session_ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://www.avito.ru/",
            "X-Requested-With": "XMLHttpRequest",
        }

        opener = self._build_opener()

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data
            except urllib.error.HTTPError as e:
                status = e.code
                logger.warning(
                    "HTTP %d on page %d (attempt %d/%d)",
                    status, page, attempt + 1, max_retries,
                )
                if status == 429 or status >= 500:
                    delay = _backoff_delay(attempt)
                    logger.info("Retrying in %.1fs...", delay)
                    time.sleep(delay)
                else:
                    return None
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning(
                    "Network error on page %d (attempt %d/%d): %s",
                    page, attempt + 1, max_retries, e,
                )
                delay = _backoff_delay(attempt)
                time.sleep(delay)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON on page %d", page)
                return None

        return None


def _backoff_delay(attempt: int, base: float = 2.0, jitter: float = 1.0) -> float:
    """Exponential backoff with jitter."""
    return base * (2 ** attempt) + random.uniform(0, jitter)


# ---------------------------------------------------------------------------
# Item extraction
# ---------------------------------------------------------------------------

def extract_seller_from_item(item: dict, category: str, city: str) -> Optional[SellerRecord]:
    """Extract a SellerRecord from a single Avito API item response."""
    try:
        seller_id = item.get("userId")
        if not seller_id:
            return None

        item_id = item.get("itemId") or item.get("id")
        title = item.get("title", "")
        price_val = item.get("priceDetailed", {}).get("value")
        if price_val is None:
            price_val = item.get("price")

        images = item.get("images") or item.get("photos") or []
        photos_count = len(images) if isinstance(images, list) else 0

        # Seller metadata (may be nested)
        seller_info = item.get("seller") or {}
        reg_date = seller_info.get("registrationDate") or seller_info.get("createdAt")
        items_count = seller_info.get("itemsCount") or seller_info.get("totalItems")

        return SellerRecord(
            seller_id=int(seller_id),
            item_id=int(item_id) if item_id else 0,
            title=title[:500],
            price=int(price_val) if price_val else None,
            photos_count=photos_count,
            category=category,
            city=city,
            seller_reg_date=str(reg_date) if reg_date else None,
            items_count=int(items_count) if items_count else None,
        )
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("Failed to extract seller from item: %s", e)
        return None


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

class AvitoParser:
    """Orchestrates fetching items across categories and cities, stores to SQLite."""

    def __init__(
        self,
        categories: Optional[dict] = None,
        cities: Optional[dict] = None,
        db: Optional[SellerDB] = None,
        client: Optional[AvitoClient] = None,
        max_pages: Optional[int] = None,
    ):
        self.categories = categories or CATEGORIES
        self.cities = cities or CITIES
        self.db = db or SellerDB()
        self.client = client or AvitoClient()
        self.max_pages = max_pages or int(os.environ.get("AVITO_MAX_PAGES", str(DEFAULT_MAX_PAGES)))

    def run(self, dry_run: bool = False) -> ParseStats:
        """Run the parser across all configured categories and cities.

        Args:
            dry_run: If True, fetch but don't persist to DB.

        Returns:
            ParseStats with summary counters.
        """
        stats = ParseStats()

        for cat_key, cat_id in self.categories.items():
            for city_key, city_info in self.cities.items():
                city_id = city_info["id"]
                city_name = city_info["name"]
                logger.info(
                    "Parsing: %s / %s (cat=%d, city=%d)",
                    cat_key, city_name, cat_id, city_id,
                )

                page_stats = self._parse_category_city(
                    cat_key, cat_id, city_name, city_id,
                    dry_run=dry_run, stats=stats,
                )
                stats.categories_done.append(f"{cat_key}/{city_key}")

                # Polite delay between category/city combos
                delay = random.uniform(1.0, 3.0)
                time.sleep(delay)

        logger.info(
            "Done: %d pages, %d items, %d new sellers, %d updated, %d errors",
            stats.pages_fetched, stats.items_seen,
            stats.sellers_new, stats.sellers_updated, stats.errors,
        )
        return stats

    def _parse_category_city(
        self,
        category: str,
        category_id: int,
        city: str,
        city_id: int,
        dry_run: bool,
        stats: ParseStats,
    ) -> None:
        """Parse all pages for one category/city combo."""
        for page in range(1, self.max_pages + 1):
            data = self.client.fetch_items(category_id, city_id, page=page)
            if data is None:
                stats.errors += 1
                logger.warning("Failed to fetch page %d for %s/%s", page, category, city)
                break

            stats.pages_fetched += 1

            items = data.get("items") or data.get("mainItems") or []
            if not items:
                logger.info("No more items on page %d for %s/%s", page, category, city)
                break

            for item_data in items:
                stats.items_seen += 1
                rec = extract_seller_from_item(item_data, category, city)
                if rec is None:
                    continue

                if not dry_run:
                    is_new = self.db.upsert_seller(rec)
                    if is_new:
                        stats.sellers_new += 1
                    else:
                        stats.sellers_updated += 1
                else:
                    stats.sellers_new += 1

            # Polite delay between pages
            delay = random.uniform(0.5, 1.5)
            time.sleep(delay)

            # Stop if fewer items than expected (last page)
            if len(items) < ITEMS_PER_PAGE:
                break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Avito seller parser")
    parser.add_argument(
        "--categories",
        type=str,
        default=None,
        help="Comma-separated category keys (default: all). Options: electronics,clothing,beauty,auto_parts",
    )
    parser.add_argument(
        "--cities",
        type=str,
        default=None,
        help="Comma-separated city keys (default: all). Options: moscow,vladivostok,novosibirsk,yekaterinburg",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Max pages per category/city (default: {DEFAULT_MAX_PAGES})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch items but don't save to DB",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print DB stats and exit",
    )

    args = parser.parse_args()

    # Stats mode
    if args.stats:
        db = SellerDB()
        print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
        return

    # Filter categories
    categories = CATEGORIES
    if args.categories:
        keys = [k.strip() for k in args.categories.split(",")]
        categories = {k: v for k, v in CATEGORIES.items() if k in keys}
        if not categories:
            print(f"Unknown categories: {args.categories}", file=sys.stderr)
            print(f"Available: {', '.join(CATEGORIES.keys())}", file=sys.stderr)
            sys.exit(1)

    # Filter cities
    cities = CITIES
    if args.cities:
        keys = [k.strip() for k in args.cities.split(",")]
        cities = {k: v for k, v in CITIES.items() if k in keys}
        if not cities:
            print(f"Unknown cities: {args.cities}", file=sys.stderr)
            print(f"Available: {', '.join(CITIES.keys())}", file=sys.stderr)
            sys.exit(1)

    avito_parser = AvitoParser(
        categories=categories,
        cities=cities,
        max_pages=args.max_pages,
    )

    stats = avito_parser.run(dry_run=args.dry_run)
    print(json.dumps({
        "pages_fetched": stats.pages_fetched,
        "items_seen": stats.items_seen,
        "sellers_new": stats.sellers_new,
        "sellers_updated": stats.sellers_updated,
        "errors": stats.errors,
        "categories_done": stats.categories_done,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
