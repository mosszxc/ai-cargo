"""
Spike test #2: Try stealth approaches to bypass 1688 anti-bot.
Strategies:
1. Stealth mode (hide webdriver detection)
2. Add cookies to simulate logged-in state
3. Use mobile user agent (m.1688.com)
4. Try offer API endpoint directly
"""

import asyncio
import json
import re
import time

from playwright.async_api import async_playwright


# Subset of URLs for quick testing
TEST_URLS = [
    "https://detail.1688.com/offer/790251400429.html",
    "https://detail.1688.com/offer/888640675772.html",
    "https://detail.1688.com/offer/595045370736.html",
    "https://detail.1688.com/offer/703974508333.html",
    "https://detail.1688.com/offer/656173618669.html",
]


async def test_stealth_browser():
    """Strategy 1: Stealth mode — hide automation indicators."""
    print("\n" + "=" * 60)
    print("STRATEGY 1: Stealth mode (hide webdriver)")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )

        # Inject stealth JS before page loads
        await context.add_init_script("""
            // Override webdriver detection
            Object.defineProperty(navigator, 'webdriver', { get: () => false });

            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });

            // Chrome runtime
            window.chrome = { runtime: {} };

            // Permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

        for url in TEST_URLS[:3]:
            page = await context.new_page()
            try:
                print(f"\n  Testing: {url}")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                current = page.url
                status = resp.status if resp else "N/A"
                print(f"    Status: {status}, Final URL: {current[:80]}")

                if "login" in current or "passport" in current:
                    print("    -> REDIRECTED TO LOGIN")
                else:
                    # Try to get title
                    title = await page.title()
                    print(f"    -> Page title: {title[:80]}")

                    # Check body content
                    body = await page.inner_text("body")
                    has_captcha = any(w in body for w in ["验证", "captcha", "滑块"])
                    print(f"    -> Captcha detected: {has_captcha}")
                    print(f"    -> Body length: {len(body)} chars")
                    if len(body) > 100:
                        print(f"    -> First 200 chars: {body[:200]}")

            except Exception as e:
                print(f"    -> ERROR: {str(e)[:150]}")
            finally:
                await page.close()
                await asyncio.sleep(2)

        await browser.close()


async def test_mobile_version():
    """Strategy 2: Mobile version (m.1688.com) — often less protected."""
    print("\n" + "=" * 60)
    print("STRATEGY 2: Mobile version (m.1688.com)")
    print("=" * 60)

    mobile_urls = [
        url.replace("detail.1688.com/offer/", "m.1688.com/offer/")
        for url in TEST_URLS[:3]
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            locale="zh-CN",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
        )

        for url in mobile_urls:
            page = await context.new_page()
            try:
                print(f"\n  Testing: {url}")
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                current = page.url
                status = resp.status if resp else "N/A"
                print(f"    Status: {status}, Final URL: {current[:80]}")

                if "login" in current:
                    print("    -> REDIRECTED TO LOGIN")
                else:
                    title = await page.title()
                    body = await page.inner_text("body")
                    print(f"    -> Title: {title[:80]}")
                    print(f"    -> Body length: {len(body)} chars")
                    if len(body) > 50:
                        print(f"    -> First 300 chars: {body[:300]}")

            except Exception as e:
                print(f"    -> ERROR: {str(e)[:150]}")
            finally:
                await page.close()
                await asyncio.sleep(2)

        await browser.close()


async def test_api_endpoint():
    """Strategy 3: Try internal API endpoints that 1688 uses for data."""
    print("\n" + "=" * 60)
    print("STRATEGY 3: Internal API endpoints")
    print("=" * 60)

    # 1688 uses these internal APIs for product data
    # The offer ID is the number from the URL
    offer_ids = [url.split("/offer/")[1].replace(".html", "") for url in TEST_URLS[:5]]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )

        # Try known API patterns
        api_patterns = [
            "https://detail.1688.com/offer/{id}.html",
            "https://h5api.m.1688.com/h5/mtop.alibaba.trade.offer.detail/1.0/?offerId={id}",
            "https://laputa.1688.com/company/offerDetail.htm?offerId={id}",
        ]

        page = await context.new_page()

        for oid in offer_ids[:3]:
            for pattern in api_patterns:
                url = pattern.format(id=oid)
                try:
                    print(f"\n  Testing API: {url[:80]}...")
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    status = resp.status if resp else "N/A"
                    current = page.url

                    if "login" in current:
                        print(f"    -> Status {status}, REDIRECTED TO LOGIN")
                        continue

                    content = await page.content()
                    body_text = await page.inner_text("body") if await page.query_selector("body") else ""
                    print(f"    -> Status {status}, body={len(body_text)} chars")

                    # Check if we got JSON
                    if content.strip().startswith("{") or content.strip().startswith("["):
                        print(f"    -> GOT JSON! First 200 chars: {content[:200]}")

                    if len(body_text) > 50:
                        print(f"    -> Content: {body_text[:200]}")

                except Exception as e:
                    print(f"    -> ERROR: {str(e)[:100]}")

                await asyncio.sleep(1)

        await page.close()
        await browser.close()


async def test_google_cache():
    """Strategy 4: Try Google cached versions of pages."""
    print("\n" + "=" * 60)
    print("STRATEGY 4: Search engine cached versions")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
        )
        page = await context.new_page()

        for url in TEST_URLS[:2]:
            # Try webcache
            cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
            try:
                print(f"\n  Testing cache: {cache_url[:80]}...")
                resp = await page.goto(cache_url, wait_until="domcontentloaded", timeout=15000)
                status = resp.status if resp else "N/A"
                body = await page.inner_text("body") if await page.query_selector("body") else ""
                print(f"    -> Status {status}, body={len(body)} chars")
                if len(body) > 50:
                    print(f"    -> Content: {body[:300]}")
            except Exception as e:
                print(f"    -> ERROR: {str(e)[:100]}")

            await asyncio.sleep(1)

        await page.close()
        await browser.close()


async def test_headful_with_delay():
    """Strategy 5: Longer delays + human-like behavior."""
    print("\n" + "=" * 60)
    print("STRATEGY 5: Human-like behavior with delays")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1920, "height": 1080},
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # First visit 1688 homepage to get cookies
        try:
            print("\n  Step 1: Visit 1688.com homepage first...")
            await page.goto("https://www.1688.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            cookies = await context.cookies()
            print(f"    -> Got {len(cookies)} cookies from homepage")

            title = await page.title()
            print(f"    -> Homepage title: {title[:60]}")

            # Now try a product page
            print("\n  Step 2: Navigate to product page...")
            await page.goto(TEST_URLS[0], wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            current = page.url
            print(f"    -> Final URL: {current[:80]}")

            if "login" in current:
                print("    -> STILL REDIRECTED TO LOGIN even with cookies")
            else:
                body = await page.inner_text("body")
                print(f"    -> Body length: {len(body)}")
                if len(body) > 50:
                    print(f"    -> Content: {body[:300]}")

        except Exception as e:
            print(f"    -> ERROR: {str(e)[:150]}")

        await browser.close()


async def main():
    print("1688.com ANTI-BOT BYPASS STRATEGIES")
    print("=" * 60)

    await test_stealth_browser()
    await test_mobile_version()
    await test_api_endpoint()
    await test_google_cache()
    await test_headful_with_delay()

    print("\n" + "=" * 60)
    print("ALL STRATEGIES TESTED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
