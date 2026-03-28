#!/usr/bin/env python3
"""
Tests for 1688 parser module.

Unit tests (no API calls) + integration tests (real URLs with Scrapling + Haiku).

Usage:
  python test_parser_1688.py          # unit tests only
  python test_parser_1688.py --live   # unit + live integration tests (5 real URLs)
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from parser_1688 import (
    extract_offer_id,
    is_1688_url,
    _parse_weight,
    FileCache,
    ScraplingLLMParser,
    Parser1688,
)


# ---------------------------------------------------------------------------
# Unit tests (no network calls)
# ---------------------------------------------------------------------------

def test_extract_offer_id():
    """Test offer ID extraction from various URL formats."""
    assert extract_offer_id("https://detail.1688.com/offer/790251400429.html") == "790251400429"
    assert extract_offer_id("https://detail.1688.com/offer/888640675772.html") == "888640675772"
    assert extract_offer_id("https://detail.1688.com/offer/595045370736.html?spm=abc") == "595045370736"
    assert extract_offer_id("https://m.1688.com/offer/703974508333.html") == "703974508333"
    assert extract_offer_id("not a url") is None
    assert extract_offer_id("https://1688.com/") is None
    print("PASS: test_extract_offer_id")


def test_is_1688_url():
    """Test URL detection in messages."""
    # Should find URL
    assert is_1688_url("https://detail.1688.com/offer/790251400429.html") is not None
    assert is_1688_url("посмотри https://detail.1688.com/offer/790251400429.html 500 штук") is not None
    assert is_1688_url("http://detail.1688.com/offer/123456789.html") is not None

    # Should NOT find URL
    assert is_1688_url("500 кг одежда из Гуанчжоу") is None
    assert is_1688_url("1688 штук кроссовок") is None
    assert is_1688_url("https://taobao.com/item/123.html") is None
    print("PASS: test_is_1688_url")


def test_parse_weight():
    """Test weight parsing from various formats."""
    assert _parse_weight("500g") == 0.5
    assert _parse_weight("2.5kg") == 2.5
    assert _parse_weight("300克") == 0.3
    assert _parse_weight("1.5千克") == 1.5
    assert _parse_weight("2кг") == 2.0
    assert _parse_weight("150г") == 0.15
    assert _parse_weight("0.8") == 0.8  # bare number, assume kg
    assert _parse_weight("unknown") is None
    assert _parse_weight("") is None
    print("PASS: test_parse_weight")


def test_file_cache():
    """Test file-based cache with TTL."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = FileCache(cache_dir=Path(tmpdir), ttl=2)

        # Miss
        assert cache.get("test123") is None

        # Put + hit
        cache.put("test123", {"success": True, "title": "Test Product", "price_cny": 10.0})
        hit = cache.get("test123")
        assert hit is not None
        assert hit["title"] == "Test Product"
        assert hit["_cached"] is True

        # TTL expiration (wait 3 sec, TTL is 2)
        time.sleep(3)
        assert cache.get("test123") is None

    print("PASS: test_file_cache")


def test_full_flow_simulation():
    """Simulate the full flow: parser output -> calculator input."""
    from calculator import CargoParams, calculate, load_rates

    rates_path = str(Path(__file__).parent.parent.parent / "data" / "companies" / "test-company" / "rates.json")
    rates = load_rates(rates_path)

    # Simulate: parser returned title + price + weight, user said "500 штук в Москву"
    parser_result = {
        "success": True,
        "title": "Кроссовки женские спортивные",
        "price_cny": {"min": 45.0, "max": 55.0, "variants": []},
        "weight_kg": 0.3,
        "offer_id": "790251400429",
    }

    # LLM would construct these params from parser + user message
    # Use min price for calculation
    params = CargoParams(
        product=parser_result["title"],
        pieces=500,
        weight_per_piece_kg=parser_result["weight_kg"],
        price_per_piece_cny=parser_result["price_cny"]["min"],
        origin="Гуанчжоу",
        destination="Москва",
    )

    result = calculate(rates, params)
    assert result["success"]
    assert result["params"]["weight_kg"] == 150.0  # 500 * 0.3
    assert "Кроссовки" in result["summary"]
    print("PASS: test_full_flow_simulation")


# ---------------------------------------------------------------------------
# Live integration tests (real URLs — requires network + API keys)
# ---------------------------------------------------------------------------

# Test URLs — diverse categories
LIVE_TEST_URLS = [
    {
        "url": "https://detail.1688.com/offer/822970193916.html",
        "category": "clothing",
        "expected_title_contains": None,  # we'll check it's not a store name
        "expected_has_price": True,
    },
    {
        "url": "https://detail.1688.com/offer/888640675772.html",
        "category": "electronics",
        "expected_title_contains": None,
        "expected_has_price": True,
    },
    {
        "url": "https://detail.1688.com/offer/595045370736.html",
        "category": "toys",
        "expected_title_contains": None,
        "expected_has_price": True,
    },
    {
        "url": "https://detail.1688.com/offer/752205332799.html",
        "category": "electronics",
        "expected_title_contains": None,
        "expected_has_price": True,
    },
    {
        "url": "https://detail.1688.com/offer/790251400429.html",
        "category": "industrial",
        "expected_title_contains": None,
        "expected_has_price": True,
    },
]

