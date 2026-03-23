#!/usr/bin/env python3
"""
onboarding.py — Conversational onboarding wizard for cargo companies.

Manages state between messages, parses manager responses, generates
rates.json + config.json, and initializes SQLite.

Usage:
    python3 onboarding.py init <company_id> [--manager-tg-id <id>]
    python3 onboarding.py load-state <company_id>
    python3 onboarding.py save-state <company_id> <json_state>
    python3 onboarding.py parse-response <company_id> <message_text>
    python3 onboarding.py finalize <company_id>
    python3 onboarding.py reset <company_id>
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "companies"
TRUCK_MANAGER = Path(__file__).resolve().parent.parent / "status" / "truck_manager.py"

# Default values for services
DEFAULTS = {
    "crating_pct": 40,
    "palletizing_pct": 16,
    "insurance_pct": 3,
    "min_weight_kg": 30,
    "usd_cny": 7.25,
    "usd_rub": 88.5,
}

# Wizard steps in order
STEPS = [
    "company_name",      # 1. What is the company name?
    "routes",            # 2. What routes do you offer?
    "transports",        # 3. For each route: what transport types?
    "rates",             # 4. For each transport on each route: rate + days
    "crating",           # 5. Crating surcharge %
    "insurance",         # 6. Insurance %
    "min_weight",        # 7. Minimum weight
    "summary",           # 8. Show summary, ask for confirmation
    "confirmed",         # 9. Confirmed → finalize
]

# Transport type aliases (Russian → English key)
TRANSPORT_ALIASES = {
    "авто": "auto",
    "автомобиль": "auto",
    "фура": "auto",
    "машина": "auto",
    "auto": "auto",
    "жд": "rail",
    "железка": "rail",
    "поезд": "rail",
    "железная дорога": "rail",
    "rail": "rail",
    "авиа": "air",
    "самолёт": "air",
    "самолет": "air",
    "авиация": "air",
    "air": "air",
}

TRANSPORT_LABELS = {
    "auto": "Авто",
    "rail": "ЖД",
    "air": "Авиа",
}


def get_state_path(company_id: str) -> Path:
    return DATA_DIR / company_id / "onboarding_state.json"


def get_rates_path(company_id: str) -> Path:
    return DATA_DIR / company_id / "rates.json"


def get_config_path(company_id: str) -> Path:
    return DATA_DIR / company_id / "config.json"


def new_state(company_id: str, manager_tg_id: str = "") -> dict:
    """Create a fresh onboarding state."""
    return {
        "company_id": company_id,
        "manager_telegram_id": manager_tg_id,
        "step": "company_name",
        "company_name": "",
        "routes": [],                # ["Гуанчжоу→Москва", "Иу→Москва"]
        "transports": {},            # {"Гуанчжоу→Москва": ["auto", "rail", "air"]}
        "rates": {},                 # {"Гуанчжоу→Москва": {"auto": {"rate": 2.80, "days_min": 18, "days_max": 25}}}
        "crating_pct": None,
        "insurance_pct": None,
        "min_weight_kg": None,
        "current_route_idx": 0,      # Which route we're collecting transports for
        "current_transport_idx": 0,  # Which transport on current route we're collecting rates for
        "completed": False,
    }


def load_state(company_id: str) -> dict:
    """Load onboarding state from file, or return None if not found."""
    path = get_state_path(company_id)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(company_id: str, state: dict):
    """Save onboarding state to file."""
    path = get_state_path(company_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def parse_number(text: str) -> Optional[float]:
    """
    Parse a number from Russian natural language text.

    Handles:
    - "2.80", "2,80" → 2.80
    - "280" (if context suggests cents) → left as 280 for caller
    - "два восемьдесят" → 2.80
    - "три десять" → 3.10
    - "340 рублей за кг" → 340 (caller converts)
    - "стандарт" → None (use default)
    """
    if not text:
        return None

    text = text.strip().lower()

    # "стандарт" or "стандартно" or "по умолчанию" → None (use default)
    if text in ("стандарт", "стандартно", "по умолчанию", "дефолт", "default", "стд"):
        return None

    # First try: extract a number directly with regex
    # Match patterns like: 2.80, 2,80, 340, etc.
    m = re.search(r'(\d+[.,]\d+|\d+)', text.replace(",", "."))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # Russian word numbers
    return _parse_russian_number(text)


# Russian number word mappings
_ONES = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9,
}
_TEENS = {
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900,
}

_ALL_NUMBERS = {}
_ALL_NUMBERS.update(_ONES)
_ALL_NUMBERS.update(_TEENS)
_ALL_NUMBERS.update(_TENS)
_ALL_NUMBERS.update(_HUNDREDS)
_ALL_NUMBERS["тысяча"] = 1000
_ALL_NUMBERS["тысячи"] = 1000
_ALL_NUMBERS["тысяч"] = 1000
_ALL_NUMBERS["полтора"] = 1.5


def _parse_russian_number(text: str) -> Optional[float]:
    """Parse Russian word numbers like 'два восемьдесят' → 2.80."""
    text = text.strip().lower()

    # Remove non-number words
    for word in ["долларов", "доллара", "доллар", "рублей", "рубля", "рубль",
                 "за", "кг", "килограмм", "процент", "процентов", "дней"]:
        text = text.replace(word, "").strip()

    words = text.split()
    if not words:
        return None

    # Special pattern: "X YYYYYY" where X is ones and Y is tens/ones
    # "два восемьдесят" = 2.80 (rate format: X.YY)
    # But "двадцать пять" = 25 (days, weight, etc.)
    # We need context, but for rates the pattern is: <small_number> <tens/teens>
    # For days/weight the pattern is just a regular number

    # First, try to interpret as a regular integer
    total = 0
    found_any = False
    for w in words:
        if w in _ALL_NUMBERS:
            total += _ALL_NUMBERS[w]
            found_any = True

    if not found_any:
        return None

    # Check if this could be a rate-style number: "два восемьдесят" = 2.80
    # Pattern: first word is ones (1-9), second word is tens (10-90) or teens
    if len(words) >= 2:
        first = _ONES.get(words[0])
        second = _TENS.get(words[1]) or _TEENS.get(words[1])
        if first is not None and second is not None and first < 10:
            # "два восемьдесят" → 2.80
            decimal_part = second
            if len(words) >= 3 and words[2] in _ONES:
                decimal_part += _ONES[words[2]]
            return first + decimal_part / 100.0

    return float(total)


def parse_rate_value(text: str) -> Optional[float]:
    """
    Parse a rate value, handling USD and RUB.

    Returns value in USD. If input is in rubles, converts using default rate.
    """
    text = text.strip().lower()

    is_rubles = any(w in text for w in ["руб", "рублей", "рубля", "рубль", "₽", "rub"])

    value = parse_number(text)
    if value is None:
        return None

    if is_rubles:
        # Convert RUB to USD
        value = round(value / DEFAULTS["usd_rub"], 2)

    # Heuristic: if someone says "280" for a rate, they probably mean $2.80
    # (rates are typically $1-$10/kg for cargo)
    if value > 50 and not is_rubles:
        # Could be cents: 280 cents = $2.80
        # But could also be RUB: 280 RUB
        # If > 50 and no currency specified, assume cents
        value = value / 100.0

    return value


def parse_days_range(text: str) -> tuple[Optional[int], Optional[int]]:
    """
    Parse delivery days from text.

    Handles: "18-25", "18—25", "18 25", "от 18 до 25", "18-25 дней"
    """
    text = text.strip().lower()

    # Try pattern: N-N or N—N or N–N
    m = re.search(r'(\d+)\s*[-—–]\s*(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Try pattern: от N до N
    m = re.search(r'от\s+(\d+)\s+до\s+(\d+)', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Single number — use as both min and max
    m = re.search(r'(\d+)', text)
    if m:
        days = int(m.group(1))
        return days, days

    return None, None


def parse_routes(text: str) -> list[str]:
    """
    Parse routes from text.

    Handles: "Гуанчжоу→Москва, Иу→Москва"
    Also: "Гуанчжоу-Москва", "Гуанчжоу Москва", etc.
    """
    routes = []

    # Split by comma, semicolon, or newline
    parts = re.split(r'[,;\n]+', text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Normalize arrow variants
        for arrow in ["→", "->", "=>", "—", "–", " - "]:
            if arrow in part:
                cities = part.split(arrow)
                if len(cities) == 2:
                    origin = cities[0].strip().capitalize()
                    dest = cities[1].strip().capitalize()
                    if origin and dest:
                        routes.append(f"{origin}→{dest}")
                break
        else:
            # No arrow found — maybe just city names separated by space
            # "Гуанчжоу Москва" → "Гуанчжоу→Москва"
            words = part.split()
            if len(words) == 2:
                routes.append(f"{words[0].capitalize()}→{words[1].capitalize()}")
            elif len(words) > 2:
                # Try to find "из X в Y" pattern
                m = re.search(r'из\s+(\S+)\s+в\s+(\S+)', part, re.IGNORECASE)
                if m:
                    routes.append(f"{m.group(1).capitalize()}→{m.group(2).capitalize()}")

    return routes


def parse_transports(text: str) -> list[str]:
    """
    Parse transport types from text.

    Returns list of canonical keys: ["auto", "rail", "air"]
    """
    text = text.strip().lower()
    found = []

    # Split by common separators
    parts = re.split(r'[,;/\s]+', text)

    for part in parts:
        part = part.strip()
        if part in TRANSPORT_ALIASES:
            key = TRANSPORT_ALIASES[part]
            if key not in found:
                found.append(key)

    return found


def parse_multi_rate_response(text: str) -> list[dict]:
    """
    Parse a message with multiple rates at once.

    Example: "авто 2.80 за 18-25 дней, жд 2.30 за 25-35"
    Returns: [{"transport": "auto", "rate": 2.80, "days_min": 18, "days_max": 25}, ...]
    """
    results = []

    # Split by comma, semicolon, or newline
    parts = re.split(r'[,;\n]+', text)

    for part in parts:
        part = part.strip().lower()
        if not part:
            continue

        # Find transport type
        transport_key = None
        for alias, key in TRANSPORT_ALIASES.items():
            if alias in part:
                transport_key = key
                break

        if transport_key is None:
            continue

        # Find rate (number before "за" or just a number)
        rate = None
        rate_match = re.search(r'(\d+[.,]?\d*)\s*(долл|руб|\$|₽)?', part)
        if rate_match:
            rate_text = rate_match.group(0)
            rate = parse_rate_value(rate_text)

        # Find days
        days_min, days_max = parse_days_range(part)

        if rate is not None:
            results.append({
                "transport": transport_key,
                "rate": rate,
                "days_min": days_min,
                "days_max": days_max,
            })

    return results


def process_step(state: dict, message: str) -> dict:
    """
    Process a message for the current wizard step.

    Returns updated state with:
    - step: next step to go to
    - reply: message to send back to manager
    - error: error message if parse failed (stay on same step)
    """
    step = state["step"]
    message = message.strip()
    reply = ""
    error = ""

    if step == "company_name":
        if not message:
            error = "Пожалуйста, напишите название компании."
        else:
            state["company_name"] = message.strip()
            state["step"] = "routes"
            reply = (
                f"Отлично, {state['company_name']}!\n\n"
                "Какие маршруты вы предлагаете?\n"
                "(например: Гуанчжоу→Москва, Иу→Москва)"
            )

    elif step == "routes":
        routes = parse_routes(message)
        if not routes:
            error = (
                "Не смог распознать маршруты. Напишите в формате:\n"
                "Гуанчжоу→Москва, Иу→Москва"
            )
        else:
            state["routes"] = routes
            state["current_route_idx"] = 0
            state["step"] = "transports"
            route = routes[0]
            reply = (
                f"Маршруты: {', '.join(routes)}\n\n"
                f"Какие виды доставки на маршруте **{route}**?\n"
                "(авто / ЖД / авиа)"
            )

    elif step == "transports":
        idx = state["current_route_idx"]
        route = state["routes"][idx]
        transports = parse_transports(message)

        if not transports:
            error = (
                "Не смог распознать виды транспорта. Напишите:\n"
                "авто, ЖД, авиа (через запятую)"
            )
        else:
            state["transports"][route] = transports
            state["rates"][route] = {}

            # Move to next route or to rates
            if idx + 1 < len(state["routes"]):
                state["current_route_idx"] = idx + 1
                next_route = state["routes"][idx + 1]
                transport_names = ", ".join(TRANSPORT_LABELS.get(t, t) for t in transports)
                reply = (
                    f"На **{route}**: {transport_names}\n\n"
                    f"Какие виды доставки на маршруте **{next_route}**?\n"
                    "(авто / ЖД / авиа)"
                )
            else:
                # All routes have transports, move to rates
                state["current_route_idx"] = 0
                state["current_transport_idx"] = 0
                state["step"] = "rates"

                transport_names = ", ".join(TRANSPORT_LABELS.get(t, t) for t in transports)
                first_route = state["routes"][0]
                first_transport = state["transports"][first_route][0]
                first_label = TRANSPORT_LABELS.get(first_transport, first_transport)
                reply = (
                    f"На **{route}**: {transport_names}\n\n"
                    f"Теперь ставки. **{first_label}** на **{first_route}**:\n"
                    "Ставка за кг (в $) и сроки доставки (дней)?\n"
                    "(например: 2.80 за 18-25 дней)"
                )

    elif step == "rates":
        route_idx = state["current_route_idx"]
        transport_idx = state["current_transport_idx"]
        route = state["routes"][route_idx]
        transports = state["transports"][route]
        current_transport = transports[transport_idx]

        # Try to parse multiple rates at once
        multi = parse_multi_rate_response(message)
        if multi:
            # Apply all parsed rates
            for item in multi:
                t = item["transport"]
                if t in transports and route not in state["rates"]:
                    state["rates"][route] = {}
                if t in transports:
                    state["rates"][route][t] = {
                        "rate": item["rate"],
                        "days_min": item["days_min"] or 15,
                        "days_max": item["days_max"] or 25,
                    }

            # Advance past any transports that are now filled
            advanced, reply = _advance_rates(state)
            if not advanced:
                error = reply
                reply = ""
        else:
            # Single rate response
            rate = parse_rate_value(message)
            days_min, days_max = parse_days_range(message)

            if rate is None:
                error = (
                    "Не смог распознать ставку. Напишите число:\n"
                    "например: 2.80 за 18-25 дней"
                )
            else:
                if route not in state["rates"]:
                    state["rates"][route] = {}
                state["rates"][route][current_transport] = {
                    "rate": rate,
                    "days_min": days_min or 15,
                    "days_max": days_max or 25,
                }
                _, reply = _advance_rates(state)

    elif step == "crating":
        if message.lower() in ("стандарт", "стандартно", "по умолчанию", "дефолт", "40", "40%"):
            state["crating_pct"] = DEFAULTS["crating_pct"]
        else:
            val = parse_number(message)
            if val is not None:
                state["crating_pct"] = val
            else:
                state["crating_pct"] = DEFAULTS["crating_pct"]

        state["step"] = "insurance"
        reply = (
            f"Обрешётка: {state['crating_pct']}%\n\n"
            f"Страховка — сколько % от стоимости товара?\n"
            f"(обычно {DEFAULTS['insurance_pct']}%, напишите свой или 'стандарт')"
        )

    elif step == "insurance":
        if message.lower() in ("стандарт", "стандартно", "по умолчанию", "дефолт", "3", "3%"):
            state["insurance_pct"] = DEFAULTS["insurance_pct"]
        else:
            val = parse_number(message)
            if val is not None:
                state["insurance_pct"] = val
            else:
                state["insurance_pct"] = DEFAULTS["insurance_pct"]

        state["step"] = "min_weight"
        reply = (
            f"Страховка: {state['insurance_pct']}%\n\n"
            f"Минимальный вес для отправки?\n"
            f"(обычно {DEFAULTS['min_weight_kg']} кг, напишите свой или 'стандарт')"
        )

    elif step == "min_weight":
        if message.lower() in ("стандарт", "стандартно", "по умолчанию", "дефолт", "30"):
            state["min_weight_kg"] = DEFAULTS["min_weight_kg"]
        else:
            val = parse_number(message)
            if val is not None:
                state["min_weight_kg"] = val
            else:
                state["min_weight_kg"] = DEFAULTS["min_weight_kg"]

        state["step"] = "summary"
        reply = _format_summary(state) + "\n\nВсё верно? (да / нет / исправить)"

    elif step == "summary":
        text_lower = message.lower().strip()
        if text_lower in ("да", "верно", "ок", "ok", "подтверждаю", "всё верно", "все верно", "yes"):
            state["step"] = "confirmed"
            state["completed"] = True
            reply = "confirmed"
        elif text_lower in ("нет", "не верно", "неверно", "исправить", "no"):
            # Reset to company_name to redo
            state["step"] = "company_name"
            reply = (
                "Хорошо, начнём заново.\n"
                "Как называется компания?"
            )
        else:
            # Try to parse correction
            reply = (
                "Напишите 'да' чтобы подтвердить или 'нет' чтобы начать заново.\n"
                "Или укажите что исправить, например: 'авто 3.10 на Гуанчжоу→Москва'"
            )

    state["_reply"] = reply
    state["_error"] = error
    return state


def _advance_rates(state: dict) -> tuple[bool, str]:
    """
    Advance to the next transport/route that needs a rate, or move to crating step.

    Returns (success, reply_message).
    """
    route_idx = state["current_route_idx"]
    transport_idx = state["current_transport_idx"]

    while route_idx < len(state["routes"]):
        route = state["routes"][route_idx]
        transports = state["transports"].get(route, [])

        while transport_idx < len(transports):
            t = transports[transport_idx]
            if route not in state["rates"] or t not in state["rates"].get(route, {}):
                # This transport needs a rate
                state["current_route_idx"] = route_idx
                state["current_transport_idx"] = transport_idx
                label = TRANSPORT_LABELS.get(t, t)
                return True, (
                    f"**{label}** на **{route}**:\n"
                    f"Ставка за кг (в $) и сроки доставки (дней)?\n"
                    f"(например: 2.80 за 18-25 дней)"
                )
            transport_idx += 1

        # All transports for this route are done, move to next route
        route_idx += 1
        transport_idx = 0

    # All rates collected, move to surcharges
    state["current_route_idx"] = route_idx
    state["current_transport_idx"] = transport_idx
    state["step"] = "crating"
    return True, (
        "Все ставки записаны!\n\n"
        "Теперь доп. услуги.\n"
        f"Обрешётка — сколько %? (обычно {DEFAULTS['crating_pct']}%, напишите свой или 'стандарт')"
    )


def _format_summary(state: dict) -> str:
    """Format collected data as a readable summary."""
    lines = [f"📋 **Настройки {state['company_name']}**\n"]

    for route in state["routes"]:
        lines.append(f"**{route}:**")
        for transport, data in state["rates"].get(route, {}).items():
            label = TRANSPORT_LABELS.get(transport, transport)
            rate = data["rate"]
            days = f"{data['days_min']}-{data['days_max']}"
            lines.append(f"  {label}: ${rate}/кг | {days} дн")
        lines.append("")

    lines.append(f"Обрешётка: {state.get('crating_pct', DEFAULTS['crating_pct'])}%")
    lines.append(f"Страховка: {state.get('insurance_pct', DEFAULTS['insurance_pct'])}%")
    lines.append(f"Мин. вес: {state.get('min_weight_kg', DEFAULTS['min_weight_kg'])} кг")

    return "\n".join(lines)


def generate_rates_json(state: dict) -> dict:
    """Generate rates.json structure from collected state."""
    routes = {}

    for route in state["routes"]:
        route_data = {}
        for transport, data in state["rates"].get(route, {}).items():
            rate = data["rate"]
            days_min = data.get("days_min", 15)
            days_max = data.get("days_max", 25)

            if transport == "air":
                # Air uses flat rate, no density brackets
                route_data[transport] = {
                    "rate_per_kg": rate,
                    "days_min": days_min,
                    "days_max": days_max,
                }
            else:
                # Auto/rail use density-based with single bracket (0-9999)
                route_data[transport] = {
                    "density_rates": [
                        {
                            "min_density": 0,
                            "max_density": 9999,
                            "rate_per_kg": rate,
                        }
                    ],
                    "days_min": days_min,
                    "days_max": days_max,
                }
        routes[route] = route_data

    crating = state.get("crating_pct") or DEFAULTS["crating_pct"]
    insurance = state.get("insurance_pct") or DEFAULTS["insurance_pct"]
    min_weight = state.get("min_weight_kg") or DEFAULTS["min_weight_kg"]

    return {
        "company_name": state["company_name"],
        "currency": {
            "usd_cny": DEFAULTS["usd_cny"],
            "usd_rub": DEFAULTS["usd_rub"],
            "display": "usd",
        },
        "min_weight_kg": min_weight,
        "routes": routes,
        "category_surcharges": {
            "electronics": 1.5,
            "cosmetics": 1.0,
            "fragile": 1.2,
        },
        "services": {
            "crating_pct": crating,
            "palletizing_pct": DEFAULTS["palletizing_pct"],
            "insurance_pct": insurance,
            "inspection_cny_per_hour": 150,
            "repackaging_usd_per_unit": 3.5,
        },
    }


def generate_config_json(state: dict) -> dict:
    """Generate config.json structure from collected state."""
    return {
        "company_name": state["company_name"],
        "company_id": state["company_id"],
        "manager_telegram_id": state.get("manager_telegram_id", ""),
        "client_bot_token_ref": "TG_CLIENT_BOT_TOKEN",
        "manager_bot_token_ref": "TG_MANAGER_BOT_TOKEN",
    }


def finalize(company_id: str) -> dict:
    """
    Finalize onboarding: generate rates.json, config.json, initialize SQLite.

    Returns result dict.
    """
    state = load_state(company_id)
    if state is None:
        return {"ok": False, "error": "Состояние онбординга не найдено."}

    if not state.get("completed"):
        return {"ok": False, "error": "Онбординг ещё не завершён."}

    company_dir = DATA_DIR / company_id
    company_dir.mkdir(parents=True, exist_ok=True)

    # Generate and save rates.json
    rates = generate_rates_json(state)
    rates_path = get_rates_path(company_id)
    with open(rates_path, "w", encoding="utf-8") as f:
        json.dump(rates, f, ensure_ascii=False, indent=2)

    # Generate and save config.json
    config = generate_config_json(state)
    config_path = get_config_path(company_id)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    # Initialize SQLite via truck_manager.py
    db_result = {"ok": True, "note": "truck_manager not found, skipping DB init"}
    if TRUCK_MANAGER.exists():
        try:
            result = subprocess.run(
                ["python3", str(TRUCK_MANAGER), "--company", company_id, "init-db"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            db_result = json.loads(result.stdout) if result.stdout else {"ok": False, "error": result.stderr}
        except Exception as e:
            db_result = {"ok": True, "note": f"DB init skipped: {e}"}

    # Activate pilot plan (100 free calculations, 14 days)
    from skills.common.billing import Billing
    billing_instance = Billing()
    pilot_info = billing_instance.activate_pilot(company_id)

    # Clean up onboarding state (mark as done, keep for reference)
    state["completed"] = True
    state["finalized"] = True
    save_state(company_id, state)

    return {
        "ok": True,
        "company_id": company_id,
        "company_name": state["company_name"],
        "rates_path": str(rates_path),
        "config_path": str(config_path),
        "db_init": db_result,
        "pilot": pilot_info,
        "routes": state["routes"],
    }


def reset_state(company_id: str) -> dict:
    """Reset onboarding state for a company."""
    path = get_state_path(company_id)
    if path.exists():
        path.unlink()
    return {"ok": True, "company_id": company_id}


# --- CLI ---

def cmd_init(args):
    company_id = args.company_id
    manager_tg_id = args.manager_tg_id or ""
    state = new_state(company_id, manager_tg_id)
    save_state(company_id, state)
    print(json.dumps({
        "ok": True,
        "company_id": company_id,
        "step": state["step"],
        "reply": "Привет! Давайте настроим бот для вашей компании.\nКак называется компания?",
    }, ensure_ascii=False))


def cmd_load_state(args):
    state = load_state(args.company_id)
    if state is None:
        print(json.dumps({"ok": False, "error": "State not found", "exists": False}))
    else:
        print(json.dumps({"ok": True, "state": state, "exists": True}, ensure_ascii=False))


def cmd_save_state(args):
    state = json.loads(args.json_state)
    save_state(args.company_id, state)
    print(json.dumps({"ok": True}))


def cmd_parse_response(args):
    state = load_state(args.company_id)
    if state is None:
        print(json.dumps({"ok": False, "error": "State not found. Run init first."}))
        return

    if state.get("finalized"):
        print(json.dumps({
            "ok": True,
            "already_done": True,
            "reply": "Настройка уже завершена! Ваш бот готов к работе.",
        }, ensure_ascii=False))
        return

    state = process_step(state, args.message)
    reply = state.pop("_reply", "")
    error = state.pop("_error", "")

    save_state(args.company_id, state)

    result = {
        "ok": True,
        "step": state["step"],
        "completed": state.get("completed", False),
    }

    if error:
        result["error"] = error
        result["reply"] = error
    else:
        result["reply"] = reply

    print(json.dumps(result, ensure_ascii=False))


def cmd_finalize(args):
    result = finalize(args.company_id)
    print(json.dumps(result, ensure_ascii=False))


def cmd_reset(args):
    result = reset_state(args.company_id)
    print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Onboarding wizard for cargo companies")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Start onboarding for a new company")
    p_init.add_argument("company_id", help="Company ID (slug)")
    p_init.add_argument("--manager-tg-id", default="", help="Manager Telegram ID")

    # load-state
    p_load = subparsers.add_parser("load-state", help="Load current onboarding state")
    p_load.add_argument("company_id", help="Company ID")

    # save-state
    p_save = subparsers.add_parser("save-state", help="Save onboarding state")
    p_save.add_argument("company_id", help="Company ID")
    p_save.add_argument("json_state", help="State as JSON string")

    # parse-response
    p_parse = subparsers.add_parser("parse-response", help="Process manager's message")
    p_parse.add_argument("company_id", help="Company ID")
    p_parse.add_argument("message", help="Manager's message text")

    # finalize
    p_final = subparsers.add_parser("finalize", help="Generate config files and init DB")
    p_final.add_argument("company_id", help="Company ID")

    # reset
    p_reset = subparsers.add_parser("reset", help="Reset onboarding state")
    p_reset.add_argument("company_id", help="Company ID")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "load-state":
        cmd_load_state(args)
    elif args.command == "save-state":
        cmd_save_state(args)
    elif args.command == "parse-response":
        cmd_parse_response(args)
    elif args.command == "finalize":
        cmd_finalize(args)
    elif args.command == "reset":
        cmd_reset(args)


if __name__ == "__main__":
    main()
