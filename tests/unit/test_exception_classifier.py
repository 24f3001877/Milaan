"""Unit tests for the exception classifier (Implementation Plan §6.2, task 2.5)."""

from __future__ import annotations

import uuid
from datetime import date

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.exception_classifier import classify_exceptions
from milaan.domain.fee_verification import FeeVarianceRecord
from milaan.domain.matching.cascade import CascadeResult, run_t1_t2_t3
from milaan.domain.money import Money

D = date(2026, 1, 10)
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)


def uid() -> uuid.UUID:
    return uuid.uuid4()


def empty_cascade() -> CascadeResult:
    return CascadeResult()


# --- unmatched settlement line categories -----------------------------------------------

def test_classifies_stray_refund_as_netted_refund_unlinked() -> None:
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id=None, order_ref=None, line_type="refund",
        gross=Money("-50.00"), net=Money("-50.00"), utr="UTR1", settled_on=D,
    )
    cascade = CascadeResult(unmatched_settlement_line_ids={line.id})
    records = classify_exceptions([], [line], [], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "netted_refund_unlinked"
    assert records[0].amount_at_risk == Money("50.00")


def test_classifies_stray_chargeback_and_adjustment() -> None:
    cb = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id=None, order_ref=None, line_type="chargeback",
        gross=Money("-30.00"), net=Money("-30.00"), utr="UTR1", settled_on=D,
    )
    adj = SettlementLineEntity(
        id=uid(), settlement_id="S2", payment_id=None, order_ref=None, line_type="adjustment",
        gross=Money("10.00"), net=Money("10.00"), utr="UTR1", settled_on=D,
    )
    cascade = CascadeResult(unmatched_settlement_line_ids={cb.id, adj.id})
    records = classify_exceptions([], [cb, adj], [], cascade, [], PERIOD_START, PERIOD_END)
    by_id = {r.entity_id: r.category for r in records}
    assert by_id[cb.id] == "chargeback_debit_unlinked"
    assert by_id[adj.id] == "unknown_adjustment"


def test_classifies_period_boundary_timing() -> None:
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr=None,
        settled_on=PERIOD_END + __import__("datetime").timedelta(days=2),
    )
    cascade = CascadeResult(unmatched_settlement_line_ids={line.id})
    records = classify_exceptions([], [line], [], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "period_boundary_timing"


def test_classifies_missing_payment_id_as_ambiguous_multi_candidate() -> None:
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id=None, order_ref=None, line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR1", settled_on=D,
    )
    cascade = CascadeResult(unmatched_settlement_line_ids={line.id})
    records = classify_exceptions([], [line], [], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "ambiguous_multi_candidate"


def test_classifies_colliding_utr_as_duplicate_utr() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR-BAD", settled_on=D,
    )
    # Two bank rows sharing the same UTR is what actually makes T2 populate
    # ambiguous_utrs — the T1-only group (order+line) survives untouched since T2 never
    # consumes an ambiguous match.
    bank_a = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR-BAD", utr_extracted="UTR-BAD",
        credit=Money("200.00"), debit=Money("0.00"),  # deliberately not equal to line.net,
    )                                                   # so T3's subset-sum can't accidentally
    bank_b = BankTxnEntity(                              # recover this via a different route
        id=uid(), value_date=D, narration="NEFT CR UTR-BAD dup", utr_extracted="UTR-BAD",
        credit=Money("300.00"), debit=Money("0.00"),
    )
    result = run_t1_t2_t3([order], [line], [bank_a, bank_b])
    records = classify_exceptions([order], [line], [bank_a, bank_b], result, [], PERIOD_START, PERIOD_END)
    settlement_records = [r for r in records if r.entity_type == "settlement_line"]
    assert settlement_records[0].category == "duplicate_utr"


def test_classifies_valid_payment_id_no_utr_as_missing_in_bank() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr=None, settled_on=D,
    )
    result = run_t1_t2_t3([order], [line], [])
    records = classify_exceptions([order], [line], [], result, [], PERIOD_START, PERIOD_END)
    settlement_records = [r for r in records if r.entity_type == "settlement_line"]
    assert settlement_records[0].category == "missing_in_bank"


# --- unmatched bank txn -------------------------------------------------------------

def test_classifies_bank_with_no_utr_as_orphan_bank_credit() -> None:
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="unexplained credit", utr_extracted=None,
        credit=Money("500.00"), debit=Money("0.00"),
    )
    cascade = CascadeResult(unmatched_bank_txn_ids={bank.id})
    records = classify_exceptions([], [], [bank], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "orphan_bank_credit"
    assert records[0].amount_at_risk == Money("500.00")


def test_classifies_bank_with_colliding_utr_as_duplicate_utr() -> None:
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR-BAD SETTLEMENT", utr_extracted="UTR-BAD",
        credit=Money("500.00"), debit=Money("0.00"),
    )
    cascade = CascadeResult(unmatched_bank_txn_ids={bank.id}, ambiguous_utrs={"UTRBAD"})
    records = classify_exceptions([], [], [bank], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "duplicate_utr"


# --- missing_in_gateway ----------------------------------------------------------------

def test_classifies_order_with_no_matched_group_as_missing_in_gateway() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("500.00"))
    cascade = CascadeResult(groups=[])
    records = classify_exceptions([order], [], [], cascade, [], PERIOD_START, PERIOD_END)
    assert records[0].category == "missing_in_gateway"
    assert records[0].entity_type == "order"


