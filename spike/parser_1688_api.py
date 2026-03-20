"""
1688.com Product Parser — API-based approach.

Direct Playwright scraping FAILED (5% success rate — anti-bot redirect to login).
This module uses third-party APIs to get 1688 product data.

Tested and recommended APIs (in order of preference):

1. LovBuy API — cheapest, simplest, working endpoint confirmed
   - Cost: 5 CNY / 100 requests (~$0.007/req)
   - Min balance: 5 CNY (~$0.70)
   - Registration: lovbuy.com (create account, add balance)
   - Endpoint: https://www.lovbuy.com/api/getinfo.php

2. OneBound API — more fields (SKUs, props), Russian language support
   - Cost: Free trial available, paid plans after
   - Registration: console.open.onebound.cn
   - Endpoint: https://api-gw.onebound.cn/1688/item_get/

3. Apify Actor — cloud scraping, handles anti-bot
   - Cost: Free $5/month credit (~500 runs)
   - Registration: apify.com
   - Actor: ecomscrape/1688-product-details-page-scraper

NOTE: RapidAPI is GEO-BLOCKED (HTTP 451) from our server location.
      TMAPI has SSL cert issues on api.tmapi.top.
      OTCommerce costs $150/month minimum — too expensive for MVP.
"""

import json
import re
import urllib.request
import urllib.error
import ssl
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Product1688:
    """Structured product data from 1688."""
    url: str
    offer_id: str
    title: Optional[str] = None
    price_cny: Optional[float] = None
    weight_kg: Optional[float] = None
    dimensions_cm: Optional[str] = None
    image_url: Optional[str] = None
    images: list[str] = None
    min_order: Optional[int] = None
    seller_name: Optional[str] = None
    location: Optional[str] = None
    source: str = "api"
    raw_data: Optional[dict] = None

    def __post_init__(self):
        if self.images is None:
            self.images = []

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_data", None)
        return d


