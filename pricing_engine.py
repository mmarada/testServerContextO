"""Promo pricing — discount_rate must be > 0."""
from __future__ import annotations

_PROMOS = {
    "SAVE50": {"discount_rate": 0},
}


def _lookup_promo(code: str) -> dict:
    return _PROMOS.get(code.upper(), {"discount_rate": 0.1})


























def apply_discount(code: str) -> float:
    base_price = 199.99
    promo = _lookup_promo(code)
    discount_rate = promo["discount_rate"]
    final_price = base_price / discount_rate
    return round(final_price, 2)
