#!/usr/bin/env python3
"""
Batch test: parse 10 random 1688 products, collect stats.
Usage: python3 scripts/batch_parse_test.py
"""

import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from skills.calc.parser_1688 import Parser1688

# 10 products across different categories
PRODUCTS = [
    # From spike (known IDs)
    {"id": "790251400429", "expected_cat": "подшипники (промышленное)"},
    {"id": "822970193916", "expected_cat": "одежда (футболки)"},
    {"id": "647997819110", "expected_cat": "электроника (подставки ноутбук)"},
    {"id": "888640675772", "expected_cat": "электроника (акустика)"},
    {"id": "973806508798", "expected_cat": "оборудование (снегоуборщик)"},
    # New IDs — diverse categories
    {"id": "733761829621", "expected_cat": "одежда"},
    {"id": "583658240815", "expected_cat": "товары для дома"},
    {"id": "595045370736", "expected_cat": "игрушки"},
    {"id": "520537149440", "expected_cat": "косметика"},
    {"id": "606628051926", "expected_cat": "товары для дома"},
]

def main():
    parser = Parser1688(enable_cache=False, debug=True)
    results = []

    for i, product in enumerate(PRODUCTS, 1):
        offer_id = product["id"]
        url = f"https://detail.1688.com/offer/{offer_id}.html"
        print(f"\n{'='*60}")
        print(f"[{i}/10] Parsing {offer_id} (expected: {product['expected_cat']})")
        print(f"{'='*60}")

        t0 = time.time()
        try:
            result = parser.parse(url)
            elapsed = round(time.time() - t0, 1)
            result["_elapsed_sec"] = elapsed
            result["_expected_cat"] = product["expected_cat"]
            results.append(result)

            if result.get("success"):
                price = result.get("price_cny", {})
                print(f"  ✅ SUCCESS ({elapsed}s)")
                print(f"  title:     {result.get('title', '?')[:60]}")
                print(f"  price:     ¥{price.get('min','?')} - ¥{price.get('max','?')}")
                print(f"  variants:  {len(price.get('variants', []))}")
                print(f"  weight:    {result.get('weight_kg', 'NULL')}")
                print(f"  dims:      {result.get('dimensions', 'NULL')}")
                print(f"  image:     {'YES' if result.get('image_url') else 'NULL'}")
                print(f"  min_order: {result.get('min_order', 'NULL')}")
                print(f"  category:  {result.get('category', 'NULL')}")
            else:
                print(f"  ❌ FAILED ({elapsed}s): {result.get('error', '?')[:100]}")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            print(f"  💥 EXCEPTION ({elapsed}s): {e}")
            results.append({
                "success": False,
                "offer_id": offer_id,
                "error": str(e),
                "_elapsed_sec": elapsed,
                "_expected_cat": product["expected_cat"],
            })

        # Small delay between requests to avoid rate limiting
        if i < len(PRODUCTS):
            print("  ... waiting 3s before next request")
            time.sleep(3)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}\n")

    success = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    print(f"Total: {len(results)} | Success: {len(success)} | Failed: {len(failed)}")
    print(f"Success rate: {len(success)*100//len(results)}%\n")

    # Field coverage
    fields = {
        "title": 0, "price_cny": 0, "weight_kg": 0,
        "dimensions": 0, "image_url": 0, "min_order": 0, "category": 0,
    }
    variants_count = 0

    for r in success:
        for field in fields:
            val = r.get(field)
            if val is not None and val != "" and val != {}:
                fields[field] += 1
        price = r.get("price_cny", {})
        if isinstance(price, dict) and price.get("variants"):
            variants_count += 1

    print("Field coverage (out of successful parses):")
    print(f"{'Field':<15} {'Found':<8} {'Rate'}")
    print("-" * 35)
    n = len(success) or 1
    for field, count in fields.items():
        print(f"{field:<15} {count}/{len(success):<5} {count*100//n}%")
    print(f"{'variants':<15} {variants_count}/{len(success):<5} {variants_count*100//n}%")

    # Timing
    times = [r.get("_elapsed_sec", 0) for r in results]
    if times:
        print(f"\nTiming: min={min(times)}s, max={max(times)}s, avg={sum(times)/len(times):.1f}s")

    # Save detailed results
    output_path = Path(__file__).parent.parent / "data" / "batch_test_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed results saved to: {output_path}")

    # Failed details
    if failed:
        print(f"\nFailed products:")
        for r in failed:
            print(f"  {r.get('offer_id','?')}: {r.get('error','?')[:80]}")


if __name__ == "__main__":
    main()