def extract_offer_id(url: str) -> Optional[str]:
    """Extract offer ID from 1688 URL."""
    match = re.search(r'/offer/(\d+)', url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# LovBuy API (recommended — cheapest, simplest)
# ---------------------------------------------------------------------------

class LovBuyParser:
    """
    Parse 1688 products via LovBuy API.

    Registration: https://www.lovbuy.com/ (create account, deposit 5 CNY)
    Get API key from account center.
    Cost: 5 CNY / 100 requests.
    """

    BASE_URL = "https://www.lovbuy.com/api/getinfo.php"
    WEBSITE_1688 = "2"  # 1=taobao, 2=1688

    def __init__(self, api_key: str):
        self.api_key = api_key

    def parse(self, url: str) -> Product1688:
        """Parse a 1688 product URL."""
        offer_id = extract_offer_id(url)
        if not offer_id:
            raise ValueError(f"Cannot extract offer ID from URL: {url}")

        api_url = (
            f"{self.BASE_URL}"
            f"?key={self.api_key}"
            f"&website={self.WEBSITE_1688}"
            f"&productid={offer_id}"
            f"&lang=en"
        )

        data = self._request(api_url)

        if data.get("status") == 501:
            raise ValueError("Invalid API key")
        if data.get("status") == 510:
            raise ValueError("Insufficient balance (deposit 5 CNY)")
        if data.get("status") == 544:
            raise ValueError(f"Failed to fetch product: {offer_id}")
        if data.get("status") != 200:
            raise ValueError(f"API error: status={data.get('status')}")

        return self._parse_response(url, offer_id, data)

    def _request(self, url: str) -> dict:
        req = urllib.request.Request(url)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_response(self, url: str, offer_id: str, data: dict) -> Product1688:
        product = Product1688(url=url, offer_id=offer_id, source="lovbuy")

        # Title
        product.title = data.get("title")

        # Price — may be in various formats
        price = data.get("price") or data.get("start_price")
        if price:
            try:
                product.price_cny = float(str(price).replace("¥", "").replace(",", "").strip())
            except (ValueError, TypeError):
                pass

        # Images
        images = data.get("images") or data.get("img") or []
        if isinstance(images, list):
            product.images = [img if isinstance(img, str) else img.get("url", "") for img in images]
        if product.images:
            product.image_url = product.images[0]

        # Min order
        min_order = data.get("min_order") or data.get("minorder")
        if min_order:
            try:
                product.min_order = int(min_order)
            except (ValueError, TypeError):
                pass

        # Try to extract weight/dimensions from properties/attributes
        props = data.get("props") or data.get("properties") or data.get("attributes") or {}
        if isinstance(props, dict):
            for key, val in props.items():
                key_lower = str(key).lower()
                val_str = str(val)
                if any(w in key_lower for w in ["weight", "重量", "净重"]):
                    product.weight_kg = self._parse_weight(val_str)
                elif any(w in key_lower for w in ["dimension", "size", "尺寸", "规格"]):
                    product.dimensions_cm = val_str

        product.raw_data = data
        return product

    @staticmethod
    def _parse_weight(text: str) -> Optional[float]:
        """Parse weight string to kg."""
        match = re.search(r'(\d+\.?\d*)\s*(kg|g|千克|克)', text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            if unit in ("g", "克"):
                return value / 1000
            return value
        return None


# ---------------------------------------------------------------------------
# OneBound API (more fields, Russian support)
# ---------------------------------------------------------------------------

class OneBoundParser:
    """
    Parse 1688 products via OneBound API.

    Registration: http://console.open.onebound.cn/console/?i=.open.api.test
    Docs: https://open.onebound.cn/help/api/1688.item_get.html
    Free trial available.
    """

    BASE_URL = "https://api-gw.onebound.cn/1688/item_get/"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret

    def parse(self, url: str, lang: str = "cn") -> Product1688:
        """Parse a 1688 product URL. lang: cn/en/ru"""
        offer_id = extract_offer_id(url)
        if not offer_id:
            raise ValueError(f"Cannot extract offer ID from URL: {url}")

        api_url = (
            f"{self.BASE_URL}"
            f"?key={self.api_key}"
            f"&secret={self.api_secret}"
            f"&num_iid={offer_id}"
            f"&lang={lang}"
        )

        data = self._request(api_url)

        if data.get("error_code") and data["error_code"] != "0000":
            raise ValueError(f"API error: {data.get('error', data.get('reason', 'Unknown'))}")

        item = data.get("item", {})
        return self._parse_item(url, offer_id, item, data)

    def _request(self, url: str) -> dict:
        req = urllib.request.Request(url)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_item(self, url: str, offer_id: str, item: dict, raw: dict) -> Product1688:
        product = Product1688(url=url, offer_id=offer_id, source="onebound")

        product.title = item.get("title")

        # Price
        price = item.get("price") or item.get("orginal_price")
        if price:
            try:
                product.price_cny = float(str(price).replace("¥", "").replace(",", "").strip())
            except (ValueError, TypeError):
                pass

        # Images
        img_list = item.get("item_imgs", [])
        if isinstance(img_list, list):
            product.images = [
                img.get("url", "") if isinstance(img, dict) else str(img)
                for img in img_list
            ]
        product.image_url = item.get("pic_url") or (product.images[0] if product.images else None)

        # Seller
        product.seller_name = item.get("nick")
        product.location = item.get("location")

        # Min order
        min_num = item.get("min_num")
        if min_num:
            try:
                product.min_order = int(min_num)
            except (ValueError, TypeError):
                pass

        # Props — try to find weight/dimensions
        props_name = item.get("props_name", "")
        props = item.get("props", [])
        if isinstance(props, list):
            for prop in props:
                name = str(prop.get("name", "")).lower()
                value = str(prop.get("value", ""))
                if any(w in name for w in ["weight", "重量", "净重"]):
                    product.weight_kg = LovBuyParser._parse_weight(value)
                elif any(w in name for w in ["dimension", "size", "尺寸", "规格"]):
                    product.dimensions_cm = value

        product.raw_data = raw
        return product


# ---------------------------------------------------------------------------
# Apify Actor (cloud scraping, free tier)
# ---------------------------------------------------------------------------

class ApifyParser:
    """
    Parse 1688 products via Apify cloud scraper.

    Registration: https://apify.com (free $5/month credit)
    Actor: ecomscrape/1688-product-details-page-scraper
    Uses Apify's infrastructure to handle anti-bot.
    """

    ACTOR_ID = "ecomscrape/1688-product-details-page-scraper"

    def __init__(self, api_token: str):
        self.api_token = api_token

    def parse(self, url: str) -> Product1688:
        """Parse a 1688 product URL via Apify actor."""
        try:
            from apify_client import ApifyClient
        except ImportError:
            raise ImportError("Install apify-client: pip install apify-client")

        offer_id = extract_offer_id(url) or ""

        client = ApifyClient(self.api_token)
        run_input = {"urls": [url]}

        run = client.actor(self.ACTOR_ID).call(run_input=run_input, timeout_secs=120)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

        if not items:
            raise ValueError(f"No data returned for: {url}")

        data = items[0]
        return self._parse_result(url, offer_id, data)

    def _parse_result(self, url: str, offer_id: str, data: dict) -> Product1688:
        product = Product1688(url=url, offer_id=offer_id, source="apify")

        product.title = data.get("title")

        price = data.get("price") or data.get("priceRange")
        if price:
            match = re.search(r'(\d+\.?\d*)', str(price))
            if match:
                product.price_cny = float(match.group(1))

        images = data.get("images") or data.get("imageUrls") or []
        product.images = images if isinstance(images, list) else []
        if product.images:
            product.image_url = product.images[0]

        # Try weight/dimensions from attributes
        attrs = data.get("attributes") or data.get("specs") or {}
        if isinstance(attrs, dict):
            for key, val in attrs.items():
                key_lower = str(key).lower()
                if any(w in key_lower for w in ["weight", "重量"]):
                    product.weight_kg = LovBuyParser._parse_weight(str(val))
                elif any(w in key_lower for w in ["dimension", "size", "尺寸"]):
                    product.dimensions_cm = str(val)

        product.raw_data = data
        return product


# ---------------------------------------------------------------------------
# Multi-source parser (tries APIs in order)
# ---------------------------------------------------------------------------

class Parser1688:
    """
    High-level parser that tries multiple APIs.
    Configure with at least one API key.
    """

    def __init__(
        self,
        lovbuy_key: str = "",
        onebound_key: str = "",
        onebound_secret: str = "",
        apify_token: str = "",
    ):
        self.parsers = []

        if lovbuy_key:
            self.parsers.append(("lovbuy", LovBuyParser(lovbuy_key)))
        if onebound_key and onebound_secret:
            self.parsers.append(("onebound", OneBoundParser(onebound_key, onebound_secret)))
        if apify_token:
            self.parsers.append(("apify", ApifyParser(apify_token)))

        if not self.parsers:
            raise ValueError(
                "No API keys configured. Provide at least one of:\n"
                "  - lovbuy_key (cheapest: ~$0.007/req)\n"
                "  - onebound_key + onebound_secret (free trial)\n"
                "  - apify_token (free $5/month)"
            )

    def parse(self, url: str) -> Product1688:
        """Parse product, trying each API in order until one succeeds."""
        errors = []
        for name, parser in self.parsers:
            try:
                return parser.parse(url)
            except Exception as e:
                errors.append(f"{name}: {e}")

        raise RuntimeError(
            f"All APIs failed for {url}:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    # Check for API keys in environment
    lovbuy_key = os.environ.get("LOVBUY_API_KEY", "")
    onebound_key = os.environ.get("ONEBOUND_API_KEY", "")
    onebound_secret = os.environ.get("ONEBOUND_API_SECRET", "")
    apify_token = os.environ.get("APIFY_TOKEN", "")

    if not any([lovbuy_key, onebound_key, apify_token]):
        print("=" * 60)
        print("1688 API Parser — No API keys configured")
        print("=" * 60)
        print()
        print("Set one of these environment variables:")
        print("  export LOVBUY_API_KEY=...        # cheapest: ~$0.007/req")
        print("  export ONEBOUND_API_KEY=...      # free trial available")
        print("  export ONEBOUND_API_SECRET=...")
        print("  export APIFY_TOKEN=...           # free $5/month")
        print()
        print("How to get keys:")
        print("  LovBuy:   Register at lovbuy.com, deposit 5 CNY ($0.70)")
        print("  OneBound: Register at console.open.onebound.cn (free trial)")
        print("  Apify:    Register at apify.com (free $5/month credit)")
        print()
        print("Then run:")
        print("  python parser_1688_api.py https://detail.1688.com/offer/790251400429.html")
        sys.exit(1)

    parser = Parser1688(
        lovbuy_key=lovbuy_key,
        onebound_key=onebound_key,
        onebound_secret=onebound_secret,
        apify_token=apify_token,
    )

    urls = sys.argv[1:] or [
        "https://detail.1688.com/offer/790251400429.html",
        "https://detail.1688.com/offer/888640675772.html",
        "https://detail.1688.com/offer/595045370736.html",
        "https://detail.1688.com/offer/703974508333.html",
        "https://detail.1688.com/offer/656173618669.html",
    ]

    print(f"Parsing {len(urls)} URLs...\n")

    success = 0
    for url in urls:
        print(f"URL: {url}")
        try:
            product = parser.parse(url)
            print(f"  Title: {product.title}")
            print(f"  Price: ¥{product.price_cny}")
            print(f"  Weight: {product.weight_kg} kg")
            print(f"  Dimensions: {product.dimensions_cm}")
            print(f"  Images: {len(product.images)}")
            print(f"  Source: {product.source}")
            success += 1
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    print(f"Results: {success}/{len(urls)} parsed successfully")