# Known store names from previous tests — these should NOT appear as titles
KNOWN_STORE_NAMES = [
    "深圳市龙华区至圣博莉服饰贸易行",
    "北京恒永盛经贸有限公司",
    "咸安区华容机械厂",
    "仪征市暖派工艺品有限公司",
]


@pytest.mark.skipif(
    not os.environ.get("PARSER_LIVE_TESTS"),
    reason="Live integration test — set PARSER_LIVE_TESTS=1 to run",
)
def test_live_urls():
    """
    Test parser against real 1688 URLs.
    Saves results to spike/scrapling_results.json and debug text to spike/debug/.
    """
    debug_dir = Path(__file__).parent.parent.parent / "spike" / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    results = []
    scorecard = {
        "total": len(LIVE_TEST_URLS),
        "loaded": 0,
        "title_ok": 0,
        "price_ok": 0,
        "weight_found": 0,
        "variants_found": 0,
        "not_store_name": 0,
    }

    parser = Parser1688(enable_cache=False, debug=True)

    for i, test_case in enumerate(LIVE_TEST_URLS, 1):
        url = test_case["url"]
        category = test_case["category"]
        offer_id = extract_offer_id(url)

        print(f"\n--- [{i}/{len(LIVE_TEST_URLS)}] {category}: {offer_id} ---")
        t0 = time.time()

        try:
            result = parser.parse(url)
            elapsed = round(time.time() - t0, 1)
            result["_elapsed_sec"] = elapsed
            result["_category"] = category

            if result.get("success"):
                scorecard["loaded"] += 1

                # Check title
                title = result.get("title", "")
                is_store = any(name in (title or "") for name in KNOWN_STORE_NAMES)
                if title and not is_store:
                    scorecard["title_ok"] += 1
                    scorecard["not_store_name"] += 1
                    print(f"  Title: {title}")
                elif is_store:
                    print(f"  Title: STORE NAME (bad): {title}")
                else:
                    print(f"  Title: None")

                # Check price
                price = result.get("price_cny")
                if price:
                    scorecard["price_ok"] += 1
                    if isinstance(price, dict):
                        print(f"  Price: {price.get('min')} - {price.get('max')} CNY")
                        variants = price.get("variants", [])
                        if variants:
                            scorecard["variants_found"] += 1
                            print(f"  Variants: {len(variants)} found")
                    else:
                        print(f"  Price: {price} CNY")

                # Check weight
                weight = result.get("weight_kg")
                if weight:
                    scorecard["weight_found"] += 1
                    print(f"  Weight: {weight} kg")
                else:
                    print(f"  Weight: None")

                # Other fields
                if result.get("min_order"):
                    print(f"  Min order: {result['min_order']}")
                if result.get("category"):
                    print(f"  Category: {result['category']}")

                print(f"  Elapsed: {elapsed}s")
            else:
                print(f"  FAILED: {result.get('error', 'unknown')[:100]}")
                result["_elapsed_sec"] = elapsed
                result["_category"] = category

        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            result = {
                "success": False,
                "error": str(e),
                "_elapsed_sec": elapsed,
                "_category": category,
                "offer_id": offer_id,
            }
            print(f"  EXCEPTION: {e}")
            traceback.print_exc()

        results.append(result)

    # Save results
    results_path = Path(__file__).parent.parent.parent / "spike" / "scrapling_results.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print scorecard
    print("\n" + "=" * 60)
    print("SCORECARD")
    print("=" * 60)
    print(f"  Pages loaded:      {scorecard['loaded']}/{scorecard['total']}")
    print(f"  Title extracted:   {scorecard['title_ok']}/{scorecard['loaded']}")
    print(f"  Not store name:    {scorecard['not_store_name']}/{scorecard['loaded']}")
    print(f"  Price extracted:   {scorecard['price_ok']}/{scorecard['loaded']}")
    print(f"  Weight found:      {scorecard['weight_found']}/{scorecard['loaded']}")
    print(f"  Variants found:    {scorecard['variants_found']}/{scorecard['loaded']}")
    print(f"  Results saved to:  {results_path}")
    print(f"  Debug text saved:  {debug_dir}/")

    return scorecard


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Always run unit tests
    test_extract_offer_id()
    test_is_1688_url()
    test_parse_weight()
    test_file_cache()
    test_full_flow_simulation()
    print("\n=== ALL UNIT TESTS PASSED ===")

    # Run live tests if --live flag
    if "--live" in sys.argv:
        print("\n=== STARTING LIVE INTEGRATION TESTS ===")
        print("(This will take 2-5 minutes — loading 5 real pages + LLM extraction)\n")
        import traceback
        scorecard = test_live_urls()
    else:
        print("\nNote: Run with --live flag to test against real 1688 URLs")
        print("  python test_parser_1688.py --live")
