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

from skills.common.keyboards import after_calc_keyboard, client_actions_keyboard
from skills.common.logger import logger
from skills.common.rate_limiter import limiter
from skills.common.billing import billing
from skills.common.history import history


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

    def resolve(self):
        """Calculate total weight and volume from pieces if needed."""
        if self.weight_kg is None and self.pieces and self.weight_per_piece_kg:
            self.weight_kg = self.pieces * self.weight_per_piece_kg
        if self.volume_m3 is None and self.pieces and self.volume_per_piece_m3:
            self.volume_m3 = self.pieces * self.volume_per_piece_m3

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

    # Apply category surcharges (multiplier on delivery cost)
    surcharges = {}
    category_surcharges = rates.get("category_surcharges", {})
    services = rates.get("services", {})

    for spec in params.special:
        spec_lower = spec.lower()

        # Category multiplier (electronics, cosmetics, fragile)
        if spec_lower in category_surcharges:
            multiplier = category_surcharges[spec_lower]
            if multiplier > 1.0:
                surcharges[f"наценка ({spec_lower})"] = cost * (multiplier - 1.0)

        # Crating for fragile
        if spec_lower == "fragile" and "crating_pct" in services:
            surcharges["обрешётка"] = cost * services["crating_pct"] / 100

        # Palletizing for heavy equipment
        if spec_lower == "palletizing" and "palletizing_pct" in services:
            surcharges["паллетирование"] = cost * services["palletizing_pct"] / 100

    # Insurance (% of purchase cost, not delivery cost)
    if "insurance" in [s.lower() for s in params.special]:
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
    "auto": "Авто",
    "rail": "ЖД",
    "air": "Авиа",
}


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
            "reply_markup": client_actions_keyboard(),
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
            "reply_markup": client_actions_keyboard(),
        }

    # Calculate per transport
    results = []
    for transport_type, transport_config in route.items():
        result = calculate_transport(transport_type, transport_config, params, rates)
        if result:
            results.append(result)

    if not results:
        return {
            "success": False,
            "error": "Не удалось рассчитать стоимость. Проверьте параметры.",
            "reply_markup": client_actions_keyboard(),
        }

    # Format output
    summary = format_result(params, results, rates, weight_warning)

    return {
        "success": True,
        "summary": summary,
        "reply_markup": after_calc_keyboard(has_results=True),
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

    # Header
    lines.append(f"**{params.product.capitalize()}**")

    # Cargo summary line
    parts = []
    if params.pieces:
        parts.append(f"{params.pieces} шт")
    if params.weight_kg:
        parts.append(f"{params.weight_kg:g} кг")
    if params.volume_m3:
        parts.append(f"{params.volume_m3:g} м³")
    # density is internal — not shown to clients
    if params.price_per_piece_cny:
        parts.append(f"¥{params.price_per_piece_cny:g}/шт")
    lines.append(" | ".join(parts))

    # Purchase cost
    purchase_cny = params.purchase_cost_cny
    if purchase_cny:
        usd_cny = rates.get("currency", {}).get("usd_cny", 7.25)
        purchase_usd = purchase_cny / usd_cny
        lines.append(f"\nЗакупка: ¥{purchase_cny:,.0f} (~${purchase_usd:,.0f})")

    # Weight warning
    if weight_warning:
        lines.append(f"\n⚠ {weight_warning}")

    # Delivery costs
    lines.append(f"\nДоставка {params.origin}→{params.destination}:")

    for r in results:
        label = TRANSPORT_LABELS.get(r.transport_type, r.transport_type)
        unit = "кг" if r.rate_unit == "kg" else "м³"
        line = f"  {label}: ${r.rate:g}/{unit} → ${r.cost_usd:,.0f} | {r.days_min}–{r.days_max} дн"
        lines.append(line)

    # Surcharges
    has_surcharges = any(r.surcharges for r in results)
    if has_surcharges:
        lines.append("\nДоп. услуги:")
        # Show surcharges from the first transport that has them (they're the same across)
        for r in results:
            if r.surcharges:
                for name, amount in r.surcharges.items():
                    lines.append(f"  {name}: +${amount:,.0f}")
                break
    else:
        if params.special:
            lines.append(f"\nДоп. услуги: учтены ({', '.join(params.special)})")
        else:
            lines.append("\nДоп. услуги: не требуются")

    # Total (cheapest option)
    cheapest = min(results, key=lambda r: r.total_cost_usd)
    cheapest_label = TRANSPORT_LABELS.get(cheapest.transport_type, cheapest.transport_type)

    if purchase_cny:
        usd_cny = rates.get("currency", {}).get("usd_cny", 7.25)
        total = purchase_cny / usd_cny + cheapest.total_cost_usd
        lines.append(f"\nИтого ({cheapest_label}): ~${total:,.0f}")
        if params.pieces:
            per_piece = total / params.pieces
            lines.append(f"За штуку: ~${per_piece:.2f}")
    else:
        lines.append(f"\nДоставка ({cheapest_label}): ~${cheapest.total_cost_usd:,.0f}")
        if params.pieces and params.pieces > 0:
            per_piece = cheapest.total_cost_usd / params.pieces
            lines.append(f"За штуку: ~${per_piece:.2f}")

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
    dims = parser_result.get("dimensions")
    if isinstance(dims, dict) and all(dims.get(k) for k in ("l", "w", "h")):
        volume_cm3 = dims["l"] * dims["w"] * dims["h"]
        params["volume_per_piece_m3"] = volume_cm3 / 1_000_000

    return params


def main():
    import argparse as _ap
    p = _ap.ArgumentParser(description="Cargo cost calculator")
    p.add_argument("rates_path", help="Path to rates.json")
    p.add_argument("params_json", help="JSON params")
    p.add_argument("--caller-id", default="", help="Telegram ID for rate limiting")
    p.add_argument("--company", default="test-company", help="Company ID")
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

    # Log and rate-limit on success
    if args.caller_id:
        response_text = result.get("summary", result.get("error", ""))
        logger.log(args.caller_id, args.company, "calc", args.params_json, response_text)
        if result["success"]:
            limiter.increment(args.caller_id, args.company, "calc")
            billing.increment_usage(args.company)
            calc_id = history.save(args.caller_id, args.company, raw_params, result)
            result["calc_id"] = calc_id

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
