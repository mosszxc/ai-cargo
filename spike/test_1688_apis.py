"""
Spike test #3: Test third-party APIs for 1688 product data.
Tests multiple API services to find one that works.
"""

import asyncio
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import ssl


# Test offer IDs (from our real URLs)
TEST_OFFER_IDS = [
    "790251400429",
    "888640675772",
    "595045370736",
    "703974508333",
    "656173618669",
]

TEST_URLS = [f"https://detail.1688.com/offer/{oid}.html" for oid in TEST_OFFER_IDS]


def http_get(url: str, headers: dict = None, timeout: int = 30) -> tuple[int, str]:
    """Simple HTTP GET without external dependencies."""
    req = urllib.request.Request(url, headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def http_post(url: str, data: dict, headers: dict = None, timeout: int = 30) -> tuple[int, str]:
    """Simple HTTP POST."""
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers or {}, method="POST")
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def test_tmapi():
    """
    TMAPI (tmapi.top) — Professional E-Commerce API Provider.
    Endpoint: http://api.tmapi.top/taobao/item_detail?item_id={id}&apiToken={token}
    Note: Need to register at console.tmapi.io for apiToken.
    Testing without token to see error response format.
    """
    print("\n" + "=" * 60)
    print("API 1: TMAPI (tmapi.top)")
    print("=" * 60)
    print("  Registration: console.tmapi.io/user/signup")
    print("  Pricing: Unknown (need to register to see)")

    # Test without token to verify endpoint exists
    url = f"http://api.tmapi.top/ali/item_detail?item_id={TEST_OFFER_IDS[0]}"
    print(f"\n  Testing endpoint (no token): {url}")
    start = time.time()
    status, body = http_get(url)
    elapsed = time.time() - start
    print(f"    Status: {status}, Time: {elapsed:.1f}s")
    print(f"    Response: {body[:500]}")

    # Also try with dummy token
    url2 = f"http://api.tmapi.top/ali/item_detail?item_id={TEST_OFFER_IDS[0]}&apiToken=test123"
    print(f"\n  Testing with dummy token: {url2}")
    status2, body2 = http_get(url2)
    print(f"    Status: {status2}")
    print(f"    Response: {body2[:500]}")

    # Try the 1688-specific endpoint
    url3 = f"http://api.tmapi.top/ali/item_detail_1688?item_id={TEST_OFFER_IDS[0]}&apiToken=test123"
    print(f"\n  Testing 1688-specific endpoint: {url3}")
    status3, body3 = http_get(url3)
    print(f"    Status: {status3}")
    print(f"    Response: {body3[:500]}")

    return {"service": "TMAPI", "works": status in (200, 401, 403), "needs_key": True}


def test_rapidapi_1688():
    """
    RapidAPI 1688 API (by mcroni).
    Host: 16882.p.rapidapi.com
    Needs x-rapidapi-key header.
    """
    print("\n" + "=" * 60)
    print("API 2: RapidAPI '1688' (mcroni)")
    print("=" * 60)
    print("  Registration: rapidapi.com (free tier available)")

    # Try without key to verify endpoint
    headers = {
        "x-rapidapi-host": "16882.p.rapidapi.com",
    }
    url = f"https://16882.p.rapidapi.com/product/detail?url={urllib.parse.quote(TEST_URLS[0])}"
    print(f"\n  Testing: {url[:80]}...")
    start = time.time()
    status, body = http_get(url, headers, timeout=15)
    elapsed = time.time() - start
    print(f"    Status: {status}, Time: {elapsed:.1f}s")
    print(f"    Response: {body[:500]}")

    return {"service": "RapidAPI-mcroni", "works": status in (200, 401, 403), "needs_key": True}


def test_rapidapi_1688_v2():
    """
    RapidAPI '1688-api' (by dataapi).
    Host: 1688-api.p.rapidapi.com
    """
    print("\n" + "=" * 60)
    print("API 3: RapidAPI '1688-api' (dataapi)")
    print("=" * 60)

    headers = {
        "x-rapidapi-host": "1688-api.p.rapidapi.com",
    }
    url = f"https://1688-api.p.rapidapi.com/product/detail?url={urllib.parse.quote(TEST_URLS[0])}"
    print(f"\n  Testing: {url[:80]}...")
    start = time.time()
    status, body = http_get(url, headers, timeout=15)
    elapsed = time.time() - start
    print(f"    Status: {status}, Time: {elapsed:.1f}s")
    print(f"    Response: {body[:500]}")

    # Try alternative path
    url2 = f"https://1688-api.p.rapidapi.com/item_detail?num_iid={TEST_OFFER_IDS[0]}"
    print(f"\n  Testing alt path: {url2[:80]}...")
    status2, body2 = http_get(url2, headers, timeout=15)
    print(f"    Status: {status2}")
    print(f"    Response: {body2[:500]}")

    return {"service": "RapidAPI-dataapi", "works": status in (200, 401, 403) or status2 in (200, 401, 403), "needs_key": True}


