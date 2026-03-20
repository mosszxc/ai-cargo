#!/usr/bin/env python3
"""
1688.com product parser — Scrapling + LLM extraction.

Strategy:
  1. Check file-based cache (24h TTL)
  2. Try Scrapling StealthyFetcher → extract ALL text + scripts → Claude Haiku extracts data
  3. If fails → Return error suggesting manual description

Usage:
  python parser_1688.py <url>

Reads ANTHROPIC_API_KEY (for Haiku) from environment or .env file.
Returns JSON to stdout.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _load_env():
    """Load .env file if needed keys not already set."""
    needed = ("ANTHROPIC_API_KEY",)
    if all(os.environ.get(k) for k in needed):
        return
    for env_path in [
        Path(__file__).parent.parent.parent / ".env",
        Path.home() / "cargo-ai-saas" / ".env",
    ]:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
            break


def extract_offer_id(url_or_text: str) -> Optional[str]:
    """Extract offer ID from any 1688 link format.

    Supports:
    - detail.1688.com/offer/123456.html (desktop)
    - m.1688.com/offer?id=123456.html (mobile)
    - qr.1688.com/s/XXXX (short link - needs resolve)
    - offerId=123456 (in app share text)
    - offer/123456 (bare path)
    - Just digits 9+ chars (raw offer ID)
    """
    # Standard: offer/123456.html
    match = re.search(r"offer/(\d+)\.html", url_or_text)
    if match:
        return match.group(1)

    # Mobile/app: id=123456.html or offerId=123456
    match = re.search(r"(?:id|offerId)=(\d{8,})", url_or_text)
    if match:
        return match.group(1)

    # Short link: resolve qr.1688.com
    if "qr.1688.com" in url_or_text:
        resolved = _resolve_short_url(url_or_text)
        if resolved:
            return extract_offer_id(resolved)

    # Raw offer ID (9+ digits)
    match = re.search(r"\b(\d{9,})\b", url_or_text)
    if match:
        return match.group(1)

    return None


def _resolve_short_url(text: str) -> Optional[str]:
    """Resolve qr.1688.com short URLs by fetching and extracting offer ID."""
    match = re.search(r"https?://qr\.1688\.com/s/\w+", text)
    if not match:
        return None
    try:
        req = urllib.request.Request(match.group(0), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(2000).decode("utf-8", errors="ignore")
            # Short URL page contains offerId=XXXX or offer?id=XXXX
            id_match = re.search(r"(?:offerId|offer\?id)=(\d{8,})", body)
            if id_match:
                return f"https://detail.1688.com/offer/{id_match.group(1)}.html"
    except Exception:
        pass
    return None


def is_1688_url(text: str) -> Optional[str]:
    """Check if text contains any 1688 reference. Returns URL or None.

    Supports:
    - detail.1688.com URLs
    - m.1688.com URLs
    - qr.1688.com short links
    - 1688 app share text (复制￥...￥)
    """
    # Standard URL
    match = re.search(r"https?://[^\s]*1688\.com[^\s]*", text)
    if match:
        return match.group(0)

    # App share format: contains 1688 reference + offer ID
    if "1688" in text and re.search(r"\d{9,}", text):
        return text  # Return full text, extract_offer_id will find the ID

    return None


def _make_result(offer_id: str = "") -> dict:
    """Create an empty result template."""
    return {
        "success": True,
        "offer_id": offer_id,
        "title": None,
        "price_cny": None,
        "weight_kg": None,
        "dimensions": None,
        "image_url": None,
        "min_order": None,
        "category": None,
        "source": None,  # "scrapling_llm"
    }


# ---------------------------------------------------------------------------
# File-based cache (24h TTL)
# ---------------------------------------------------------------------------

class FileCache:
    """Simple file-based JSON cache with TTL."""

    def __init__(self, cache_dir: Path = CACHE_DIR, ttl: int = CACHE_TTL_SECONDS):
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, offer_id: str) -> Path:
        return self.cache_dir / f"{offer_id}.json"

    def get(self, offer_id: str) -> Optional[dict]:
        """Get cached result if fresh. Returns None if missing or expired."""
        path = self._path(offer_id)
        if not path.exists():
            return None
        try:
            stat = path.stat()
            age = time.time() - stat.st_mtime
            if age > self.ttl:
                path.unlink(missing_ok=True)
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_cached"] = True
            return data
        except Exception:
            return None

    def put(self, offer_id: str, data: dict):
        """Store result in cache."""
        try:
            path = self._path(offer_id)
            # Remove internal keys before caching
            clean = {k: v for k, v in data.items() if not k.startswith("_")}
            path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[cache] Failed to write {offer_id}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Scrapling + LLM Parser (primary — free, fast)
# ---------------------------------------------------------------------------

class ScraplingLLMParser:
    """
    Primary parser: Scrapling StealthyFetcher loads page, extracts all text + script data,
    then Claude Haiku extracts structured product information.

    This avoids brittle CSS selectors — LLM handles the messy HTML/text extraction.
    """

    # Anti-bot / login redirect indicators
    ANTI_BOT_KEYWORDS = [
        "验证", "captcha", "verify", "robot",
        "滑块", "slider", "安全验证",
        "请完成安全验证", "human verification",
    ]

    EXTRACTION_PROMPT = """Extract product information from this 1688.com product page.

