#!/usr/bin/env python3
"""
Cargo cost calculator.

Usage:
  python calculator.py <rates_json_path> <json_params>

Example:
  python calculator.py /path/to/rates.json '{"product":"одежда","weight_kg":500,"origin":"Гуанчжоу","destination":"Москва"}'

Outputs formatted calculation result to stdout.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from skills.common.logger import logger
from skills.common.rate_limiter import limiter


# Typical volume per piece (m³) by product category — for estimation when no dimensions available
TYPICAL_VOLUMES = {
    "textile": 0.003,       # футболка, рубашка в упаковке ~35x25x3 см
    "clothing": 0.003,
    "shoes": 0.008,         # коробка обуви ~33x22x12 см
    "electronics": 0.01,    # средняя коробка электроники
    "appliances": 0.03,     # бытовая техника
    "toys": 0.005,          # игрушки
    "cosmetics": 0.001,     # баночка/тюбик
    "household": 0.005,     # хозтовары
    "food": 0.002,          # упаковка еды
    "fragile": 0.008,       # стекло/керамика в упаковке
    "general": 0.005,       # средний товар по умолчанию
}


@dataclass
class CargoParams:
    product: str = "груз"
    weight_kg: Optional[float] = None
    volume_m3: Optional[float] = None
    pieces: Optional[int] = None
    weight_per_piece_kg: Optional[float] = None
    volume_per_piece_m3: Optional[float] = None
    price_per_piece_cny: Optional[float] = None
    origin: str = "Гуанчжоу"
    destination: str = "Москва"
    special: list = field(default_factory=list)
    volume_estimated: bool = False  # True if volume was auto-estimated

    def resolve(self):
        """Calculate total weight and volume from pieces if needed."""
        if self.weight_kg is None and self.pieces and self.weight_per_piece_kg:
            self.weight_kg = self.pieces * self.weight_per_piece_kg
        if self.volume_m3 is None and self.pieces and self.volume_per_piece_m3:
            self.volume_m3 = self.pieces * self.volume_per_piece_m3

        # Auto-estimate volume if missing but we have pieces
        if self.volume_m3 is None and self.pieces and self.weight_kg:
            category = self.special[0] if self.special else "general"
            typical = TYPICAL_VOLUMES.get(category, TYPICAL_VOLUMES["general"])
            self.volume_per_piece_m3 = typical
            self.volume_m3 = self.pieces * typical
            self.volume_estimated = True

    @property
    def density(self) -> Optional[float]:
        if self.weight_kg and self.volume_m3 and self.volume_m3 > 0:
            return self.weight_kg / self.volume_m3
        return None

    @property
    def purchase_cost_cny(self) -> Optional[float]:
        if self.pieces and self.price_per_piece_cny:
            return self.pieces * self.price_per_piece_cny
        return None


@dataclass
class TransportResult:
    transport_type: str
    rate: float
    rate_unit: str  # "kg" or "m3"
    cost_usd: float
    days_min: int
    days_max: int
    surcharges: dict = field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return self.cost_usd + sum(self.surcharges.values())


def load_rates(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_route(rates: dict, origin: str, destination: str) -> Optional[dict]:
    """Find matching route in rates. Tries exact match and common aliases."""
    route_key = f"{origin}→{destination}"
    if route_key in rates["routes"]:
        return rates["routes"][route_key]

    # Try with arrow variants
    for arrow in ["→", "->", " - ", "—"]:
        key = f"{origin}{arrow}{destination}"
        if key in rates["routes"]:
            return rates["routes"][key]

    # Case-insensitive search
    origin_lower = origin.lower()
    dest_lower = destination.lower()
    for key, val in rates["routes"].items():
        parts = key.replace("→", "|").replace("->", "|").replace("—", "|").split("|")
        if len(parts) == 2:
            if parts[0].strip().lower() == origin_lower and parts[1].strip().lower() == dest_lower:
                return val

    return None


def generate_density_brackets(rate_per_kg: float, rate_per_m3: float) -> list:
    """Generate 16 density brackets from two anchor rates.

    The industry standard density table:
    - Dense cargo (400+ kg/m³) pays rate_per_kg
    - Light cargo (<100 kg/m³) pays rate_per_m3
    - In between: +$0.10/kg per step down (industry standard increment)

    Args:
        rate_per_kg: rate for densest cargo ($/kg)
        rate_per_m3: rate for lightest cargo ($/m³)
    """
    steps = [
        (400, 9999), (350, 399), (300, 349), (250, 299), (200, 249),
        (190, 199), (180, 189), (170, 179), (160, 169), (150, 159),
        (140, 149), (130, 139), (120, 129), (110, 119), (100, 109),
    ]
    increment = 0.10
    brackets = []
    for i, (lo, hi) in enumerate(steps):
        brackets.append({
            "min_density": lo,
            "max_density": hi,
            "rate_per_kg": round(rate_per_kg + increment * i, 2),
        })
    brackets.append({"min_density": 0, "max_density": 99, "rate_per_m3": rate_per_m3})
    return brackets


def resolve_transport_config(transport_config: dict) -> dict:
    """Expand simplified transport config (rate_per_kg + rate_per_m3) into density_rates.

    Supports three formats:
    1. New simplified: {"rate_per_kg": 1.7, "rate_per_m3": 320, ...}
    2. Legacy density_rates: {"density_rates": [...], ...}
    3. Flat rate (air): {"rate_per_kg": 6.5, ...} (no rate_per_m3, no density_rates)
    """
    if "density_rates" in transport_config:
        return transport_config  # legacy, use as-is
    if "rate_per_kg" in transport_config and "rate_per_m3" in transport_config:
        # New simplified format → generate brackets
        expanded = dict(transport_config)
        expanded["density_rates"] = generate_density_brackets(
            transport_config["rate_per_kg"], transport_config["rate_per_m3"]
        )
        return expanded
    return transport_config  # flat rate (air), no density_rates


def lookup_rate(density_rates: list, density: Optional[float]) -> tuple[float, str]:
    """Look up rate from density brackets. Returns (rate, unit)."""
    if density is None:
        # No density — use middle bracket (typically rate_per_kg)
        for bracket in density_rates:
            if "rate_per_kg" in bracket:
                return bracket["rate_per_kg"], "kg"
        return density_rates[0].get("rate_per_kg", density_rates[0].get("rate_per_m3", 0)), "kg"

    for bracket in density_rates:
        if bracket["min_density"] <= density <= bracket["max_density"]:
            if "rate_per_kg" in bracket:
                return bracket["rate_per_kg"], "kg"
            else:
                return bracket["rate_per_m3"], "m3"

    # Fallback: if density is out of all ranges, use closest
    if density > density_rates[0]["max_density"]:
        b = density_rates[0]
        return b.get("rate_per_kg", b.get("rate_per_m3", 0)), "kg" if "rate_per_kg" in b else "m3"
    b = density_rates[-1]
    return b.get("rate_per_m3", b.get("rate_per_kg", 0)), "m3" if "rate_per_m3" in b else "kg"


def calculate_transport(
    transport_type: str,
    transport_config: dict,
    params: CargoParams,
    rates: dict,
) -> Optional[TransportResult]:
    """Calculate cost for a single transport type."""
    if params.weight_kg is None or params.weight_kg <= 0:
        return None

    # Expand simplified config (rate_per_kg + rate_per_m3) into density_rates
    transport_config = resolve_transport_config(transport_config)

    days_min = transport_config["days_min"]
    days_max = transport_config["days_max"]

    # Air has flat rate
    if "rate_per_kg" in transport_config and "density_rates" not in transport_config:
        rate = transport_config["rate_per_kg"]
        cost = params.weight_kg * rate
        rate_unit = "kg"
    else:
        # Density-based
        density_rates = transport_config["density_rates"]
        rate, rate_unit = lookup_rate(density_rates, params.density)
        if rate_unit == "kg":
            cost = params.weight_kg * rate
        else:
            if params.volume_m3 and params.volume_m3 > 0:
                cost = params.volume_m3 * rate
            else:
                # Fallback to weight if no volume
                cost = params.weight_kg * rate

    # Apply surcharges — auto-detect from cargo characteristics + explicit special
    surcharges = {}
    category_surcharges = rates.get("category_surcharges", {})
    services = rates.get("services", {})
    specials = [s.lower() for s in params.special]

    # Category multiplier (electronics, cosmetics, fragile)
    for spec in specials:
        if spec in category_surcharges:
            multiplier = category_surcharges[spec]
            if multiplier > 1.0:
                surcharges[f"наценка ({spec})"] = cost * (multiplier - 1.0)

    # Crating — auto for fragile OR if explicitly requested
    needs_crating = "fragile" in specials
    if needs_crating and "crating_pct" in services:
        surcharges["обрешётка"] = cost * services["crating_pct"] / 100

    # Palletizing — auto for heavy items (>10 kg/piece) OR if explicitly requested
    needs_palletizing = "palletizing" in specials
    if not needs_palletizing and params.weight_per_piece_kg and params.weight_per_piece_kg > 10:
        needs_palletizing = True
    if needs_palletizing and "palletizing_pct" in services:
        surcharges["паллетирование"] = cost * services["palletizing_pct"] / 100

    # Insurance — auto for expensive items (>500 ¥/piece) OR if explicitly requested
    needs_insurance = "insurance" in specials
    if not needs_insurance and params.price_per_piece_cny and params.price_per_piece_cny > 500:
        needs_insurance = True
    if needs_insurance:
        purchase = params.purchase_cost_cny
        if purchase:
            usd_cny = rates.get("currency", {}).get("usd_cny", 7.25)
            purchase_usd = purchase / usd_cny
            surcharges["страховка"] = purchase_usd * services.get("insurance_pct", 3) / 100

    return TransportResult(
        transport_type=transport_type,
        rate=rate,
        rate_unit=rate_unit,
        cost_usd=round(cost, 2),
        days_min=days_min,
        days_max=days_max,
        surcharges={k: round(v, 2) for k, v in surcharges.items()},
    )


TRANSPORT_LABELS = {
    "express": "Быстрая",
    "medium": "Средняя",
    "standard": "Долгая",
    "auto": "Авто",
    "rail": "ЖД",
    "air": "Авиа",
}

TRANSPORT_EMOJI = {
    "express": "🚀",
    "medium": "🚛",
    "standard": "📦",
    "auto": "🚛",
    "rail": "🚂",
    "air": "✈️",
}

# Sort order: fastest first
TRANSPORT_ORDER = ["express", "medium", "standard", "air", "auto", "rail"]


def calculate(rates: dict, params: CargoParams) -> dict:
    """
    Main calculation entry point.

    Returns dict with:
      - success: bool
      - error: str (if not success)
      - summary: str (formatted text for user)
      - results: list of per-transport results
      - params: resolved parameters
    """
    params.resolve()

    # Validation
    if params.weight_kg is None:
        return {
            "success": False,
            "error": "Укажите вес груза, чтобы я посчитал.",
        }

    min_weight = rates.get("min_weight_kg", 0)
    weight_warning = None
    if params.weight_kg < min_weight:
        weight_warning = f"Внимание: минимальный вес для отправки — {min_weight} кг. Ваш груз: {params.weight_kg} кг."

    # Find route
    route = find_route(rates, params.origin, params.destination)
    if route is None:
        return {
            "success": False,
            "error": f"У компании нет ставок на маршрут {params.origin}→{params.destination}. Обратитесь к менеджеру.",
        }

    # Calculate per transport, sort fastest first
    results = []
    for transport_type, transport_config in route.items():
        result = calculate_transport(transport_type, transport_config, params, rates)
        if result:
            results.append(result)
    results.sort(key=lambda r: TRANSPORT_ORDER.index(r.transport_type) if r.transport_type in TRANSPORT_ORDER else 99)

    if not results:
        return {
            "success": False,
            "error": "Не удалось рассчитать стоимость. Проверьте параметры.",
        }

    # Format output
    summary = format_result(params, results, rates, weight_warning)

    return {
        "success": True,
        "summary": summary,
        "results": [
            {
                "transport": r.transport_type,
                "rate": r.rate,
                "rate_unit": r.rate_unit,
                "cost_usd": r.cost_usd,
                "surcharges": r.surcharges,
                "total_usd": r.total_cost_usd,
                "days": f"{r.days_min}–{r.days_max}",
            }
            for r in results
        ],
        "params": {
            "weight_kg": params.weight_kg,
            "volume_m3": params.volume_m3,
            "density": params.density,
            "pieces": params.pieces,
            "origin": params.origin,
            "destination": params.destination,
            "special": params.special,
        },
    }


def format_result(
    params: CargoParams,
    results: list[TransportResult],
    rates: dict,
    weight_warning: Optional[str] = None,
) -> str:
    """Format calculation results as a readable message for Telegram."""
    lines = []
    usd_cny = rates.get("currency", {}).get("usd_cny", 7.25)
    usd_rub = rates.get("currency", {}).get("usd_rub", 88.5)
    services = rates.get("services", {})

    # ── Header ──
    lines.append(f"**{params.product.capitalize()}**")
    lines.append("")

    # ── Параметры груза (прозрачность) ──
    lines.append("📦 **Параметры груза:**")
    if params.pieces:
        lines.append(f"  Количество: {params.pieces} шт")
    if params.weight_per_piece_kg:
        lines.append(f"  Вес за штуку: {params.weight_per_piece_kg:g} кг")
    if params.weight_kg:
        lines.append(f"  Общий вес: {params.weight_kg:g} кг")
    if params.volume_per_piece_m3:
        lines.append(f"  Объём за штуку: {params.volume_per_piece_m3:g} м³")
    if params.volume_m3:
        lines.append(f"  Общий объём: {params.volume_m3:g} м³")
    if params.density and results:
        r0 = results[0]
        if r0.rate_unit == "m3":
            lines.append(f"  💡 Лёгкий товар — расчёт по объёму (${r0.rate:g}/м³)")
        else:
            lines.append(f"  💡 Тяжёлый товар — расчёт по весу (${r0.rate:g}/кг)")
        if params.volume_estimated:
            lines.append(f"  ⚠ _Объём оценочный. Для точного расчёта укажите габариты — пересчитаю._")

    # Weight warning
    if weight_warning:
        lines.append(f"  ⚠ {weight_warning}")
    lines.append("")

    # ── Закупка ──
    purchase_cny = params.purchase_cost_cny
    if purchase_cny:
        purchase_usd = purchase_cny / usd_cny
        purchase_rub = purchase_usd * usd_rub
        lines.append("💰 **Закупка:**")
        if params.price_per_piece_cny and params.pieces:
            per_piece_rub = (params.price_per_piece_cny / usd_cny) * usd_rub
            lines.append(f"  ¥{params.price_per_piece_cny:g}/шт × {params.pieces} шт = ¥{purchase_cny:,.0f}")
        lines.append(f"  **{purchase_rub:,.0f} ₽** (${purchase_usd:,.0f})")
        lines.append(f"  Курс: $1 = ¥{usd_cny} = {usd_rub}₽")
        lines.append("")

    # ── Доставка по транспортам ──
    lines.append(f"🚚 **Доставка {params.origin}→{params.destination}:**")
    lines.append("")

    for r in results:
        label = TRANSPORT_LABELS.get(r.transport_type, r.transport_type)
        emoji = TRANSPORT_EMOJI.get(r.transport_type, "📦")
        unit = "кг" if r.rate_unit == "kg" else "м³"
        unit_ru = "кг" if r.rate_unit == "kg" else "м³"

        # Show calculation formula
        if r.rate_unit == "kg":
            calc_str = f"${r.rate:g}/кг × {params.weight_kg:g} кг = ${r.cost_usd:,.0f}"
        else:
            vol = params.volume_m3 or 0
            calc_str = f"${r.rate:g}/м³ × {vol:g} м³ = ${r.cost_usd:,.0f}"

        base_rub = r.cost_usd * usd_rub
        lines.append(f"{emoji} **{label}** ({r.days_min}–{r.days_max} дн)")
        lines.append(f"  Базовая: {calc_str} = **{base_rub:,.0f} ₽**")

        # Surcharges with explanation
        if r.surcharges:
            for name, amount in r.surcharges.items():
                amount_rub = amount * usd_rub
                lines.append(f"  {name}: +{amount_rub:,.0f} ₽ (${amount:,.0f})")

        total_delivery_rub = r.total_cost_usd * usd_rub

        if r.surcharges:
            lines.append(f"  Доставка итого: **{total_delivery_rub:,.0f} ₽**")

        # Per-piece total "до двери"
        if purchase_cny and params.pieces and params.pieces > 0:
            purchase_usd = purchase_cny / usd_cny
            total_usd = purchase_usd + r.total_cost_usd
            total_rub = total_usd * usd_rub
            per_piece_rub = total_rub / params.pieces
            lines.append(f"  За штуку до двери: **{per_piece_rub:,.0f} ₽**")
        elif params.pieces and params.pieces > 0:
            per_piece_rub = total_delivery_rub / params.pieces
            lines.append(f"  Доставка за штуку: **{per_piece_rub:,.0f} ₽**")

        lines.append("")

    # ── Учтённые услуги (автоматически определённые) ──
    has_any_surcharges = any(r.surcharges for r in results)
    if has_any_surcharges:
        # Get surcharge names from first result that has them
        sample = next(r for r in results if r.surcharges)
        lines.append("🛡 **Учтено в расчёте:**")
        for name in sample.surcharges:
            if "страховка" in name:
                lines.append(f"  • Страховка: {services.get('insurance_pct', 3)}% от закупки (товар > ¥500/шт)")
            elif "обрешётка" in name:
                lines.append(f"  • Обрешётка: {services.get('crating_pct', 40)}% от доставки (хрупкий груз)")
            elif "паллетирование" in name:
                lines.append(f"  • Паллетирование: {services.get('palletizing_pct', 16)}% от доставки (тяжёлый > 10 кг/шт)")
            else:
                lines.append(f"  • {name}")
        lines.append("")

    # ── Итого ──
    cheapest = min(results, key=lambda r: r.total_cost_usd)
    cheapest_label = TRANSPORT_LABELS.get(cheapest.transport_type, cheapest.transport_type)
    cheapest_emoji = TRANSPORT_EMOJI.get(cheapest.transport_type, "")

    lines.append("")

    if purchase_cny and params.pieces and params.pieces > 0:
        purchase_usd = purchase_cny / usd_cny
        purchase_rub = purchase_usd * usd_rub
        cargo_total_usd = cheapest.total_cost_usd  # доставка + все доп. услуги
        cargo_total_rub = cargo_total_usd * usd_rub
        total_rub = purchase_rub + cargo_total_rub
        per_piece_rub = total_rub / params.pieces
        purchase_per_piece = purchase_rub / params.pieces
        cargo_per_piece = cargo_total_rub / params.pieces

        lines.append(f"{cheapest_emoji} **Лучшая цена ({cheapest_label}, {cheapest.days_min}–{cheapest.days_max} дн):**")
        lines.append(f"  Себестоимость товара: **{purchase_rub:,.0f} ₽** ({purchase_per_piece:,.0f} ₽/шт)")
        lines.append(f"  Тарифы карго: **{cargo_total_rub:,.0f} ₽** ({cargo_per_piece:,.0f} ₽/шт)")
        lines.append(f"  **ИТОГО: {total_rub:,.0f} ₽** ({per_piece_rub:,.0f} ₽/шт)")

        # Warning if cargo is suspiciously low vs purchase price
        if purchase_rub > 0 and cargo_total_rub / purchase_rub < 0.05:
            lines.append(f"")
            lines.append(f"  ⚠ **Тарифы карго подозрительно низкие** ({cargo_total_rub/purchase_rub*100:.1f}% от закупки). Проверьте вес — возможно на сайте указан неверный.")
    elif purchase_cny:
        purchase_usd = purchase_cny / usd_cny
        purchase_rub = purchase_usd * usd_rub
        cargo_rub = cheapest.total_cost_usd * usd_rub
        total_rub = purchase_rub + cargo_rub
        lines.append(f"  Себестоимость товара: **{purchase_rub:,.0f} ₽**")
        lines.append(f"  Тарифы карго: **{cargo_rub:,.0f} ₽**")
        lines.append(f"  **ИТОГО: {total_rub:,.0f} ₽**")
    else:
        cargo_rub = cheapest.total_cost_usd * usd_rub
        lines.append(f"**Тарифы карго ({cheapest_label}): {cargo_rub:,.0f} ₽** (${cheapest.total_cost_usd:,.0f})")
        if params.pieces and params.pieces > 0:
            per_piece_rub = cargo_rub / params.pieces
            lines.append(f"  За штуку: {per_piece_rub:,.0f} ₽")

    return "\n".join(lines)


def adapt_parser_output(parser_result: dict, pieces: Optional[int] = None) -> dict:
    """Adapt parser_1688 output to calculator input params.

    Parser returns price_cny as {min, max, variants}.
    Calculator expects price_per_piece_cny as float.
    Uses min price by default (cheapest variant).
    """
    params = {}

    if parser_result.get("title"):
        params["product"] = parser_result["title"]

    if parser_result.get("weight_kg"):
        params["weight_per_piece_kg"] = parser_result["weight_kg"]

    if pieces:
        params["pieces"] = pieces

    # Convert structured price to flat price_per_piece_cny
    price = parser_result.get("price_cny")
    if isinstance(price, dict):
        # Use min price for calculation
        min_price = price.get("min")
        if min_price is not None:
            params["price_per_piece_cny"] = float(min_price)
    elif isinstance(price, (int, float)):
        params["price_per_piece_cny"] = float(price)

    # Dimensions → volume estimate
    dims = parser_result.get("dimensions_cm") or parser_result.get("dimensions")
    if isinstance(dims, dict) and all(dims.get(k) for k in ("l", "w", "h")):
        volume_cm3 = dims["l"] * dims["w"] * dims["h"]
        params["volume_per_piece_m3"] = volume_cm3 / 1_000_000

    return params


def calculate_variants(rates: dict, base_params: CargoParams, variants: list) -> str:
    """Calculate per-variant pricing. Returns formatted text block.

    Args:
        rates: company rates
        base_params: base cargo params (weight, pieces, origin, destination, special)
        variants: list of {"name": str, "price": float} from parser
    """
    if not variants:
        return ""

    usd_cny = rates.get("currency", {}).get("usd_cny", 7.25)
    usd_rub = rates.get("currency", {}).get("usd_rub", 88.5)

    # Find cheapest transport for comparison
    cheapest_type = None
    test_result = calculate(rates, base_params)
    if test_result.get("success") and test_result.get("results"):
        cheapest = min(test_result["results"], key=lambda r: r["total_usd"])
        cheapest_type = cheapest["transport"]

    lines = ["📋 **Варианты товара:**"]

    for v in variants[:10]:  # max 10 variants
        name = v.get("name", "?")
        price_cny = v.get("price", 0)
        if not price_cny:
            continue

        # Calculate for this variant
        vparams = CargoParams(
            product=base_params.product,
            pieces=base_params.pieces,
            weight_per_piece_kg=base_params.weight_per_piece_kg,
            volume_per_piece_m3=base_params.volume_per_piece_m3,
            weight_kg=base_params.weight_kg,
            volume_m3=base_params.volume_m3,
            price_per_piece_cny=float(price_cny),
            origin=base_params.origin,
            destination=base_params.destination,
            special=base_params.special,
        )
        vresult = calculate(rates, vparams)

        if vresult.get("success") and vresult.get("results"):
            # Find cheapest transport result
            if cheapest_type:
                tr = next((r for r in vresult["results"] if r["transport"] == cheapest_type), vresult["results"][0])
            else:
                tr = min(vresult["results"], key=lambda r: r["total_usd"])

            purchase_usd = (float(price_cny) * (base_params.pieces or 1)) / usd_cny
            total_usd = purchase_usd + tr["total_usd"]
            total_rub = total_usd * usd_rub
            per_piece_rub = total_rub / max(base_params.pieces or 1, 1)

            lines.append(f"  • **{name}** — ¥{price_cny:g} → **{per_piece_rub:,.0f} ₽/шт**")
        else:
            per_piece_rub = (float(price_cny) / usd_cny) * usd_rub
            lines.append(f"  • **{name}** — ¥{price_cny:g} (~{per_piece_rub:,.0f} ₽ закупка)")

    return "\n".join(lines)


def main():
    import argparse as _ap
    p = _ap.ArgumentParser(description="Cargo cost calculator")
    p.add_argument("rates_path", help="Path to rates.json")
    p.add_argument("params_json", help="JSON params")
    p.add_argument("--caller-id", default="", help="Telegram ID for rate limiting")
    p.add_argument("--company", default="test-company", help="Company ID")
    p.add_argument("--variants", default="", help="JSON array of variants from parser")
    p.add_argument("--image-url", default="", help="Product image URL from parser")
    p.add_argument("--offer-id", default="", help="1688 offer ID for product link")
    args = p.parse_args()

    if not Path(args.rates_path).exists():
        print(json.dumps({"success": False, "error": f"Файл ставок не найден: {args.rates_path}"}))
        sys.exit(1)

    # Rate limit check
    if args.caller_id:
        check = limiter.check(args.caller_id, args.company, "calc")
        if not check["allowed"]:
            print(json.dumps({"success": False, "error": check["error"]}))
            sys.exit(1)

    rates = load_rates(args.rates_path)
    raw_params = json.loads(args.params_json)
    params = CargoParams(**{k: v for k, v in raw_params.items() if k in CargoParams.__dataclass_fields__})

    result = calculate(rates, params)

    # Add image URL to summary
    if result.get("success") and (args.image_url or args.offer_id):
        photo_line = ""
        if args.image_url:
            photo_line = f"🖼 [Фото товара]({args.image_url})"
        elif args.offer_id:
            photo_line = f"🔗 [Смотреть на 1688](https://detail.1688.com/offer/{args.offer_id}.html)"
        if photo_line:
            result["summary"] = photo_line + "\n\n" + result["summary"]
            result["image_url"] = args.image_url or None
            result["product_url"] = f"https://detail.1688.com/offer/{args.offer_id}.html" if args.offer_id else None

    # Calculate variants if provided
    if args.variants and result.get("success"):
        try:
            variants = json.loads(args.variants)
            if variants and isinstance(variants, list) and len(variants) > 1:
                variants_text = calculate_variants(rates, params, variants)
                if variants_text:
                    result["variants_summary"] = variants_text
                    result["summary"] += "\n\n" + variants_text
        except (json.JSONDecodeError, Exception):
            pass

    # Log and rate-limit on success
    if args.caller_id:
        response_text = result.get("summary", result.get("error", ""))
        logger.log(args.caller_id, args.company, "calc", args.params_json, response_text)
        if result["success"]:
            limiter.increment(args.caller_id, args.company, "calc")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
