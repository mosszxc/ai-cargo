"""
Spike test #3: Alternative approaches to get 1688 product data.
1. Alibaba.com (English version — less anti-bot?)
2. 1688 open platform / APIs
3. Third-party scraping services
4. Data extraction from 1688 search page (not detail)
"""

import asyncio
import json
import re
import time

from playwright.async_api import async_playwright


async def test_alibaba_com():
    """Test if alibaba.com (international) is parseable."""
    print("\n" + "=" * 60)
    print("STRATEGY: Alibaba.com (international version)")
    print("=" * 60)
    print("  Note: Different from 1688.com — this is the English B2B platform")
    print("  May have less aggressive anti-bot since it targets international buyers")

    # Real Alibaba.com product URLs
    test_urls = [
        "https://www.alibaba.com/product-detail/Summer-Ladies-Dress-2024-V-Neck_1601254826826.html",
        "https://www.alibaba.com/product-detail/TWS-Wireless-Bluetooth-Headphones-Noise-Cancelling_1601183684019.html",
        "https://www.alibaba.com/product-detail/Kids-Toy-Plush-Animal-Stuffed-Toy_1600100886547.html",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1920, "height": 1080},
        )

        for url in test_urls:
            page = await context.new_page()
            try:
                print(f"\n  Testing: {url[:70]}...")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                status = resp.status if resp else "N/A"
                current = page.url
                print(f"    Status: {status}")
                print(f"    Final URL: {current[:80]}")

                if "login" in current or "captcha" in current:
                    print("    -> BLOCKED/REDIRECT")
                    continue

                # Try title
                title = await page.title()
                print(f"    Title: {title[:80]}")

                # Try product name
                for sel in ["h1", ".module-product-title h1", ".product-title", "[class*='title'] h1"]:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text and len(text) > 5:
                            print(f"    Product name: {text[:80]}")
                            break

                # Try price
                body = await page.inner_text("body")
                price_match = re.search(r'\$\s*(\d+\.?\d*)', body)
                if price_match:
                    print(f"    Price: ${price_match.group(1)}")

                print(f"    Body length: {len(body)} chars")

            except Exception as e:
                print(f"    -> ERROR: {str(e)[:150]}")
            finally:
                await page.close()
                await asyncio.sleep(2)

        await browser.close()


async def test_1688_search_page():
    """Test if 1688 search results page is accessible (not individual products)."""
    print("\n" + "=" * 60)
    print("STRATEGY: 1688 search results page")
    print("=" * 60)

    search_urls = [
        "https://s.1688.com/selloffer/offer_search.htm?keywords=女装+连衣裙",
        "https://s.1688.com/selloffer/offer_search.htm?keywords=毛绒玩具",
        "https://s.1688.com/selloffer/offer_search.htm?keywords=手机壳",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
        )

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")

        for url in search_urls:
            page = await context.new_page()
            try:
                print(f"\n  Testing: {url[:70]}...")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                status = resp.status if resp else "N/A"
                current = page.url
                print(f"    Status: {status}")
                print(f"    Final URL: {current[:80]}")

                if "login" in current:
                    print("    -> REDIRECTED TO LOGIN")
                    continue

                title = await page.title()
                body = await page.inner_text("body")
                print(f"    Title: {title[:60]}")
                print(f"    Body: {len(body)} chars")

                if len(body) > 100:
                    # Try to find product cards
                    cards = await page.query_selector_all("[class*='offer']")
                    print(f"    Offer elements found: {len(cards)}")
                    print(f"    First 300 chars: {body[:300]}")

            except Exception as e:
                print(f"    -> ERROR: {str(e)[:150]}")
            finally:
                await page.close()
                await asyncio.sleep(2)

        await browser.close()


async def test_1688_open_api():
    """Check if 1688 has any open/public API we can use."""
    print("\n" + "=" * 60)
    print("STRATEGY: 1688 Open API / public endpoints")
    print("=" * 60)

    # Known 1688 API patterns
    api_urls = [
        # Cross-border API
        "https://open.1688.com/api/detail/get.htm?offerId=790251400429",
        # New detail API
        "https://gw.open.1688.com/openapi/param2/1/com.alibaba.product/alibaba.product.get/0?offerId=790251400429",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        for url in api_urls:
            try:
                print(f"\n  Testing: {url[:80]}...")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else "N/A"
                content = await page.content()
                print(f"    Status: {status}")
                print(f"    Content: {content[:300]}")
            except Exception as e:
                print(f"    -> ERROR: {str(e)[:150]}")

        await page.close()
        await browser.close()


async def main():
    print("1688.com ALTERNATIVE STRATEGIES")
    print("=" * 60)

    await test_alibaba_com()
    await test_1688_search_page()
    await test_1688_open_api()

    print("\n" + "=" * 60)
    print("ALTERNATIVE STRATEGIES COMPLETE")
    print("=" * 60)
    print("\nConclusions will be written in spike-results.md")


if __name__ == "__main__":
    asyncio.run(main())
