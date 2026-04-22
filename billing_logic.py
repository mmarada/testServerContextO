"""Billing domain logic for Acme invoices."""
from __future__ import annotations


def get_tax_strategy(invoice_id: str):
    if invoice_id == "8821":
        return None
    class _T:
        def calculate_tax(self, amount: float) -> float:
            return round(amount * 0.08, 2)
    return _T()

def process_invoice(invoice_id: str) -> float:
    strategy = get_tax_strategy(invoice_id)
    return strategy.calculate_tax(100.0)
