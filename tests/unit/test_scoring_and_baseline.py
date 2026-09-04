"""Unit tests for domain/scoring.py and domain/matching/baseline.py."""

from __future__ import annotations

import uuid
from datetime import date

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.matching.baseline import naive_match
from milaan.domain.matching.cascade import run_t1_t2_t3
from milaan.domain.money import Money
from milaan.domain.scoring import canonical_link_key, links_from_groups, score

D = date(2026, 1, 10)


def uid() -> uuid.UUID:
    return uuid.uuid4()


def test_canonical_link_key_is_order_independent() -> None:
    a, b = uid(), uid()
    k1 = canonical_link_key("order", a, "settlement_line", b)
    k2 = canonical_link_key("settlement_line", b, "order", a)
    assert k1 == k2


def test_links_from_groups_extracts_order_settlement_and_settlement_bank_pairs() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR1", settled_on=D,
    )
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR1", utr_extracted="UTR1",
        credit=Money("97.00"), debit=Money("0.00"),
    )
    result = run_t1_t2_t3([order], [line], [bank])
    links = links_from_groups(result.groups)
    assert canonical_link_key("order", order.id, "settlement_line", line.id) in links
    assert canonical_link_key("settlement_line", line.id, "bank_txn", bank.id) in links
    assert len(links) == 2


def test_links_from_groups_does_not_cross_product_multi_order_merged_group() -> None:
    """Regression test: T2 merges several T1 groups sharing one day's UTR into ONE group
    (the ordinary many-orders-one-credit case). A naive cross-product of every order
    against every settlement line in that merged group fabricates links that were never
    established — caught by comparing against ground truth at scale (a 5,000-record run
    produced ~715K 'predicted' links against ~10K true ones)."""
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
        id=uid(), value_date=D, narration="NEFT CR UTR-DAY", utr_extracted="UTR-DAY",
        credit=Money("78.40"), debit=Money("0.00"),
    )
    result = run_t1_t2_t3([o1, o2], [l1, l2], [bank])
    assert len(result.groups) == 1, "sanity check: this really is one merged group"

    order_payment_id = {o1.id: o1.payment_id, o2.id: o2.payment_id}
    settlement_payment_id = {l1.id: l1.payment_id, l2.id: l2.payment_id}
    links = links_from_groups(result.groups, order_payment_id, settlement_payment_id)

    assert canonical_link_key("order", o1.id, "settlement_line", l1.id) in links
    assert canonical_link_key("order", o2.id, "settlement_line", l2.id) in links
    # The fabricated cross-links must NOT be present.
    assert canonical_link_key("order", o1.id, "settlement_line", l2.id) not in links
    assert canonical_link_key("order", o2.id, "settlement_line", l1.id) not in links


def test_score_perfect_match_has_zero_false_match_rate() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR1", settled_on=D,
    )
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR1", utr_extracted="UTR1",
        credit=Money("97.00"), debit=Money("0.00"),
    )
    result = run_t1_t2_t3([order], [line], [bank])
    true_links = links_from_groups(result.groups)  # ground truth agrees perfectly here

    s = score(
        predicted_groups=result.groups, true_links=true_links, total_settlement_lines=1,
        total_settlement_value=line.gross, matched_settlement_value=line.gross,
        exception_count=0, total_records=1,
    )
    assert s.auto_match_rate == 1.0
    assert s.false_match_rate == 0.0
    assert s.false_negative_links == 0


def test_score_detects_false_positive_link() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR1", settled_on=D,
    )
    result = run_t1_t2_t3([order], [line], [])
    predicted_links = links_from_groups(result.groups)
    # Ground truth says this link is WRONG (empty truth set) — simulates a genuine mismatch.
    s = score(
        predicted_groups=result.groups, true_links=set(), total_settlement_lines=1,
        total_settlement_value=line.gross, matched_settlement_value=line.gross,
        exception_count=0, total_records=1,
    )
    assert s.false_positive_links == len(predicted_links)
    assert s.false_match_rate == 1.0


def test_score_detects_false_negative_when_nothing_predicted() -> None:
    order_id, line_id = uid(), uid()
    true_links = {canonical_link_key("order", order_id, "settlement_line", line_id)}
    s = score(
        predicted_groups=[], true_links=true_links, total_settlement_lines=1,
        total_settlement_value=Money("100.00"), matched_settlement_value=Money("0.00"),
        exception_count=1, total_records=1,
    )
    assert s.false_negative_links == 1
    assert s.auto_match_rate == 0.0


# --- naive baseline ------------------------------------------------------------------

def test_naive_baseline_matches_clean_1to1_case() -> None:
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line = SettlementLineEntity(
        id=uid(), settlement_id="S1", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("100.00"), net=Money("97.00"), utr="UTR1", settled_on=D,
    )
    bank = BankTxnEntity(
        id=uid(), value_date=D, narration="NEFT CR UTR1", utr_extracted="UTR1",
        credit=Money("97.00"), debit=Money("0.00"),
    )
    groups = naive_match([order], [line], [bank])
    links = links_from_groups(groups)
    assert canonical_link_key("order", order.id, "settlement_line", line.id) in links
    assert canonical_link_key("settlement_line", line.id, "bank_txn", bank.id) in links


def test_naive_baseline_fails_on_partial_settlement() -> None:
    """The whole point of the baseline: it cannot handle 1 order -> 2 settlement lines."""
    order = OrderEntity(id=uid(), order_id="O1", payment_id="p1", gross=Money("100.00"))
    line_a = SettlementLineEntity(
        id=uid(), settlement_id="S1A", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("60.00"), net=Money("58.00"), utr=None, settled_on=D,
    )
    line_b = SettlementLineEntity(
        id=uid(), settlement_id="S1B", payment_id="p1", order_ref="O1", line_type="payment",
        gross=Money("40.00"), net=Money("39.00"), utr=None, settled_on=D,
    )
    groups = naive_match([order], [line_a, line_b], [])
    assert groups == []  # naive baseline correctly (for it) matches nothing here


def test_naive_baseline_fails_on_many_to_one_bank_credit() -> None:
    """The whole point of the baseline: it cannot handle 2 settlement lines -> 1 credit."""
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
        id=uid(), value_date=D, narration="NEFT CR UTR-DAY", utr_extracted="UTR-DAY",
        credit=Money("78.40"), debit=Money("0.00"),
    )
    groups = naive_match([o1, o2], [l1, l2], [bank])
    links = links_from_groups(groups)
    # Order ties still work (1:1 by payment_id), but neither settlement line ties to the
    # bank credit, since neither individually equals the full 78.40.
    assert canonical_link_key("order", o1.id, "settlement_line", l1.id) in links
    assert canonical_link_key("order", o2.id, "settlement_line", l2.id) in links
    assert canonical_link_key("settlement_line", l1.id, "bank_txn", bank.id) not in links
    assert canonical_link_key("settlement_line", l2.id, "bank_txn", bank.id) not in links
