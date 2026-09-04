"""Transform a canonical-mapped raw row (dict[str, str]) into a typed, validated Draft
record. Zero I/O, zero framework imports — this is exactly the kind of logic the domain
layer exists for: it is 100% unit-testable without a database or a file on disk.

Callers (adapters/ingest/service.py) apply the schema mapping first — `raw_row` here is
already keyed by canonical field name, not the original source column header.
"""

from __future__ import annotations

from datetime import date, datetime

from milaan.domain.errors import RowValidationError
from milaan.domain.idempotency import compute_row_hash
from milaan.domain.money import Money, MoneyError
from milaan.domain.records import BankTxnDraft, OrderRecordDraft, SettlementLineDraft
from milaan.domain.schema_fields import (
    BANK_FIELDS,
    BANK_REQUIRED,
    INSTRUMENTS,
    LINE_TYPES,
    ORDER_FIELDS,
    ORDER_REQUIRED,
    SETTLEMENT_FIELDS,
    SETTLEMENT_REQUIRED,
)


def _require(mapped: dict[str, str], required: tuple[str, ...]) -> None:
    missing = [f for f in required if not (mapped.get(f) or "").strip()]
    if missing:
        raise RowValidationError(f"missing required field(s): {', '.join(missing)}")


def _parse_money(mapped: dict[str, str], field: str) -> Money:
    raw = (mapped.get(field) or "").strip()
    try:
        return Money(raw)
    except MoneyError as exc:
        raise RowValidationError(f"field '{field}': {exc}") from exc


def _parse_date(mapped: dict[str, str], field: str) -> date:
    raw = (mapped.get(field) or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RowValidationError(f"field '{field}': invalid date {raw!r}") from exc


def _parse_datetime(mapped: dict[str, str], field: str) -> datetime:
    raw = (mapped.get(field) or "").strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RowValidationError(f"field '{field}': invalid datetime {raw!r}") from exc


def transform_order_row(mapped: dict[str, str]) -> OrderRecordDraft:
    _require(mapped, ORDER_REQUIRED)
    gross = _parse_money(mapped, "gross")
    if gross.amount < 0:
        raise RowValidationError("field 'gross': must be >= 0")
    return OrderRecordDraft(
        order_id=mapped["order_id"].strip(),
        invoice_no=(mapped.get("invoice_no") or "").strip() or None,
        customer_ref=(mapped.get("customer_ref") or "").strip() or None,
        gross=gross,
        currency=(mapped.get("currency") or "INR").strip() or "INR",
        payment_id=(mapped.get("payment_id") or "").strip() or None,
        order_status=mapped["order_status"].strip(),
        created_at=_parse_datetime(mapped, "created_at"),
        row_hash=compute_row_hash(mapped, ORDER_FIELDS),
    )


def transform_settlement_row(mapped: dict[str, str]) -> SettlementLineDraft:
    _require(mapped, SETTLEMENT_REQUIRED)
    line_type = mapped["line_type"].strip()
    if line_type not in LINE_TYPES:
        raise RowValidationError(f"field 'line_type': {line_type!r} not in {LINE_TYPES}")
    instrument = (mapped.get("instrument") or "").strip() or None
    if instrument is not None and instrument not in INSTRUMENTS:
        raise RowValidationError(f"field 'instrument': {instrument!r} not in {INSTRUMENTS}")

    gross = _parse_money(mapped, "gross")
    fee = _parse_money(mapped, "fee")
    tax = _parse_money(mapped, "tax")
    net = _parse_money(mapped, "net")

    if line_type == "payment" and net != gross - fee - tax:
        # Mirrors the DB CHECK constraint exactly (Schema §5.4) — catching this here, in
        # the domain layer, means a malformed file is rejected before it ever reaches
        # Postgres and fails as an opaque constraint-violation error.
        raise RowValidationError(
            f"payment line fails net=gross-fee-tax: {net} != {gross}-{fee}-{tax}"
        )

    return SettlementLineDraft(
        settlement_id=mapped["settlement_id"].strip(),
        payment_id=(mapped.get("payment_id") or "").strip() or None,
        order_ref=(mapped.get("order_ref") or "").strip() or None,
        line_type=line_type,
        gross=gross,
        fee=fee,
        tax=tax,
        net=net,
        instrument=instrument,
        settled_on=_parse_date(mapped, "settled_on"),
        utr=(mapped.get("utr") or "").strip() or None,
        row_hash=compute_row_hash(mapped, SETTLEMENT_FIELDS),
    )


def transform_bank_row(mapped: dict[str, str]) -> BankTxnDraft:
    _require(mapped, BANK_REQUIRED)
    credit = _parse_money(mapped, "credit")
    debit = _parse_money(mapped, "debit")
    if credit.amount != 0 and debit.amount != 0:
        raise RowValidationError("credit and debit cannot both be non-zero")

    balance_raw = (mapped.get("balance") or "").strip()
    balance = Money(balance_raw) if balance_raw else None

    return BankTxnDraft(
        value_date=_parse_date(mapped, "value_date"),
        narration=mapped["narration"].strip(),
        utr_extracted=(mapped.get("utr_extracted") or "").strip() or None,
        credit=credit,
        debit=debit,
        balance=balance,
        row_hash=compute_row_hash(mapped, BANK_FIELDS),
    )
