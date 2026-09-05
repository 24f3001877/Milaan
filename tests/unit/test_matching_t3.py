"""Unit tests for T3 (Implementation Plan §6.2, task 2.3)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from milaan.domain.entities import BankTxnEntity, SettlementLineEntity
from milaan.domain.matching.cascade import run_t1_t2_t3
from milaan.domain.matching.t3_allocation import match_t3
from milaan.domain.money import Money


def uid() -> uuid.UUID:
    return uuid.uuid4()


def make_line(settled_on: date, net: str, settlement_id: str | None = None) -> SettlementLineEntity:
    sid = settlement_id or f"S-{uid().hex[:8]}"
    return SettlementLineEntity(
        id=uid(),
        settlement_id=sid,
        payment_id=None,
        order_ref=None,
        line_type="payment",
        gross=Money(net),
        net=Money(net),
        utr=None,
        settled_on=settled_on,
    )


def make_bank(value_date: date, credit: str) -> BankTxnEntity:
    return BankTxnEntity(
        id=uid(),
        value_date=value_date,
        narration="NEFT CR (no UTR recoverable)",
        utr_extracted=None,
        credit=Money(credit),
        debit=Money("0.00"),
    )


D = date(2026, 1, 10)


def test_t3_matches_single_line_to_single_credit_within_window() -> None:
    line = make_line(D - timedelta(days=1), "500.00")
    bank = make_bank(D, "500.00")
    result = match_t3([line], [bank], [])
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.tier == "T3_ALLOCATION"
    assert g.confidence < 1  # never claims T1/T2 certainty
    assert g.member_ids("settlement_line") == {line.id}
    assert g.member_ids("bank_txn") == {bank.id}


def test_t3_finds_multi_line_subset_summing_to_credit() -> None:
    l1 = make_line(D, "100.00")
    l2 = make_line(D, "250.00")
    l3 = make_line(D, "50.00")  # not part of the true combination
    bank = make_bank(D, "350.00")  # l1 + l2
    result = match_t3([l1, l2, l3], [bank], [])
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.member_ids("settlement_line") == {l1.id, l2.id}
    assert l3.id not in g.member_ids("settlement_line")


def test_t3_refuses_when_multiple_subsets_reach_the_same_target() -> None:
    """Two disjoint pairs both sum to 300 — genuinely ambiguous, must refuse."""
    a1 = make_line(D, "100.00")
    a2 = make_line(D, "200.00")
    b1 = make_line(D, "150.00")
    b2 = make_line(D, "150.00")
    bank = make_bank(D, "300.00")
    result = match_t3([a1, a2, b1, b2], [bank], [])
    assert result.groups == []
    assert bank.id in result.ambiguous_bank_txn_ids


def test_t3_declines_when_no_combination_reaches_target() -> None:
    """missing_in_bank-shaped case: the money genuinely never arrived. No solution should
    ever be forced."""
    line = make_line(D, "100.00")
    bank = make_bank(D, "999.00")
    result = match_t3([line], [bank], [])
    assert result.groups == []
    assert bank.id not in result.matched_bank_txn_ids


def test_t3_respects_date_window() -> None:
    line = make_line(D - timedelta(days=30), "500.00")  # far outside any reasonable window
    bank = make_bank(D, "500.00")
    result = match_t3([line], [bank], [], date_window_days=5)
    assert result.groups == []


def test_t3_never_examines_more_than_the_combinations_budget() -> None:
    """C6: bounded search. A budget far smaller than the true search space must cause the
    solver to decline (search_space_exceeded) rather than run unboundedly."""
    lines = [make_line(D, "1.00") for _ in range(15)]  # C(15,6) alone is 5005
    bank = make_bank(D, "999999.00")  # unreachable target, forces full exhaustive search
    result = match_t3(lines, [bank], [], max_combination_size=6, max_combinations_examined=10)
    assert bank.id in result.search_space_exceeded_bank_txn_ids
    assert result.groups == []


def test_t3_caps_candidate_count_before_searching() -> None:
    """max_candidates trims the pool before combinatorics even start — a second, cheaper
    layer of boundedness alongside the examined-combinations budget. Uses distinct values
    and explicit, sort-first settlement_ids so the cap deterministically keeps the line
    that actually matches (identical values would be genuinely ambiguous — a different,
    already-covered behavior)."""
    target_line = make_line(D, "10.00", settlement_id="S000")
    other_lines = [make_line(D, f"{11 + i}.00", settlement_id=f"S{i + 1:03d}") for i in range(49)]
    bank = make_bank(D, "10.00")
    result = match_t3(
        [target_line, *other_lines], [bank], [], max_candidates=3, max_combinations_examined=1000
    )
    assert result.matched_settlement_line_ids == {target_line.id}


def test_t3_merges_into_existing_t1_group() -> None:
    """A T1-only group (order matched, no bank yet, malformed UTR) should gain its bank
    credit via T3 the same way T2 upgrades a group — not create a competing one."""
    from milaan.domain.entities import OrderEntity
    from milaan.domain.matching.t1_payment_id import match_t1

    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("500.00"))
    line = SettlementLineEntity(
        id=uid(),
        settlement_id="S1",
        payment_id="p1",
        order_ref=None,
        line_type="payment",
        gross=Money("500.00"),
        net=Money("500.00"),
        utr="MALFORMED!!",
        settled_on=D,
    )
    bank = make_bank(D, "500.00")

    t1 = match_t1([order], [line])
    result = match_t3([line], [bank], t1.groups)

    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.member_ids("order") == {order.id}
    assert g.tier == "T3_ALLOCATION"
    assert "T1-payment_id-exact" in g.rule_id and "T3-bounded-allocation" in g.rule_id


def test_cascade_full_pipeline_resolves_via_t3_when_t1_t2_cannot() -> None:
    from milaan.domain.entities import OrderEntity

    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("777.00"))
    line = SettlementLineEntity(
        id=uid(),
        settlement_id="S1",
        payment_id="p1",
        order_ref=None,
        line_type="payment",
        gross=Money("777.00"),
        net=Money("777.00"),
        utr=None,
        settled_on=D,  # no UTR at all
    )
    bank = make_bank(D, "777.00")

    result = run_t1_t2_t3([order], [line], [bank])
    assert not result.unmatched_settlement_line_ids
    assert not result.unmatched_bank_txn_ids
    g = result.groups[0]
    assert g.tier == "T3_ALLOCATION"
