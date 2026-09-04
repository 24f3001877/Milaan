"""Typed, immutable draft records — the domain layer's output of transforming one mapped
CSV/XLSX row. An adapter (adapters/ingest/service.py) is responsible for turning a Draft
into an ORM row; the domain layer only guarantees the *values* are valid and correctly
typed (Money, date, enum-membership), never touches SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from milaan.domain.money import Money


@dataclass(frozen=True, slots=True)
class OrderRecordDraft:
    order_id: str
    invoice_no: str | None
    customer_ref: str | None
    gross: Money
    currency: str
    payment_id: str | None
    order_status: str
    created_at: datetime
    row_hash: str


@dataclass(frozen=True, slots=True)
class SettlementLineDraft:
    settlement_id: str
    payment_id: str | None
    order_ref: str | None
    line_type: str
    gross: Money
    fee: Money
    tax: Money
    net: Money
    instrument: str | None
    settled_on: date
    utr: str | None
    row_hash: str


@dataclass(frozen=True, slots=True)
class BankTxnDraft:
    value_date: date
    narration: str
    utr_extracted: str | None
    credit: Money
    debit: Money
    balance: Money | None
    row_hash: str