def test_rapidapi_scraper_1688():
    """
    RapidAPI 'Scraper 1688' (by puspuuus).
    Host: scraper-1688.p.rapidapi.com
    """
    print("\n" + "=" * 60)
    print("API 4: RapidAPI 'Scraper 1688' (puspuuus)")
    print("=" * 60)

    headers = {
        "x-rapidapi-host": "scraper-1688.p.rapidapi.com",
    }
    url = f"https://scraper-1688.p.rapidapi.com/product?url={urllib.parse.quote(TEST_URLS[0])}"
    print(f"\n  Testing: {url[:80]}...")
    start = time.time()
    status, body = http_get(url, headers, timeout=15)
    elapsed = time.time() - start
    print(f"    Status: {status}, Time: {elapsed:.1f}s")
    print(f"    Response: {body[:500]}")

    return {"service": "RapidAPI-scraper1688", "works": status in (200, 401, 403), "needs_key": True}


def test_rapidapi_open_taobao():
    """
    RapidAPI 'open-taobao-1688' (by apichinagroup).
    Host: open-taobao-1688.p.rapidapi.com
    """
    print("\n" + "=" * 60)
    print("API 5: RapidAPI 'open-taobao-1688' (apichinagroup)")
    print("=" * 60)

    headers = {
        "x-rapidapi-host": "open-taobao-1688.p.rapidapi.com",
    }
    # Try common patterns
    for path in [
        f"/item_detail?num_iid={TEST_OFFER_IDS[0]}",
        f"/product/detail?id={TEST_OFFER_IDS[0]}",
        f"/item_get?num_iid={TEST_OFFER_IDS[0]}",
    ]:
        url = f"https://open-taobao-1688.p.rapidapi.com{path}"
        print(f"\n  Testing: {url[:80]}...")
        status, body = http_get(url, headers, timeout=15)
        print(f"    Status: {status}")
        print(f"    Response: {body[:300]}")
        if status in (200, 401, 403):
            break

    return {"service": "RapidAPI-open-taobao", "works": status in (200, 401, 403), "needs_key": True}


def test_idatariver():
    """
    iDataRiver — referenced in Alibaba-1688-API-Doc GitHub repo.
    """
    print("\n" + "=" * 60)
    print("API 6: iDataRiver (idatariver.com)")
    print("=" * 60)

    url = f"https://api.idatariver.com/1688/item?item_id={TEST_OFFER_IDS[0]}"
    print(f"\n  Testing: {url}")
    status, body = http_get(url, timeout=15)
    print(f"    Status: {status}")
    print(f"    Response: {body[:500]}")

    return {"service": "iDataRiver", "works": status in (200, 401, 403), "needs_key": True}


def test_1688_product_rapidapi():
    """
    RapidAPI '1688-product' (by solo-xwz).
    Host: 1688-product2.p.rapidapi.com
    """
    print("\n" + "=" * 60)
    print("API 7: RapidAPI '1688-product2' (solo-xwz)")
    print("=" * 60)

    headers = {
        "x-rapidapi-host": "1688-product2.p.rapidapi.com",
    }
    for path in [
        f"/product/detail?id={TEST_OFFER_IDS[0]}",
        f"/item_detail?num_iid={TEST_OFFER_IDS[0]}",
        f"/detail?offerId={TEST_OFFER_IDS[0]}",
    ]:
        url = f"https://1688-product2.p.rapidapi.com{path}"
        print(f"\n  Testing: {url[:80]}...")
        status, body = http_get(url, headers, timeout=15)
        print(f"    Status: {status}")
        print(f"    Response: {body[:300]}")
        if status in (200, 401, 403):
            break

    return {"service": "RapidAPI-1688-product2", "works": status in (200, 401, 403), "needs_key": True}


def main():
    print("=" * 60)
    print("1688.com THIRD-PARTY API TESTING")
    print("=" * 60)
    print(f"Testing with offer IDs: {TEST_OFFER_IDS[:3]}...")

    results = []
    results.append(test_tmapi())
    results.append(test_rapidapi_1688())
    results.append(test_rapidapi_1688_v2())
    results.append(test_rapidapi_scraper_1688())
    results.append(test_rapidapi_open_taobao())
    results.append(test_idatariver())
    results.append(test_1688_product_rapidapi())

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        status = "ENDPOINT EXISTS" if r["works"] else "ENDPOINT NOT FOUND/ERROR"
        print(f"  {r['service']}: {status}")

    # Save results
    with open("/home/dev-moss/cargo-ai-saas/spike/api_probe_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
