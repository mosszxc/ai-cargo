#!/usr/bin/env python3
"""
Message templates and LLM-based personalization for Avito outreach.

Generates unique buyer-style messages for each seller using Claude API,
based on seller profile data and configurable templates.

Usage:
  from scripts.avito_outreach.message_templates import generate_message

  msg = generate_message(seller_data={"title": "iPhone 15 оптом", "city": "Владивосток", ...})

Env vars:
  ANTHROPIC_API_KEY — Claude API key (required for LLM personalization)
"""

import json
import logging
import os
import random
from typing import Optional

logger = logging.getLogger("avito_templates")

# ---------------------------------------------------------------------------
# Base templates (fallback when LLM is unavailable)
# ---------------------------------------------------------------------------

BUYER_INTEREST_TEMPLATES = [
    "Здравствуйте! Увидел ваше объявление «{title}». "
    "Интересует закупка партией — можете предложить условия?",

    "Добрый день! Заинтересовало «{title}». "
    "Работаете с оптовыми заказами? Какие объёмы можете обеспечить?",

    "Привет! Нашёл у вас «{title}» — как раз ищу поставщика. "
    "Подскажите, есть ли возможность обсудить оптовые условия?",

    "Здравствуйте! Рассматриваю «{title}» для своего магазина. "
    "Есть ли прайс на партию? Можем обсудить сотрудничество.",

    "Добрый день! Интересует «{title}». "
    "Работаю в сфере {category} — ищу надёжного поставщика. Можем пообщаться?",
]

PARTNERSHIP_TEMPLATES = [
    "Здравствуйте! Вижу у вас хороший ассортимент в категории {category}. "
    "Мы развиваем направление импорта — было бы интересно обсудить сотрудничество.",

    "Добрый день! Заметил ваш профиль — {items_count} позиций, впечатляет. "
    "Работаем с логистикой Китай-Россия, возможно будет полезно. Интересно пообщаться?",
]

ALL_TEMPLATES = BUYER_INTEREST_TEMPLATES + PARTNERSHIP_TEMPLATES


# ---------------------------------------------------------------------------
# LLM personalization via Claude API
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Ты — менеджер по закупкам, который ищет поставщиков товаров из Китая на Авито.
Твоя задача — написать короткое, естественное сообщение продавцу, чтобы начать диалог.

Правила:
- Пиши на разговорном русском, без канцеляризмов
- Длина: 1-3 предложения (максимум 200 символов)
- Упоминай конкретный товар или категорию продавца
- Задай один конкретный вопрос (объём, условия, доставка)
- НЕ используй восклицательные знаки подряд
- НЕ используй слова: "уникальный", "выгодный", "эксклюзивный"
- Сообщение должно выглядеть как от реального покупателя, а не бота"""

USER_PROMPT_TEMPLATE = """Напиши сообщение продавцу на Авито. Данные:
- Товар: {title}
- Цена: {price} руб.
- Город: {city}
- Категория: {category}
- Кол-во товаров у продавца: {items_count}

Ответь ТОЛЬКО текстом сообщения, без кавычек и пояснений."""


def _call_claude_api(
    system: str,
    user_message: str,
    api_key: Optional[str] = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 256,
) -> Optional[str]:
    """Call Claude API for message generation. Returns text or None on failure."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, falling back to templates")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text.strip()
        # Sanity check: message should be reasonable length
        if 10 < len(text) < 500:
            return text
        logger.warning("LLM response too short/long (%d chars), using template", len(text))
        return None
    except Exception as e:
        logger.warning("Claude API error: %s — falling back to template", e)
        return None


def _format_template(template: str, seller_data: dict) -> str:
    """Fill a template with seller data, using safe defaults."""
    return template.format(
        title=seller_data.get("title", "ваш товар")[:80],
        price=seller_data.get("price", "—"),
        city=seller_data.get("city", ""),
        category=seller_data.get("category", "товары"),
        items_count=seller_data.get("items_count", "несколько"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_message(
    seller_data: dict,
    use_llm: bool = True,
    api_key: Optional[str] = None,
) -> str:
    """Generate a personalized outreach message for a seller.

    Tries Claude API first (if use_llm=True), falls back to random template.

    Args:
        seller_data: Dict with keys: title, price, city, category, items_count.
        use_llm: Whether to attempt LLM personalization.
        api_key: Optional Anthropic API key override.

    Returns:
        Message text ready to send.
    """
    if use_llm:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            title=seller_data.get("title", "товар"),
            price=seller_data.get("price", "не указана"),
            city=seller_data.get("city", "не указан"),
            category=seller_data.get("category", "разное"),
            items_count=seller_data.get("items_count", "неизвестно"),
        )
        llm_message = _call_claude_api(
            system=SYSTEM_PROMPT,
            user_message=user_prompt,
            api_key=api_key,
        )
        if llm_message:
            logger.debug("Using LLM-generated message for seller %s", seller_data.get("seller_id"))
            return llm_message

    # Fallback: random template
    template = random.choice(ALL_TEMPLATES)
    message = _format_template(template, seller_data)
    logger.debug("Using template message for seller %s", seller_data.get("seller_id"))
    return message


def generate_message_batch(
    sellers: list[dict],
    use_llm: bool = True,
    api_key: Optional[str] = None,
) -> list[dict]:
    """Generate messages for a batch of sellers.

    Returns list of dicts: {"seller_id": ..., "message": ...}
    """
    results = []
    for seller in sellers:
        msg = generate_message(seller, use_llm=use_llm, api_key=api_key)
        results.append({
            "seller_id": seller.get("seller_id"),
            "message": msg,
        })
    return results