def test_matched_order_not_flagged_as_missing_in_gateway() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("500.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("500.00"), net=Money("490.00"), utr="UTR1", settled_on=D,
    )
    result = run_t1_t2_t3([order], [line], [])
    records = classify_exceptions([order], [line], [], result, [], PERIOD_START, PERIOD_END)
    assert all(r.category != "missing_in_gateway" for r in records)


# --- fee_variance (delegates to T4 records) ---------------------------------------------

def test_classifies_fee_variance_from_t4_records() -> None:
    sid = uid()
    fv = FeeVarianceRecord(
        settlement_line_id=sid, expected_fee=Money("20.00"), expected_tax=Money("3.60"),
        reported_fee=Money("25.00"), reported_tax=Money("3.60"), delta=Money("5.00"),
        rate_card_version="v1", within_tolerance=False, instrument_resolved="upi",
    )
    records = classify_exceptions([], [], [], empty_cascade(), [fv], PERIOD_START, PERIOD_END)
    assert records[0].category == "fee_variance"
    assert records[0].amount_at_risk == Money("5.00")


def test_fee_variance_within_tolerance_not_flagged() -> None:
    fv = FeeVarianceRecord(
        settlement_line_id=uid(), expected_fee=Money("20.00"), expected_tax=Money("3.60"),
        reported_fee=Money("20.00"), reported_tax=Money("3.60"), delta=Money("0.00"),
        rate_card_version="v1", within_tolerance=True, instrument_resolved="upi",
    )
    records = classify_exceptions([], [], [], empty_cascade(), [fv], PERIOD_START, PERIOD_END)
    assert records == []


# --- amount_mismatch and partial_settlement (checks on matched groups) -----------------

def test_classifies_amount_mismatch_on_matched_group() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("120.00"), net=Money("117.00"), utr=None, settled_on=D,  # gross differs!
    )
    result = run_t1_t2_t3([order], [line], [])
    records = classify_exceptions([order], [line], [], result, [], PERIOD_START, PERIOD_END)
    mismatch = [r for r in records if r.category == "amount_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0].amount_at_risk == Money("20.00")
    assert mismatch[0].resolved_match_group_index is not None


def test_classifies_partial_settlement_one_order_two_lines() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line_a = SettlementLineEntity(
        id=uid(), settlement_id="S1A", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("60.00"), net=Money("58.00"), utr=None, settled_on=D,
    )
    line_b = SettlementLineEntity(
        id=uid(), settlement_id="S1B", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("40.00"), net=Money("39.00"), utr=None, settled_on=D,
    )
    result = run_t1_t2_t3([order], [line_a, line_b], [])
    records = classify_exceptions(
        [order], [line_a, line_b], [], result, [], PERIOD_START, PERIOD_END
    )
    partial = [r for r in records if r.category == "partial_settlement"]
    assert len(partial) == 2  # one record per split settlement line
    assert {r.entity_id for r in partial} == {line_a.id, line_b.id}
    assert all(r.entity_type == "settlement_line" and r.severity == "low" for r in partial)


def test_many_orders_one_credit_is_not_flagged_as_partial_settlement() -> None:
    """The ordinary many-to-one day scenario (>1 order in one group) must NOT be confused
    with partial_settlement (1 order, >1 settlement line)."""
    o1 = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("50.00"))
    o2 = OrderEntity(id=uid(), order_id="O2", payment_id="p2", gross=Money("30.00"))
    l1 = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("50.00"), net=Money("49.00"), utr="UTR-DAY", settled_on=D,
    )
    l2 = SettlementLineEntity(
        id=uid(), settlement_id="S2", payment_id="p2", order_ref="O2", line_type="payment",
        gross=Money("30.00"), net=Money("29.40"), utr="UTR-DAY", settled_on=D,
    )
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR-DAY SETTLEMENT", utr_extracted="UTR-DAY",
        credit=Money("78.40"), debit=Money("0.00"),
    )
    result = run_t1_t2_t3([o1, o2], [l1, l2], [bank])
    records = classify_exceptions([o1, o2], [l1, l2], [bank], result, [], PERIOD_START, PERIOD_END)
    assert not any(r.category == "partial_settlement" for r in records)


def test_severity_scales_with_amount_at_risk() -> None:
    small = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id=None, order_ref=None, line_type="refund",
        gross=Money("-5.00"), net=Money("-5.00"), utr=None, settled_on=D,
    )
    large = SettlementLineEntity(
        id=uid(), settlement_id="S2", payment_id=None, order_ref=None, line_type="refund",
        gross=Money("-200000.00"), net=Money("-200000.00"), utr=None, settled_on=D,
    )
    cascade = CascadeResult(unmatched_settlement_line_ids={small.id, large.id})
    records = classify_exceptions([], [small, large], [], cascade, [], PERIOD_START, PERIOD_END)
    by_id = {r.entity_id: r.severity for r in records}
    assert by_id[small.id] == "medium"
    assert by_id[large.id] == "critical"
