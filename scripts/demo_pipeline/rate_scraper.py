#!/usr/bin/env python3
"""
rate_scraper.py — Scrape cargo company rates from websites and Telegram channels.

Fetches page content, then uses Anthropic Haiku to extract structured rate data.

Usage:
    python -m scripts.demo_pipeline.rate_scraper <url> [--output rates_raw.json]
"""

import json
import os
import re
import sys
from typing import Optional
from urllib.parse import urlparse

import anthropic

# Try playwright import; fall back gracefully
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


EXTRACTION_PROMPT = """\
Ты — эксперт по логистике карго из Китая. Проанализируй текст с сайта/канала карго-компании и извлеки все ставки доставки.

Верни JSON строго в таком формате:
{
  "company_name": "название компании",
  "routes": [
    {
      "origin": "город отправки (по-русски)",
      "destination": "город назначения (по-русски)",
      "transports": [
        {
          "type": "auto|rail|air",
          "rate": число ($/кг),
          "rate_unit": "kg" или "m3",
          "days_min": число,
          "days_max": число,
          "density_brackets": [
            {"min_density": 0, "max_density": 99, "rate": число, "rate_unit": "m3"},
            {"min_density": 100, "max_density": 199, "rate": число, "rate_unit": "kg"},
            {"min_density": 200, "max_density": 9999, "rate": число, "rate_unit": "kg"}
          ]
        }
      ]
    }
  ],
  "min_weight_kg": число или null,
  "currency": "usd" или "rub" или "cny",
  "services": {
    "insurance_pct": число или null,
    "crating_pct": число или null
  },
  "notes": "любые важные замечания"
}

Правила:
- Если ставки указаны в $/кг — это rate_unit: "kg"
- Если ставки указаны в $/м³ — это rate_unit: "m3"
- Если есть разбивка по плотности — заполни density_brackets
- Если нет разбивки по плотности — оставь density_brackets пустым, укажи rate
- "auto" = авто/фура, "rail" = ЖД/поезд, "air" = авиа/самолёт
- Города: используй русские названия (Гуанчжоу, Иу, Пекин, Москва, Владивосток и т.д.)
- Если валюта не указана явно, предполагай USD
- Если сроки не указаны, поставь разумные значения: авто 18-25, жд 20-30, авиа 5-10
- Верни ТОЛЬКО JSON, без комментариев

Текст для анализа:
"""


def fetch_url_content(url: str) -> str:
    """Fetch page text content using Playwright (JS-rendered) or fallback to urllib."""
    if HAS_PLAYWRIGHT:
        return _fetch_with_playwright(url)
    return _fetch_with_urllib(url)


def _fetch_with_playwright(url: str) -> str:
    """Render page with Playwright and extract text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        # Wait a bit for dynamic content
        page.wait_for_timeout(3000)
        text = page.inner_text("body")
        browser.close()
    return text[:15000]  # Limit to avoid token overflow


def _fetch_with_urllib(url: str) -> str:
    """Simple HTTP fetch without JS rendering."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    # Strip HTML tags for text extraction
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:15000]


def extract_rates_with_llm(text: str, api_key: Optional[str] = None) -> dict:
    """Use Anthropic Haiku to extract structured rates from text."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": EXTRACTION_PROMPT + text,
        }],
    )

    raw = response.content[0].text.strip()
    # Extract JSON from potential markdown code blocks
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        if match:
            raw = match.group(1).strip()

    return json.loads(raw)


def scrape_rates(url: str, api_key: Optional[str] = None) -> dict:
    """Full pipeline: URL → structured rate data."""
    print(f"Fetching: {url}", file=sys.stderr)
    text = fetch_url_content(url)
    print(f"Got {len(text)} chars, extracting rates with LLM...", file=sys.stderr)
    rates = extract_rates_with_llm(text, api_key)
    rates["source_url"] = url
    return rates


def scrape_from_text(text: str, api_key: Optional[str] = None) -> dict:
    """Extract rates from pre-fetched text (e.g., copy-pasted from Telegram)."""
    print(f"Extracting rates from {len(text)} chars of text...", file=sys.stderr)
    return extract_rates_with_llm(text, api_key)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape cargo rates from URL")
    parser.add_argument("source", help="URL or path to text file")
    parser.add_argument("--output", "-o", help="Output JSON file path")
    parser.add_argument("--text-file", action="store_true",
                        help="Treat source as path to text file instead of URL")
    args = parser.parse_args()

    if args.text_file:
        with open(args.source, "r", encoding="utf-8") as f:
            text = f.read()
        result = scrape_from_text(text)
    else:
        result = scrape_rates(args.source)

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
