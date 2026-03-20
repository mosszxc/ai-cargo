"""
Spike test #4: Test APIs that are actually accessible from our location.
Focus on:
1. Onebound (api-gw.onebound.cn) — has free trial, Chinese-based
2. TMAPI (api.tmapi.top) — correct endpoint paths
3. Alibaba Open Platform (aop.alibaba.com) — official

RapidAPI is GEO-BLOCKED (451) from our server location, so skip all RapidAPI options.
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
import ssl


TEST_OFFER_IDS = [
    "790251400429",
    "888640675772",
    "595045370736",
    "703974508333",
    "656173618669",
]


def http_get(url: str, headers: dict = None, timeout: int = 30) -> tuple[int, str]:
    """Simple HTTP GET."""
    req = urllib.request.Request(url, headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def test_onebound():
    """
    Onebound API (api-gw.onebound.cn)
    - Chinese company, no geo-restrictions
    - Has free trial: console.open.onebound.cn
    - Endpoint: https://api-gw.onebound.cn/1688/item_get/?key=<key>&secret=<secret>&num_iid=<id>
    """
    print("\n" + "=" * 60)
    print("API: OneBound (api-gw.onebound.cn)")
    print("=" * 60)
    print("  Registration: http://console.open.onebound.cn/console/?i=.open.api.test")
    print("  Docs: https://open.onebound.cn/help/api/1688.item_get.html")
    print("  Supports: title, price, images, SKUs, seller info, props")
    print("  Languages: cn, en, ru")

    # Test without key to verify endpoint is reachable
    url = f"https://api-gw.onebound.cn/1688/item_get/?key=test&secret=test&num_iid={TEST_OFFER_IDS[0]}"
    print(f"\n  Testing endpoint reachability (dummy key)...")
    start = time.time()
    status, body = http_get(url)
    elapsed = time.time() - start
    print(f"    Status: {status}, Time: {elapsed:.1f}s")
    print(f"    Response: {body[:500]}")

    # Try with lang=ru
    url2 = f"https://api-gw.onebound.cn/1688/item_get/?key=test&secret=test&num_iid={TEST_OFFER_IDS[0]}&lang=ru"
    print(f"\n  Testing with lang=ru...")
    status2, body2 = http_get(url2)
    print(f"    Status: {status2}")
    print(f"    Response: {body2[:500]}")

    reachable = status != 0 and "ECONNREFUSED" not in body and "timeout" not in body.lower()
    print(f"\n  Endpoint reachable: {reachable}")
    return {
        "service": "OneBound",
        "reachable": reachable,
        "status": status,
        "response_preview": body[:200],
        "price": "Free trial available, paid plans unknown",
        "fields": "title, price, images, SKUs, seller_id, location, props, total_sold",
        "languages": "cn, en, ru",
    }


def test_tmapi_corrected():
    """
    TMAPI (api.tmapi.top) — try various endpoint path formats.
    Based on docs: {base}/ali/item_detail or {base}/taobao/item_detail
    Also try: api.tmapi.io (alternative domain)
    """
    print("\n" + "=" * 60)
    print("API: TMAPI (tmapi.top / tmapi.io)")
    print("=" * 60)
    print("  Registration: console.tmapi.io/user/signup")

    # Try various base URLs and paths
    endpoints = [
        ("api.tmapi.top", "/ali/item_detail"),
        ("api.tmapi.top", "/ali/item_detail_1688"),
        ("api.tmapi.top", "/1688/item_detail"),
        ("api.tmapi.top", "/taobao/item_detail"),
        ("api.tmapi.io", "/ali/item_detail"),
        ("api.tmapi.io", "/1688/item_detail"),
    ]

    for host, path in endpoints:
        url = f"https://{host}{path}?item_id={TEST_OFFER_IDS[0]}&apiToken=test123"
        print(f"\n  Testing: {host}{path}")
        status, body = http_get(url, timeout=10)
        print(f"    Status: {status}, Response: {body[:200]}")

    return {"service": "TMAPI", "note": "Need correct endpoint path"}


def test_alibaba_open_platform():
    """
    Alibaba Open Platform (aop.alibaba.com)
    Official API — requires registered app.
    """
    print("\n" + "=" * 60)
    print("API: Alibaba Open Platform (aop.alibaba.com)")
    print("=" * 60)

    url = "https://aop.alibaba.com/api/api.htm?ns=cn.alibaba.open&n=alibaba.product.get&v=1"
    print(f"\n  Testing: {url[:70]}...")
    status, body = http_get(url, timeout=10)
    print(f"    Status: {status}")
    print(f"    Response: {body[:500]}")

    return {"service": "Alibaba Open Platform", "note": "Requires registered app + secret"}


def test_oxylabs():
    """
    Oxylabs 1688 Scraper API — enterprise scraping service.
    """
    print("\n" + "=" * 60)
    print("API: Oxylabs (oxylabs.io)")
    print("=" * 60)
    print("  Enterprise service, likely expensive")

    # Their API uses a different format
    url = "https://realtime.oxylabs.io/v1/queries"
    headers = {
        "Content-Type": "application/json",
    }
    # Test without auth to see if endpoint exists
    print(f"\n  Testing endpoint reachability...")
    status, body = http_get(url, timeout=10)
    print(f"    Status: {status}")
    print(f"    Response: {body[:300]}")

    return {"service": "Oxylabs", "note": "Enterprise, requires paid account"}


def test_apify():
    """
    Apify 1688 Scraper — cloud scraping platform.
    Has free tier ($5/month credit).
    """
    print("\n" + "=" * 60)
    print("API: Apify (apify.com)")
    print("=" * 60)
    print("  Free tier: $5/month credit")
    print("  Actor: ecomscrape/1688-product-details-page-scraper")

    # Test API endpoint reachability
    url = "https://api.apify.com/v2/acts/ecomscrape~1688-product-details-page-scraper/runs?token=test"
    print(f"\n  Testing endpoint...")
    status, body = http_get(url, timeout=10)
    print(f"    Status: {status}")
    print(f"    Response: {body[:300]}")

    return {"service": "Apify", "note": "Has free tier, actor-based scraping"}


def main():
    print("=" * 60)
    print("1688.com API TESTING (GEO-ACCESSIBLE SERVICES ONLY)")
    print("=" * 60)
    print("NOTE: RapidAPI returns 451 (geo-blocked) — skipped entirely")
    print(f"Testing with offer IDs: {TEST_OFFER_IDS[:3]}...\n")

    results = []
    results.append(test_onebound())
    results.append(test_tmapi_corrected())
    results.append(test_alibaba_open_platform())
    results.append(test_oxylabs())
    results.append(test_apify())

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        reachable = r.get("reachable", "unknown")
        note = r.get("note", "")
        print(f"  {r['service']}: reachable={reachable} {note}")

    with open("/home/dev-moss/cargo-ai-saas/spike/api_probe_v2_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nResults saved to spike/api_probe_v2_results.json")


if __name__ == "__main__":
    main()
