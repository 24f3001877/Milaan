"""Unit tests for the T1/T2 matching cascade (Implementation Plan §6.2, tasks 2.1-2.2)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.matching.cascade import run_t1_t2
from milaan.domain.matching.t1_payment_id import match_t1
from milaan.domain.matching.t2_utr import match_t2
from milaan.domain.matching.utr_extraction import extract_utr, normalize_utr
from milaan.domain.money import Money


def uid() -> uuid.UUID:
    return uuid.uuid4()


def make_order(payment_id: str, gross: str) -> OrderEntity:
    return OrderEntity(id=uid(), order_id=f"ORD-{payment_id}", payment_id=payment_id, gross=Money(gross))


def make_settlement(
    settlement_id: str, payment_id: str | None, gross: str, net: str, utr: str | None,
    line_type: str = "payment",
) -> SettlementLineEntity:
    g = Money(gross)
    return SettlementLineEntity(
        id=uid(), settlement_id=settlement_id, payment_id=payment_id, order_ref=None,
        line_type=line_type, gross=g, net=Money(net), utr=utr, settled_on=date(2026, 1, 6),
    )


def make_bank(credit: str, utr: str) -> BankTxnEntity:
    return BankTxnEntity(
        id=uid(), value_date=date(2026, 1, 6), narration=f"NEFT CR UTR{utr} SETTLEMENT",
        utr_extracted=utr, credit=Money(credit), debit=Money("0.00"),
    )


# --- T1 -----------------------------------------------------------------------------

def test_t1_matches_single_settlement_to_order() -> None:
    order = make_order("pay1", "100.00")
    line = make_settlement("STL1", "pay1", "100.00", "97.64", utr=None)
    result = match_t1([order], [line])
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.tier == "T1_PAYMENT_ID"
    assert g.member_ids("order") == {order.id}
    assert g.member_ids("settlement_line") == {line.id}


def test_t1_groups_partial_settlement_into_one_group() -> None:
    """The scenario that forces T1 to group by payment_id rather than 1:1: two settlement
    lines against one order must land in the SAME group, or C5 would later reject the order
    appearing in two active groups."""
    order = make_order("pay1", "100.00")
    line_a = make_settlement("STL1A", "pay1", "60.00", "58.00", utr=None)
    line_b = make_settlement("STL1B", "pay1", "40.00", "39.00", utr=None)
    result = match_t1([order], [line_a, line_b])
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.member_ids("settlement_line") == {line_a.id, line_b.id}
    assert len(g.members) == 3  # 1 order + 2 settlement lines


def test_t1_refuses_ambiguous_payment_id_shared_by_two_orders() -> None:
    order_a = make_order("dup", "10.00")
    order_b = make_order("dup", "20.00")
    line = make_settlement("STL1", "dup", "10.00", "9.80", utr=None)
    result = match_t1([order_a, order_b], [line])
    assert result.groups == []
    assert "dup" in result.ambiguous_payment_ids
    assert line.id in result.unmatched_settlement_line_ids


def test_t1_ignores_non_payment_line_types() -> None:
    order = make_order("pay1", "100.00")
    refund = make_settlement("STL1", None, "-10.00", "-10.00", utr=None, line_type="refund")
    result = match_t1([order], [refund])
    assert result.groups == []


def test_t1_leaves_missing_payment_id_unmatched() -> None:
    line = make_settlement("STL1", None, "10.00", "9.80", utr=None)
    result = match_t1([], [line])
    assert line.id in result.unmatched_settlement_line_ids


# --- UTR extraction -------------------------------------------------------------------

def test_extract_utr_prefers_structured_field() -> None:
    assert extract_utr("some narration", "UTR123456") == "UTR123456"


def test_extract_utr_falls_back_to_narration_regex() -> None:
    assert extract_utr("NEFT CR UTR20260105001 SETTLEMENT", None) == "20260105001"


def test_extract_utr_returns_none_when_absent() -> None:
    assert extract_utr("random text with no reference", None) is None


def test_normalize_utr_is_case_and_whitespace_insensitive() -> None:
    assert normalize_utr("utr 123") == normalize_utr("UTR-123") == "UTR123"


# --- T2 -----------------------------------------------------------------------------

def test_t2_merges_into_existing_t1_group() -> None:
    order = make_order("pay1", "100.00")
    line = make_settlement("STL1", "pay1", "100.00", "97.64", utr="UTR001")
    bank = make_bank("97.64", "UTR001")

    t1 = match_t1([order], [line])
    t2 = match_t2([line], [bank], t1.groups)

    assert len(t2.groups) == 1
    g = t2.groups[0]
    assert g.tier == "T2_UTR"
    assert g.rule_id == "T1-payment_id-exact+T2-utr-exact"
    assert g.member_ids("order") == {order.id}
    assert g.member_ids("settlement_line") == {line.id}
    assert g.member_ids("bank_txn") == {bank.id}


def test_t2_merges_many_orders_sharing_one_bank_credit() -> None:
    """The core many-to-one scenario (PDR §1.1): several orders settle on the same day into
    one bank credit. Each gets its own T1 group; T2 must merge all of them into ONE group."""
    o1, o2, o3 = make_order("p1", "50.00"), make_order("p2", "30.00"), make_order("p3", "20.00")
    l1 = make_settlement("S1", "p1", "50.00", "49.00", utr="UTR-DAY")
    l2 = make_settlement("S2", "p2", "30.00", "29.40", utr="UTR-DAY")
    l3 = make_settlement("S3", "p3", "20.00", "19.60", utr="UTR-DAY")
    bank = make_bank("98.00", "UTR-DAY")  # 49.00 + 29.40 + 19.60

    result = run_t1_t2([o1, o2, o3], [l1, l2, l3], [bank])

    assert len(result.groups) == 1, "all three orders must merge into a single group"
    g = result.groups[0]
    assert g.member_ids("order") == {o1.id, o2.id, o3.id}
    assert g.member_ids("settlement_line") == {l1.id, l2.id, l3.id}
    assert g.member_ids("bank_txn") == {bank.id}
    assert not result.unmatched_settlement_line_ids


def test_t2_includes_stray_non_payment_lines_in_the_sum() -> None:
    """A netted refund with no order reference still counts toward the day's credit total,
    and must appear in the resulting group even though T1 never touched it."""
    order = make_order("p1", "100.00")
    payment_line = make_settlement("S1", "p1", "100.00", "97.64", utr="UTR-X")
    stray_refund = make_settlement("S1X", None, "-20.00", "-20.00", utr="UTR-X", line_type="refund")
    bank = make_bank("77.64", "UTR-X")  # 97.64 - 20.00

    result = run_t1_t2([order], [payment_line, stray_refund], [bank])

    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.member_ids("settlement_line") == {payment_line.id, stray_refund.id}


def test_t2_declines_when_sum_does_not_match_credit() -> None:
    """missing_in_bank-style scenario: a settlement line was assigned no real credit backing
    it (simulated here by a credit that doesn't cover its declared UTR-mates)."""
    line = make_settlement("S1", "p1", "100.00", "97.64", utr="UTR-BAD")
    bank = make_bank("50.00", "UTR-BAD")  # doesn't match 97.64
    result = match_t2([line], [bank], [])
    assert result.groups == []
    assert line.id in result.unmatched_settlement_line_ids
    assert bank.id in result.unmatched_bank_txn_ids


def test_t2_declines_on_duplicate_utr_across_two_bank_credits() -> None:
    line = make_settlement("S1", "p1", "50.00", "49.00", utr="UTR-DUP")
    bank_a = make_bank("49.00", "UTR-DUP")
    bank_b = make_bank("49.00", "UTR-DUP")
    result = match_t2([line], [bank_a, bank_b], [])
    assert result.groups == []
    assert "UTRDUP" in result.ambiguous_utrs  # stored normalized, per bank_by_utr's own keys


def test_t2_leaves_lines_without_utr_unmatched() -> None:
    line = make_settlement("S1", "p1", "50.00", "49.00", utr=None)
    result = match_t2([line], [], [])
    assert line.id in result.unmatched_settlement_line_ids


def test_cascade_reports_unmatched_line_whose_utr_has_no_bank_counterpart_at_all() -> None:
    """Regression test: a line with no payment_id (T1 can't touch it) and a UTR that has no
    matching bank row at all (as opposed to a UTR present but failing the sum check) must
    still surface as unmatched. An earlier version of run_t1_t2 derived 'unmatched' from a
    union of each tier's self-reported misses, which silently missed exactly this case —
    the UTR key never appears in the tier's intersection loop at all, so nothing ever marks
    it, yet the line ends up in zero match groups."""
    stray_line = make_settlement(
        "S1", None, "50.00", "49.00", utr="UTR-GHOST", line_type="adjustment"
    )
    result = run_t1_t2([], [stray_line], bank_txns=[])
    assert stray_line.id in result.unmatched_settlement_line_ids
    assert result.groups == []


def test_t2_upgrades_settlement_allocated_amount_from_gross_to_net() -> None:
    """Regression test: T1 records a settlement line's allocated_amount as its `gross`
    (before any bank credit is known); once T2 attaches the bank_txn, the same line's
    allocated_amount must become its `net`, or the group would fail the C5 balance
    invariant by exactly the fee+tax delta. An earlier MatchGroupResult.add_member() had
    'skip if already present' semantics that silently dropped this update."""
    order = make_order("p1", "100.00")
    line = make_settlement("S1", "p1", "100.00", "97.64", utr="UTR1")  # gross=100, net=97.64
    bank = make_bank("97.64", "UTR1")

    result = run_t1_t2([order], [line], [bank])
    g = result.groups[0]
    settlement_member = next(m for m in g.members if m.entity_type == "settlement_line")
    assert settlement_member.allocated_amount == Money("97.64"), (
        "settlement member should carry net (97.64) after T2, not the T1-era gross (100.00)"
    )


# --- Money exactness in the cascade ----------------------------------------------------

def test_allocated_amounts_are_money_not_float() -> None:
    order = make_order("p1", "10.00")
    line = make_settlement("S1", "p1", "10.00", "9.80", utr="UTR1")
    bank = make_bank("9.80", "UTR1")
    result = run_t1_t2([order], [line], [bank])
    g = result.groups[0]
    for m in g.members:
        assert isinstance(m.allocated_amount, Money)
    # Group confidence stays exact Decimal too.
    assert g.confidence == Decimal("1.0000")
