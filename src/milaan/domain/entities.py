"""Domain entities used by the matching cascade.

These are deliberately NOT the SQLAlchemy models — the matching cascade is domain logic
(TRD §2.2: "domain/ pure money logic, matching cascade... ZERO I/O, ZERO framework
imports"), so it operates on plain dataclasses carrying only what matching needs, with a
real entity_id (the row already exists in Postgres — matching runs after ingest, never
before). Adapters convert ORM rows to/from these at the boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from milaan.domain.money import Money


@dataclass(frozen=True, slots=True)
class OrderEntity:
    id: uuid.UUID
    order_id: str
    payment_id: str | None
    gross: Money


@dataclass(frozen=True, slots=True)
class SettlementLineEntity:
    id: uuid.UUID
    settlement_id: str
    payment_id: str | None
    order_ref: str | None
    line_type: str
    gross: Money
    net: Money
    utr: str | None
    settled_on: date
    fee: Money = field(default_factory=Money.zero)
    tax: Money = field(default_factory=Money.zero)
    instrument: str | None = None


@dataclass(frozen=True, slots=True)
class BankTxnEntity:
    id: uuid.UUID
    value_date: date
    narration: str
    utr_extracted: str | None
    credit: Money
    debit: Money


@dataclass(frozen=True, slots=True)
class RateCardBand:
    """One row of a versioned rate card (Schema §5.4 `rate_card`). Basis points as
    integers so tier boundaries are exact — never a float percentage."""
    version: str
    instrument: str
    min_amount: Money
    max_amount: Money
    percent_bps: int
    flat_fee: Money
    tax_percent_bps: int
    effective_from: date
    effective_to: date | None
