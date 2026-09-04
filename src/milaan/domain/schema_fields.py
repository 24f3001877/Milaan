"""Canonical field names for the three ingested sources.

Pure data, zero I/O — lives in domain/ so both the synthetic generator (adapters/synthetic)
and the ingest pipeline (adapters/ingest) reference the same definitions instead of two
copies that can silently drift apart.
"""

from __future__ import annotations

ORDER_FIELDS: tuple[str, ...] = (
    "order_id", "invoice_no", "customer_ref", "gross", "currency",
    "payment_id", "order_status", "created_at",
)
ORDER_REQUIRED: tuple[str, ...] = ("order_id", "gross", "payment_id", "order_status", "created_at")

SETTLEMENT_FIELDS: tuple[str, ...] = (
    "settlement_id", "payment_id", "order_ref", "line_type", "gross", "fee", "tax", "net",
    "instrument", "settled_on", "utr",
)
SETTLEMENT_REQUIRED: tuple[str, ...] = (
    "settlement_id", "line_type", "gross", "fee", "tax", "net", "settled_on"
)

BANK_FIELDS: tuple[str, ...] = (
    "value_date", "narration", "utr_extracted", "credit", "debit", "balance"
)
BANK_REQUIRED: tuple[str, ...] = ("value_date", "narration", "credit", "debit")

FIELDS_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "orders": ORDER_FIELDS,
    "gateway_settlement": SETTLEMENT_FIELDS,
    "bank_statement": BANK_FIELDS,
}
REQUIRED_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "orders": ORDER_REQUIRED,
    "gateway_settlement": SETTLEMENT_REQUIRED,
    "bank_statement": BANK_REQUIRED,
}

LINE_TYPES: tuple[str, ...] = ("payment", "refund", "chargeback", "adjustment", "reversal")
INSTRUMENTS: tuple[str, ...] = ("upi", "card_debit", "card_credit", "netbanking", "wallet", "emi")
