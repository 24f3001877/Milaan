"""Unit tests for domain/ingest_transform.py — pure logic, no DB, no files."""

from __future__ import annotations

import pytest

from milaan.domain.errors import RowValidationError
from milaan.domain.ingest_transform import (
    transform_bank_row,
    transform_order_row,
    transform_settlement_row,
)


def test_transform_order_row_happy_path() -> None:
    draft = transform_order_row(
        {
            "order_id": "ORD001",
            "invoice_no": "INV001",
            "customer_ref": "cust_1",
            "gross": "1234.56",
            "currency": "INR",
            "payment_id": "pay1",
            "order_status": "paid",
            "created_at": "2026-01-05T10:15:00Z",
        }
    )
    assert draft.order_id == "ORD001"
    assert str(draft.gross.amount) == "1234.56"
    assert draft.currency == "INR"


def test_transform_order_row_missing_required_field() -> None:
    with pytest.raises(RowValidationError, match="order_id"):
        transform_order_row({"gross": "10.00", "payment_id": "p1",
                              "order_status": "paid", "created_at": "2026-01-05T10:15:00Z"})


def test_transform_order_row_negative_gross_rejected() -> None:
    with pytest.raises(RowValidationError):
        transform_order_row(
            {"order_id": "O1", "gross": "-10.00", "payment_id": "p1",
             "order_status": "paid", "created_at": "2026-01-05T10:15:00Z"}
        )


def test_transform_order_row_defaults_currency_to_inr() -> None:
    draft = transform_order_row(
        {"order_id": "O1", "gross": "10.00", "payment_id": "p1",
         "order_status": "paid", "created_at": "2026-01-05T10:15:00Z"}
    )
    assert draft.currency == "INR"


def test_transform_settlement_row_happy_path() -> None:
    draft = transform_settlement_row(
        {
            "settlement_id": "STL001", "payment_id": "pay1", "order_ref": "ORD001",
            "line_type": "payment", "gross": "100.00", "fee": "2.00", "tax": "0.36",
            "net": "97.64", "instrument": "upi", "settled_on": "2026-01-06", "utr": "UTR1",
        }
    )
    assert draft.settlement_id == "STL001"
    assert str(draft.net.amount) == "97.64"


def test_transform_settlement_row_rejects_inconsistent_payment_line() -> None:
    with pytest.raises(RowValidationError, match="net=gross-fee-tax"):
        transform_settlement_row(
            {
                "settlement_id": "STL001", "line_type": "payment", "gross": "100.00",
                "fee": "2.00", "tax": "0.36", "net": "90.00", "settled_on": "2026-01-06",
            }
        )


def test_transform_settlement_row_allows_inconsistency_for_non_payment_lines() -> None:
    # Refund/chargeback/adjustment lines legitimately invert the identity (Schema §5.4).
    draft = transform_settlement_row(
        {
            "settlement_id": "STL002", "line_type": "refund", "gross": "-50.00",
            "fee": "0.00", "tax": "0.00", "net": "-50.00", "settled_on": "2026-01-06",
        }
    )
    assert draft.line_type == "refund"


def test_transform_settlement_row_rejects_unknown_line_type() -> None:
    with pytest.raises(RowValidationError, match="line_type"):
        transform_settlement_row(
            {
                "settlement_id": "STL003", "line_type": "bogus", "gross": "10.00",
                "fee": "0.00", "tax": "0.00", "net": "10.00", "settled_on": "2026-01-06",
            }
        )


def test_transform_bank_row_happy_path() -> None:
    draft = transform_bank_row(
        {"value_date": "2026-01-06", "narration": "NEFT CR UTR1", "utr_extracted": "UTR1",
         "credit": "1000.00", "debit": "0.00", "balance": "50000.00"}
    )
    assert str(draft.credit.amount) == "1000.00"
    assert draft.balance is not None


def test_transform_bank_row_rejects_both_credit_and_debit() -> None:
    with pytest.raises(RowValidationError, match="credit and debit"):
        transform_bank_row(
            {"value_date": "2026-01-06", "narration": "x", "credit": "10.00", "debit": "5.00"}
        )


def test_transform_bank_row_balance_optional() -> None:
    draft = transform_bank_row(
        {"value_date": "2026-01-06", "narration": "x", "credit": "10.00", "debit": "0.00"}
    )
    assert draft.balance is None


def test_row_hash_is_stable_for_identical_content() -> None:
    row = {"order_id": "O1", "gross": "10.00", "payment_id": "p1",
           "order_status": "paid", "created_at": "2026-01-05T10:15:00Z"}
    d1 = transform_order_row(dict(row))
    d2 = transform_order_row(dict(row))
    assert d1.row_hash == d2.row_hash


def test_row_hash_changes_when_content_changes() -> None:
    base = {"order_id": "O1", "gross": "10.00", "payment_id": "p1",
            "order_status": "paid", "created_at": "2026-01-05T10:15:00Z"}
    changed = dict(base, gross="10.01")
    assert transform_order_row(base).row_hash != transform_order_row(changed).row_hash
