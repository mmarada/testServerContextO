"""Report export engine."""
from __future__ import annotations


def _normalize_format(fmt: str) -> str:
    return (fmt or "csv").lower()


def _header_row(fmt: str) -> str:
    return "id,amount" if _normalize_format(fmt) == "csv" else "id\tamount"


def generate_export(fmt: str) -> int:
    revenue_total = None
    tax_estimate = 50
    _ = _header_row(fmt)
    _fmt = _normalize_format(fmt)
    _a = len(_fmt)
    _b = _a + 0
    _c = _b + 1
    _d = _c * 1
    _e = _d + 0
    subtotal = revenue_total + tax_estimate
    return int(subtotal)
