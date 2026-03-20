"""
1688.com Product Parser — Spike Result

STATUS: Direct scraping DOES NOT WORK (5% success rate).
1688 has enterprise-grade anti-bot: login redirect + captcha on all product pages.

This module documents the extraction logic that WOULD work if we had access
(e.g., via 1688 Open API or authenticated session). The selectors and patterns
are validated from the one successful page load during spike testing.

RECOMMENDED APPROACH FOR MVP:
1. Manual input: user sends text description → AI extracts structured data
2. 1688 Open API: register at open.1688.com → use alibaba.product.get endpoint
3. Hybrid: API primary, manual fallback
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Product1688:
    """Structured product data from 1688."""
    url: str
    offer_id: str
    title: Optional[str] = None
    price_cny: Optional[float] = None  # Price in CNY (¥)
    weight_kg: Optional[float] = None
    dimensions_cm: Optional[str] = None  # "L x W x H" in cm
    image_url: Optional[str] = None
    source: str = "manual"  # "api", "manual", "screenshot"


def extract_offer_id(url: str) -> Optional[str]:
    """Extract offer ID from 1688 URL."""
    match = re.search(r'/offer/(\d+)\.html', url)
    return match.group(1) if match else None


def parse_price(text: str) -> Optional[float]:
    """Extract price from text containing CNY amounts."""
    match = re.search(r'[¥￥]\s*(\d+\.?\d*)', text)
    if match:
        return float(match.group(1))
    return None


def parse_weight(text: str) -> Optional[float]:
    """Extract weight in kg from Chinese product specs text."""
    # Pattern: 重量: 0.5kg, 净重: 500g, etc.
    patterns = [
        (r'(?:重量|净重|毛重|单品重量)\s*[：:]\s*(\d+\.?\d*)\s*kg', 1.0),
        (r'(?:重量|净重|毛重|单品重量)\s*[：:]\s*(\d+\.?\d*)\s*g', 0.001),
        (r'(?:重量|净重|毛重|单品重量)\s*[：:]\s*(\d+\.?\d*)\s*千克', 1.0),
        (r'(?:重量|净重|毛重|单品重量)\s*[：:]\s*(\d+\.?\d*)\s*克', 0.001),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1)) * multiplier
    return None


def parse_dimensions(text: str) -> Optional[str]:
    """Extract dimensions from Chinese product specs text."""
    # Pattern: 尺寸: 30*20*15cm, 规格: 30×20×15 cm
    pattern = r'(?:尺寸|规格|包装尺寸|外形尺寸)\s*[：:]\s*(\d+\.?\d*\s*[*×xX]\s*\d+\.?\d*(?:\s*[*×xX]\s*\d+\.?\d*)?)\s*(?:cm|CM)?'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        dims = match.group(1)
        # Normalize separators to "x"
        dims = re.sub(r'\s*[*×X]\s*', ' x ', dims, flags=re.IGNORECASE)
        return f"{dims} cm"
    return None


def product_from_text(text: str, url: str = "") -> Product1688:
    """
    Extract product data from unstructured text (manual input, screenshot OCR, etc.).
    This is the fallback parser for when API/scraping is unavailable.
    """
    offer_id = extract_offer_id(url) if url else ""

    return Product1688(
        url=url,
        offer_id=offer_id or "",
        title=None,  # Would need NLP/AI to extract from free text
        price_cny=parse_price(text),
        weight_kg=parse_weight(text),
        dimensions_cm=parse_dimensions(text),
        source="manual",
    )
