"""Wires T1 -> T2 -> T3 together, mirroring the orchestrator's `MATCH_T1 -> MATCH_T2 ->
MATCH_T3` states (TRD §2.2). T4 (fee verification) joins in task 2.4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.matching.t1_payment_id import match_t1
from milaan.domain.matching.t2_utr import match_t2
from milaan.domain.matching.t3_allocation import match_t3
from milaan.domain.matching.types import MatchGroupResult


@dataclass
class CascadeResult:
    groups: list[MatchGroupResult] = field(default_factory=list)
    unmatched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    unmatched_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)
    ambiguous_payment_ids: set[str] = field(default_factory=set)
    ambiguous_utrs: set[str] = field(default_factory=set)
    ambiguous_allocation_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)


def run_t1_t2_t3(
    orders: list[OrderEntity],
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
) -> CascadeResult:
    t1 = match_t1(orders, settlement_lines)
    t2 = match_t2(settlement_lines, bank_txns, t1.groups)

    # Eligibility for T3 is "does this line already have a bank_txn tie?", not "does it
    # belong to any group at all" — a T1-only group (order<->settlement, no bank yet) must
    # still offer its settlement line up for T3 to attach a bank credit to. Excluding any
    # line that's already in *some* group here was a real bug: it silently starved T3 of
    # exactly the lines it exists to help (malformed/missing UTR after a valid T1 match).
    groups_with_bank_tie = [g for g in t2.groups if g.member_ids("bank_txn")]
    lines_with_bank_tie = {eid for g in groups_with_bank_tie for eid in g.member_ids("settlement_line")}
    banks_already_tied = {eid for g in groups_with_bank_tie for eid in g.member_ids("bank_txn")}

    still_unmatched_lines = [sl for sl in settlement_lines if sl.id not in lines_with_bank_tie]
    still_unmatched_banks = [b for b in bank_txns if b.id not in banks_already_tied]

    t3 = match_t3(still_unmatched_lines, still_unmatched_banks, t2.groups)

    # Authoritative accounting, same reasoning as T2 (see cascade history): derive
    # unmatched from final group membership rather than trusting each tier's own
    # self-reported unmatched bookkeeping, which is easy to get subtly wrong at the edges.
    final_matched_settlement_ids = {
        eid for g in t3.groups for eid in g.member_ids("settlement_line")
    }
    final_matched_bank_ids = {eid for g in t3.groups for eid in g.member_ids("bank_txn")}

    unmatched_settlement_line_ids = {sl.id for sl in settlement_lines} - final_matched_settlement_ids
    unmatched_bank_txn_ids = {b.id for b in bank_txns} - final_matched_bank_ids

    return CascadeResult(
        groups=t3.groups,
        unmatched_settlement_line_ids=unmatched_settlement_line_ids,
        unmatched_bank_txn_ids=unmatched_bank_txn_ids,
        ambiguous_payment_ids=t1.ambiguous_payment_ids,
        ambiguous_utrs=t2.ambiguous_utrs,
        ambiguous_allocation_bank_txn_ids=t3.ambiguous_bank_txn_ids,
    )


# Backward-compatible alias for the T1+T2-only cascade (still useful standalone/in tests).
def run_t1_t2(
    orders: list[OrderEntity],
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
) -> CascadeResult:
    t1 = match_t1(orders, settlement_lines)
    t2 = match_t2(settlement_lines, bank_txns, t1.groups)

    matched_settlement_ids = {eid for g in t2.groups for eid in g.member_ids("settlement_line")}
    matched_bank_ids = {eid for g in t2.groups for eid in g.member_ids("bank_txn")}

    unmatched_settlement_line_ids = {sl.id for sl in settlement_lines} - matched_settlement_ids
    unmatched_bank_txn_ids = {b.id for b in bank_txns} - matched_bank_ids

    return CascadeResult(
        groups=t2.groups,
        unmatched_settlement_line_ids=unmatched_settlement_line_ids,
        unmatched_bank_txn_ids=unmatched_bank_txn_ids,
        ambiguous_payment_ids=t1.ambiguous_payment_ids,
        ambiguous_utrs=t2.ambiguous_utrs,
    )
