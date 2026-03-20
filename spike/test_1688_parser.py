"""
Spike test: Parse product data from 1688.com using Playwright.
Tests 20 real product URLs across 5 categories.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from playwright.async_api import async_playwright, Page, Browser


# 20 real 1688 product URLs across categories (from web search results)
TEST_URLS = [
    # Clothing / Shoes (одежда/обувь)
    {"url": "https://detail.1688.com/offer/790251400429.html", "category": "clothing"},
    {"url": "https://detail.1688.com/offer/822970193916.html", "category": "clothing"},
    {"url": "https://detail.1688.com/offer/733761829621.html", "category": "clothing"},
    {"url": "https://detail.1688.com/offer/984046619242.html", "category": "clothing"},
    # Electronics (электроника)
    {"url": "https://detail.1688.com/offer/888640675772.html", "category": "electronics"},
    {"url": "https://detail.1688.com/offer/752205332799.html", "category": "electronics"},
    {"url": "https://detail.1688.com/offer/647997819110.html", "category": "electronics"},
    {"url": "https://detail.1688.com/offer/708012905355.html", "category": "electronics"},
    # Home goods (товары для дома)
    {"url": "https://detail.1688.com/offer/44776366308.html", "category": "home"},
    {"url": "https://detail.1688.com/offer/583658240815.html", "category": "home"},
    {"url": "https://detail.1688.com/offer/606628051926.html", "category": "home"},
    {"url": "https://detail.1688.com/offer/617737573706.html", "category": "home"},
    # Toys / Accessories (игрушки/аксессуары)
    {"url": "https://detail.1688.com/offer/595045370736.html", "category": "toys"},
    {"url": "https://detail.1688.com/offer/577687060339.html", "category": "toys"},
    {"url": "https://detail.1688.com/offer/602480973397.html", "category": "toys"},
    {"url": "https://detail.1688.com/offer/656173618669.html", "category": "toys"},
    # Cosmetics / Other (косметика/прочее)
    {"url": "https://detail.1688.com/offer/520537149440.html", "category": "cosmetics"},
    {"url": "https://detail.1688.com/offer/534550904424.html", "category": "cosmetics"},
    {"url": "https://detail.1688.com/offer/703974508333.html", "category": "cosmetics"},
    {"url": "https://detail.1688.com/offer/558868806998.html", "category": "cosmetics"},
]

PROXY = "socks5://aggpoakv-rotate:6us5w6ptopgk@p.webshare.io:80"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


@dataclass
class ParseResult:
    url: str
    category: str
    title: Optional[str] = None
    price: Optional[str] = None
    weight: Optional[str] = None
    dimensions: Optional[str] = None
    image_url: Optional[str] = None
    error: Optional[str] = None
    load_time_sec: float = 0.0
    anti_bot_detected: bool = False
    redirect_detected: bool = False


async def extract_product_data(page: Page, url: str) -> dict:
    """Extract product data from a loaded 1688 product page."""
    data = {}

    # Title extraction — multiple selectors
    title_selectors = [
        "h1.title-text",
        ".title-text",
        "h1[class*='title']",
        ".module-pdp-title h1",
        ".offer-title h1",
        "[class*='DetailHeader'] h1",
        ".detail-title-text",
        "h1",
    ]
    for sel in title_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip()
                if text and len(text) > 2:
                    data["title"] = text
                    break
        except Exception:
            continue

    # Price extraction
    price_selectors = [
        ".price-text",
        "[class*='price'] span",
        ".offer-price .price",
        ".price-value",
        ".mod-detail-price .value",
        "[class*='Price'] span",
        ".sk-price-num",
    ]
    for sel in price_selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                text = (await el.inner_text()).strip()
                # Match Chinese Yuan prices like ¥12.50, 12.50, etc.
                price_match = re.search(r'[¥￥]?\s*(\d+\.?\d*)', text)
                if price_match:
                    data["price"] = f"¥{price_match.group(1)}"
                    break
            if "price" in data:
                break
        except Exception:
            continue

    # Also try extracting price from page content with JS
    if "price" not in data:
        try:
            price_js = await page.evaluate("""
                () => {
                    // Try to find price in common patterns
                    const allText = document.body.innerText;
                    const priceMatch = allText.match(/[¥￥]\s*(\d+\.?\d*)/);
                    return priceMatch ? priceMatch[0] : null;
                }
            """)
            if price_js:
                data["price"] = price_js
        except Exception:
            pass

    # Image extraction
    image_selectors = [
        ".detail-gallery-img img",
        ".vertical-img img",
        ".detail-main-img img",
        ".main-image img",
        "[class*='gallery'] img",
        ".offer-main-img img",
        ".thumb-list img",
    ]
    for sel in image_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                src = await el.get_attribute("src")
                if src and "http" in src:
                    data["image_url"] = src
                    break
        except Exception:
            continue

    # Weight and dimensions — usually in specs/params table
    # Look for 产品参数 (product parameters) section
    try:
        specs_data = await page.evaluate("""
            () => {
                const result = {weight: null, dimensions: null};

                // Strategy 1: Look for attribute tables
                const tables = document.querySelectorAll('table, [class*="attr"], [class*="param"], [class*="spec"]');
                for (const table of tables) {
                    const text = table.innerText;

                    // Weight patterns
                    const weightPatterns = [
                        /(?:重量|净重|毛重|单品重量|商品重量)[：:]\s*([0-9.]+\s*(?:kg|g|千克|克))/i,
                        /(?:weight)[：:]\s*([0-9.]+\s*(?:kg|g))/i,
                    ];
                    for (const pat of weightPatterns) {
                        const m = text.match(pat);
                        if (m) { result.weight = m[1]; break; }
                    }

                    // Dimensions patterns
                    const dimPatterns = [
                        /(?:尺寸|规格|包装尺寸|产品尺寸|外形尺寸)[：:]\s*([0-9.]+\s*[*×xX]\s*[0-9.]+(?:\s*[*×xX]\s*[0-9.]+)?(?:\s*(?:cm|mm|m))?)/i,
                        /(?:dimension|size)[：:]\s*([0-9.]+\s*[*×xX]\s*[0-9.]+(?:\s*[*×xX]\s*[0-9.]+)?(?:\s*(?:cm|mm|m))?)/i,
                    ];
                    for (const pat of dimPatterns) {
                        const m = text.match(pat);
                        if (m) { result.dimensions = m[1]; break; }
                    }
                }

                // Strategy 2: Look in entire page text
                if (!result.weight) {
                    const bodyText = document.body.innerText;
                    const wm = bodyText.match(/(?:重量|净重|毛重)[：:]*\s*(\d+\.?\d*\s*(?:kg|g|千克|克))/i);
                    if (wm) result.weight = wm[1];
                }
                if (!result.dimensions) {
                    const bodyText = document.body.innerText;
                    const dm = bodyText.match(/(?:尺寸|规格)[：:]*\s*(\d+\.?\d*\s*[*×xX]\s*\d+\.?\d*(?:\s*[*×xX]\s*\d+\.?\d*)?(?:\s*(?:cm|mm|m))?)/i);
                    if (dm) result.dimensions = dm[1];
                }

                return result;
            }
        """)
        if specs_data.get("weight"):
            data["weight"] = specs_data["weight"]
        if specs_data.get("dimensions"):
            data["dimensions"] = specs_data["dimensions"]
    except Exception:
        pass

    return data


async def check_anti_bot(page: Page) -> tuple[bool, bool]:
    """Check if the page shows anti-bot measures or redirect."""
    try:
        current_url = page.url
        content = await page.content()
        text = await page.inner_text("body") if await page.query_selector("body") else ""

        # Check for common anti-bot indicators
        anti_bot_keywords = [
            "验证", "captcha", "verify", "robot",
            "滑块", "slider", "安全验证",
            "请完成安全验证", "human verification",
        ]
        anti_bot = any(kw in text.lower() or kw in content.lower() for kw in anti_bot_keywords)

        # Check for redirect (login page, etc.)
        redirect = "login" in current_url or "passport" in current_url or "detail.1688.com" not in current_url

        return anti_bot, redirect
    except Exception:
        return False, False


async def parse_single_url(browser: Browser, url_info: dict, ua_index: int, use_proxy: bool = False) -> ParseResult:
    """Parse a single 1688 product URL."""
    url = url_info["url"]
    category = url_info["category"]
    result = ParseResult(url=url, category=category)

    ua = USER_AGENTS[ua_index % len(USER_AGENTS)]

    context_opts = {
        "user_agent": ua,
        "locale": "zh-CN",
        "viewport": {"width": 1920, "height": 1080},
        "extra_http_headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        },
    }

    if use_proxy:
        context_opts["proxy"] = {"server": PROXY}

    context = await browser.new_context(**context_opts)

    try:
        page = await context.new_page()
        start = time.time()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            result.error = f"Navigation timeout/error: {str(e)[:200]}"
            result.load_time_sec = time.time() - start
            return result

        # Wait a bit for JS to render
        await asyncio.sleep(3)

        # Try scrolling to trigger lazy loads
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(1)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)
        except Exception:
            pass

        result.load_time_sec = time.time() - start

        # Check anti-bot
        anti_bot, redirect = await check_anti_bot(page)
        result.anti_bot_detected = anti_bot
        result.redirect_detected = redirect

        if redirect:
            result.error = f"Redirected to: {page.url}"
            return result

        if anti_bot:
            result.error = "Anti-bot/captcha detected"
            # Still try to parse — sometimes partial data is available

        # Extract data
        data = await extract_product_data(page, url)
        result.title = data.get("title")
        result.price = data.get("price")
        result.weight = data.get("weight")
        result.dimensions = data.get("dimensions")
        result.image_url = data.get("image_url")

        if not result.title and not result.price:
            if not result.error:
                result.error = "No data extracted (page may be empty or blocked)"

    except Exception as e:
        result.error = f"Unexpected error: {str(e)[:200]}"
    finally:
        await context.close()

    return result


async def run_spike():
    """Run the full spike test."""
    print("=" * 70)
    print("1688.com PARSING SPIKE TEST")
    print("=" * 70)
    print(f"Testing {len(TEST_URLS)} URLs across 5 categories\n")

    results: list[ParseResult] = []

    async with async_playwright() as p:
        # First pass: without proxy
        print("[Phase 1] Testing WITHOUT proxy...")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        for i, url_info in enumerate(TEST_URLS):
            print(f"  [{i+1}/{len(TEST_URLS)}] {url_info['category']}: {url_info['url'][:60]}...")
            result = await parse_single_url(browser, url_info, i, use_proxy=False)
            results.append(result)

            status = []
            if result.title:
                status.append("title")
            if result.price:
                status.append(f"price={result.price}")
            if result.weight:
                status.append(f"weight={result.weight}")
            if result.dimensions:
                status.append(f"dims={result.dimensions}")
            if result.image_url:
                status.append("image")
            if result.anti_bot_detected:
                status.append("ANTI-BOT!")
            if result.redirect_detected:
                status.append("REDIRECT!")
            if result.error:
                status.append(f"ERR: {result.error[:80]}")

            print(f"    -> {', '.join(status) if status else 'EMPTY'} ({result.load_time_sec:.1f}s)")

            # Small delay between requests
            await asyncio.sleep(2)

        await browser.close()

        # Second pass: retry failed URLs with proxy
        failed_indices = [
            i for i, r in enumerate(results)
            if not r.title and not r.price
        ]

        if failed_indices:
            print(f"\n[Phase 2] Retrying {len(failed_indices)} failed URLs WITH proxy...")
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )

                for idx in failed_indices:
                    url_info = TEST_URLS[idx]
                    print(f"  [retry] {url_info['category']}: {url_info['url'][:60]}...")
                    result = await parse_single_url(browser, url_info, idx, use_proxy=True)

                    status = []
                    if result.title:
                        status.append("title")
                    if result.price:
                        status.append(f"price={result.price}")
                    if result.weight:
                        status.append(f"weight={result.weight}")
                    if result.dimensions:
                        status.append(f"dims={result.dimensions}")
                    if result.image_url:
                        status.append("image")
                    if result.error:
                        status.append(f"ERR: {result.error[:80]}")

                    print(f"    -> {', '.join(status) if status else 'STILL EMPTY'}")

                    # Update result if proxy version got more data
                    if result.title or result.price:
                        results[idx] = result
                        results[idx].error = (results[idx].error or "") + " [proxy retry]"

                    await asyncio.sleep(2)

                await browser.close()
            except Exception as e:
                print(f"  Proxy phase failed: {e}")

    # Print summary
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    total = len(results)
    has_title = sum(1 for r in results if r.title)
    has_price = sum(1 for r in results if r.price)
    has_weight = sum(1 for r in results if r.weight)
    has_dimensions = sum(1 for r in results if r.dimensions)
    has_image = sum(1 for r in results if r.image_url)
    has_any = sum(1 for r in results if r.title or r.price)
    anti_bot_count = sum(1 for r in results if r.anti_bot_detected)
    redirect_count = sum(1 for r in results if r.redirect_detected)

    print(f"Total URLs tested: {total}")
    print(f"Parsed successfully (title OR price): {has_any}/{total} ({has_any/total*100:.0f}%)")
    print(f"  Title extracted:      {has_title}/{total} ({has_title/total*100:.0f}%)")
    print(f"  Price extracted:      {has_price}/{total} ({has_price/total*100:.0f}%)")
    print(f"  Weight extracted:     {has_weight}/{total} ({has_weight/total*100:.0f}%)")
    print(f"  Dimensions extracted: {has_dimensions}/{total} ({has_dimensions/total*100:.0f}%)")
    print(f"  Image URL extracted:  {has_image}/{total} ({has_image/total*100:.0f}%)")
    print(f"Anti-bot detected: {anti_bot_count}")
    print(f"Redirects detected: {redirect_count}")

    avg_time = sum(r.load_time_sec for r in results) / total
    print(f"Avg load time: {avg_time:.1f}s")

    # By category
    print("\nBy category:")
    for cat in ["clothing", "electronics", "home", "toys", "cosmetics"]:
        cat_results = [r for r in results if r.category == cat]
        cat_ok = sum(1 for r in cat_results if r.title or r.price)
        print(f"  {cat}: {cat_ok}/{len(cat_results)}")

    # Go/No-go
    pct = has_any / total * 100
    print(f"\n{'='*70}")
    if pct > 70:
        print(f"GO/NO-GO: GO ({pct:.0f}% > 70% threshold)")
    elif pct >= 50:
        print(f"GO/NO-GO: CONDITIONAL GO ({pct:.0f}% — 50-70% range, need fallback)")
    else:
        print(f"GO/NO-GO: NO-GO ({pct:.0f}% < 50% threshold)")
    print("=" * 70)

    # Save raw results as JSON
    results_json = [asdict(r) for r in results]
    with open("/home/dev-moss/cargo-ai-saas/spike/results.json", "w") as f:
        json.dump(results_json, f, indent=2, ensure_ascii=False)
    print(f"\nRaw results saved to spike/results.json")

    return results


if __name__ == "__main__":
    asyncio.run(run_spike())