Visible text from the page:
---
{visible_text}
---

Script/JSON data found on the page:
---
{script_data}
---

Return ONLY a JSON object (no markdown, no explanation) with this exact structure:
{{
  "title": "product name in original language (NOT the store/company name — company names end with 有限公司, 商行, 工厂, etc.)",
  "price_cny": {{
    "min": lowest_price_as_number,
    "max": highest_price_as_number,
    "variants": [
      {{"name": "variant_name", "price": price_as_number}},
      ...
    ]
  }},
  "weight_kg": weight_per_unit_in_kg_or_null,
  "dimensions_cm": {{"l": length, "w": width, "h": height}} or null,
  "min_order": minimum_order_quantity_as_number_or_null,
  "image_url": "main product image URL or null",
  "category": "product category guess in Russian"
}}

Important rules:
- title = the PRODUCT name, NOT the store/seller/company name
- price: look for priceRange, skuMap, offerPrice, or price text. Extract ALL variant prices if available.
- If only one price, set min=max and variants=[]
- weight: may be in specs (产品参数, 规格, 克重), description text, or script data. Convert grams to kg (divide by 1000).
- dimensions: convert mm to cm if needed
- image_url: look for main product image (alicdn.com URLs preferred)
- If a field is not found, set it to null
- Return ONLY valid JSON, nothing else"""

    def parse(self, url: str, offer_id: str, debug_dir: Optional[Path] = None) -> dict:
        """
        Parse a 1688 product page: load with Scrapling, extract with Haiku.

        Args:
            url: 1688 product URL
            offer_id: extracted offer ID
            debug_dir: if set, save raw text to {debug_dir}/{offer_id}_text.txt
        """
        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError:
            raise RuntimeError("scrapling not installed: pip install scrapling && python3 -m camoufox fetch")

        _load_env()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — needed for LLM extraction")

        # --- Step 1: Load page with Scrapling ---
        def page_action(page):
            """Scroll to trigger lazy loading."""
            import time as _time
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                _time.sleep(1)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                _time.sleep(1)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                _time.sleep(1)
            except Exception:
                pass

        response = StealthyFetcher.fetch(
            url,
            headless=True,
            locale="zh-CN",
            network_idle=True,
            timeout=30000,
            wait=3000,
            page_action=page_action,
            google_search=False,
            disable_resources=False,
            extra_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

        if response is None:
            raise RuntimeError("StealthyFetcher returned None")

        # Check for anti-bot or redirect
        page_url = str(response.url) if response.url else ""
        if "login" in page_url or "passport" in page_url:
            raise RuntimeError(f"Redirected to login: {page_url}")
        if "detail.1688.com" not in page_url and "offer" not in page_url:
            raise RuntimeError(f"Unexpected redirect to: {page_url}")

        # --- Step 2: Extract ALL content from page ---
        visible_text = self._extract_visible_text(response)
        script_data = self._extract_script_data(response)
        image_url = self._extract_main_image(response)

        # Anti-bot check on visible text
        text_lower = visible_text.lower() if visible_text else ""
        for kw in self.ANTI_BOT_KEYWORDS:
            if kw in text_lower:
                raise RuntimeError(f"Anti-bot detected: '{kw}' found on page")

        if not visible_text and not script_data:
            raise RuntimeError("No content extracted from page (empty or blocked)")

        # Save debug output if requested
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"{offer_id}_text.txt"
            debug_content = f"=== URL: {url} ===\n\n"
            debug_content += f"=== VISIBLE TEXT ({len(visible_text)} chars) ===\n{visible_text[:10000]}\n\n"
            debug_content += f"=== SCRIPT DATA ({len(script_data)} chars) ===\n{script_data[:20000]}\n"
            debug_path.write_text(debug_content, encoding="utf-8")

        # --- Step 3: Send to Claude Haiku for extraction ---
        # Truncate to fit context window (Haiku has 200k but let's be reasonable)
        visible_truncated = visible_text[:8000] if visible_text else "(no visible text)"
        script_truncated = script_data[:15000] if script_data else "(no script data)"

        prompt = self.EXTRACTION_PROMPT.format(
            visible_text=visible_truncated,
            script_data=script_truncated,
        )

        extracted = self._call_haiku(api_key, prompt)

        # --- Step 4: Build result ---
        result = _make_result(offer_id)
        result["source"] = "scrapling_llm"
        # Use image from HTML (more reliable than LLM extraction)
        if image_url:
            result["image_url"] = image_url

        if extracted:
            result["title"] = extracted.get("title")

            # Price — handle the new structured format
            price_data = extracted.get("price_cny")
            if isinstance(price_data, dict):
                min_price = price_data.get("min")
                max_price = price_data.get("max")
                if min_price is not None:
                    result["price_cny"] = {
                        "min": float(min_price) if min_price else None,
                        "max": float(max_price) if max_price else None,
                        "variants": price_data.get("variants", []),
                    }
            elif isinstance(price_data, (int, float)):
                result["price_cny"] = {"min": float(price_data), "max": float(price_data), "variants": []}

            # Weight
            weight = extracted.get("weight_kg")
            if weight is not None:
                try:
                    result["weight_kg"] = float(weight)
                except (ValueError, TypeError):
                    pass

            # Dimensions
            dims = extracted.get("dimensions_cm")
            if isinstance(dims, dict) and any(dims.get(k) for k in ("l", "w", "h")):
                result["dimensions"] = dims

            # Image
            result["image_url"] = extracted.get("image_url")

            # Min order
            min_order = extracted.get("min_order")
            if min_order is not None:
                try:
                    result["min_order"] = int(min_order)
                except (ValueError, TypeError):
                    pass

            # Category
            result["category"] = extracted.get("category")

        # Validate we got something useful
        if not result["title"] and not result["price_cny"]:
            raise RuntimeError("LLM extraction returned no useful data (no title, no price)")

        return result

    def _extract_main_image(self, response) -> Optional[str]:
        """Extract main product image URL directly from HTML (not via LLM)."""
        try:
            # Try common 1688 product image selectors
            selectors = [
                "img.detail-gallery-img",
                "img[data-role='main-image']",
                ".detail-gallery img",
                ".img-wrapper img",
                ".main-image img",
                "img.J_ImageMain",
            ]
            for sel in selectors:
                imgs = response.css(sel)
                if imgs:
                    img = imgs[0] if hasattr(imgs, '__getitem__') else imgs
                    src = img.attrib.get("src") or img.attrib.get("data-src") or img.attrib.get("data-lazy-src")
                    if src:
                        if src.startswith("//"):
                            src = "https:" + src
                        return src

            # Fallback: find any alicdn.com image (1688's CDN)
            all_imgs = response.css("img")
            if all_imgs:
                for img in all_imgs[:20]:
                    src = img.attrib.get("src") or img.attrib.get("data-src") or ""
                    if "alicdn.com" in src and ("ibank" in src or "cbu01" in src):
                        if src.startswith("//"):
                            src = "https:" + src
                        # Skip tiny icons (usually < 50px wide)
                        if "_50x50" not in src and "_32x32" not in src:
                            return src

            # Last resort: find alicdn URLs in script data
            scripts = response.css("script")
            if scripts:
                for script in scripts[:30]:
                    text = script.text if hasattr(script, 'text') else ""
                    if text:
                        match = re.search(r'(https?://cbu01\.alicdn\.com/img/[^"\'\\]+\.(?:jpg|png|webp))', text)
                        if match:
                            return match.group(1)
        except Exception:
            pass
        return None

    def _extract_visible_text(self, response) -> str:
        """Extract all visible text from the page."""
        try:
            # Try get_all_text first (scrapling method)
            if hasattr(response, 'get_all_text'):
                text = response.get_all_text()
                if text and len(text.strip()) > 50:
                    return text.strip()
        except Exception:
            pass

        # Fallback: get text from body
        try:
            body = response.css("body")
            if body:
                el = body[0] if isinstance(body, list) else body
                text = el.text if hasattr(el, 'text') else ""
                if text and len(text.strip()) > 50:
                    return text.strip()
        except Exception:
            pass

        # Last resort: title + any text we can get
        try:
            parts = []
            title_el = response.css("title")
            if title_el:
                el = title_el[0] if isinstance(title_el, list) else title_el
                parts.append(el.text.strip() if hasattr(el, 'text') else "")
            return "\n".join(parts)
        except Exception:
            return ""

    def _extract_script_data(self, response) -> str:
        """Extract relevant JSON/data from <script> tags.

        1688 stores product data in script tags as JSON objects.
        Key patterns: window.__INIT_DATA__, window.detailData, skuProps, skuMap, priceRange, offerPrice
        """
        script_contents = []
        interesting_patterns = [
            "skuProps", "skuMap", "skuInfoMap", "priceRange", "offerPrice",
            "window.__INIT_DATA__", "window.detailData", "detailData",
            "iDetailData", "offer", "unitWeight", "productWeight",
            "globalData", "tradePrice", "priceModel", "skuModel",
        ]

        try:
            scripts = response.css("script")
            items = scripts if isinstance(scripts, list) else [scripts] if scripts else []
            for el in items:
                try:
                    text = el.text if hasattr(el, 'text') else str(el)
                    if not text or len(text.strip()) < 20:
                        continue
                    text = text.strip()

                    # Check if this script contains interesting data
                    has_interesting = any(pat in text for pat in interesting_patterns)
                    if has_interesting:
                        script_contents.append(text)
                    # Also try to find inline JSON objects with product data
                    elif "price" in text.lower() and ("sku" in text.lower() or "offer" in text.lower()):
                        script_contents.append(text)
                except Exception:
                    continue
        except Exception:
            pass

        return "\n---\n".join(script_contents)

    def _call_haiku(self, api_key: str, prompt: str) -> Optional[dict]:
        """Call Claude Haiku to extract product data. Returns parsed JSON dict or None."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Clean up response — remove markdown code blocks if present
            if text.startswith("```"):
                # Remove ```json and ``` wrapper
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
                text = text.strip()

            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[parser_1688] Haiku returned invalid JSON: {e}", file=sys.stderr)
            print(f"[parser_1688] Raw response: {text[:500]}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"[parser_1688] Haiku call failed: {e}", file=sys.stderr)
            return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Parser1688:
    """
    Orchestrator: cache -> Scrapling+LLM -> error.

    Usage:
        parser = Parser1688()
        result = parser.parse("https://detail.1688.com/offer/790251400429.html")
    """

    def __init__(
        self,
        enable_scrapling: bool = True,
        enable_cache: bool = True,
        debug: bool = False,
    ):
        self.enable_scrapling = enable_scrapling
        self.enable_cache = enable_cache
        self.debug = debug
        self._scrapling = ScraplingLLMParser() if enable_scrapling else None
        self._cache = FileCache() if enable_cache else None
        self._debug_dir = Path(__file__).parent.parent.parent / "spike" / "debug" if debug else None

    def parse(self, url: str) -> dict:
        """
        Parse a 1688 product URL.

        Strategy:
          1. Check cache (24h TTL)
          2. Try Scrapling + Haiku LLM (free, ~15-45 sec)
          3. Cache successful result
          4. If all fail -> return error

        Returns:
            {
                "success": True/False,
                "title": str,
                "price_cny": {"min": float, "max": float, "variants": [...]},
                "weight_kg": float|None,
                "dimensions": dict|None,
                "image_url": str|None,
                "min_order": int|None,
                "category": str|None,
                "offer_id": str,
                "source": "scrapling_llm"|None,
                "error": str  (only if success=False)
            }
        """
        offer_id = extract_offer_id(url)
        if not offer_id:
            return {"success": False, "error": f"Cannot extract offer ID from URL: {url}"}

        # 1. Check cache
        if self._cache:
            cached = self._cache.get(offer_id)
            if cached:
                cached["source"] = (cached.get("source") or "unknown") + "_cached"
                return cached

        errors = []

        # 2. Try Scrapling + LLM (free)
        if self._scrapling:
            try:
                t0 = time.time()
                result = self._scrapling.parse(url, offer_id, debug_dir=self._debug_dir)
                result["success"] = True
                result["_elapsed_sec"] = round(time.time() - t0, 1)
                # Cache successful result
                if self._cache:
                    self._cache.put(offer_id, result)
                return result
            except Exception as e:
                err_msg = f"Scrapling+LLM: {e}"
                errors.append(err_msg)
                print(f"[parser_1688] {err_msg}", file=sys.stderr)

        # 3. Failed
        return {
            "success": False,
            "error": "Не удалось загрузить данные с 1688. Опишите товар текстом — я рассчитаю вручную.\n"
                     f"Details: {'; '.join(errors)}",
        }


# ---------------------------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------------------------

def parse_1688_url(url: str) -> dict:
    """
    Parse a 1688 product URL. Backward-compatible entry point.

    Tries Scrapling+LLM (free, ~15-45s).
    """
    parser = Parser1688()
    return parser.parse(url)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: parser_1688.py <1688_url>", file=sys.stderr)
        print("Example: parser_1688.py https://detail.1688.com/offer/790251400429.html", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    debug = "--debug" in sys.argv

    parser = Parser1688(debug=debug)
    result = parser.parse(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
