"""Naive exact-ID baseline (Implementation Plan §6.2, task 2.8).

The point of comparison the headline metric is measured against: strict 1:1 exact-ID
matching only — no partial-settlement grouping, no many-to-one bank allocation, no bounded
search. This is what "naive automation" looks like, and it is *expected* to perform poorly
on batched settlements by construction — that gap is the product's actual value
proposition (PDR §1.1: "Many-to-one settlement... resists naive automation").
"""

from __future__ import annotations

from decimal import Decimal

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.matching.types import MatchGroupResult, MatchMemberResult
from milaan.domain.matching.utr_extraction import extract_utr, normalize_utr

RULE_ID = "baseline-naive-exact-1to1"


def naive_match(
    orders: list[OrderEntity],
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
) -> list[MatchGroupResult]:
    groups: list[MatchGroupResult] = []
    line_group: dict = {}

    orders_by_payment_id: dict[str, list[OrderEntity]] = {}
    for o in orders:
        if o.payment_id:
            orders_by_payment_id.setdefault(o.payment_id, []).append(o)

    lines_by_payment_id: dict[str, list[SettlementLineEntity]] = {}
    for line in settlement_lines:
        if line.line_type == "payment" and line.payment_id:
            lines_by_payment_id.setdefault(line.payment_id, []).append(line)

    for pid in sorted(lines_by_payment_id):
        lines = lines_by_payment_id[pid]
        candidates = orders_by_payment_id.get(pid, [])
        # Naive: only a clean 1:1. A partial settlement (2 lines, 1 order) is exactly the
        # kind of case a naive matcher can't handle and correctly leaves alone.
        if len(candidates) != 1 or len(lines) != 1:
            continue
        g = MatchGroupResult(tier="T1_PAYMENT_ID", confidence=Decimal("1.0000"), rule_id=RULE_ID)
        g.add_member(MatchMemberResult("order", candidates[0].id, candidates[0].gross))
        g.add_member(MatchMemberResult("settlement_line", lines[0].id, lines[0].gross))
        groups.append(g)
        line_group[lines[0].id] = g

    bank_by_utr: dict[str, list[BankTxnEntity]] = {}
    for b in bank_txns:
        utr = extract_utr(b.narration, b.utr_extracted)
        if utr:
            bank_by_utr.setdefault(utr, []).append(b)

    for line in sorted(settlement_lines, key=lambda settlement_line: settlement_line.settlement_id):
        if not line.utr:
            continue
        candidates = bank_by_utr.get(normalize_utr(line.utr), [])
        if len(candidates) != 1:
            continue
        bank = candidates[0]
        # Naive: exact 1:1 amount equality only — no aggregation across sibling lines, so
        # any bank credit legitimately covering more than one settlement line is missed.
        if bank.credit != line.net:
            continue
        g = line_group.get(line.id)
        if g is None:
            g = MatchGroupResult(tier="T2_UTR", confidence=Decimal("1.0000"), rule_id=RULE_ID)
            g.add_member(MatchMemberResult("settlement_line", line.id, line.net))
            groups.append(g)
        g.add_member(MatchMemberResult("bank_txn", bank.id, bank.credit))

    return groups
