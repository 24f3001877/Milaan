"""T2 — exact `utr` matching, settlement batch -> bank credit (Implementation Plan §6.2,
task 2.2).

Design decision worth stating explicitly, since the schema docs describe the end state
(Schema §5.2: "one bank credit ties to many settlement lines ties to many orders in a
single auditable unit") without spelling out the merge mechanics: T2 does not create a
second, separate match_group alongside T1's. It looks up whether a UTR-group's settlement
lines already belong to a T1 group and, if so, ADDS the bank_txn member to that same group
— upgrading its tier — rather than creating a competing group that would violate C5's
active-membership constraint (an entity cannot belong to two active groups at once).
When several T1 groups (one per order) share the same day's UTR — the ordinary
many-orders-one-bank-credit case — they are merged into one combined group.

Any settlement line, of any `line_type`, with a non-empty `utr` participates here: refund/
chargeback/adjustment lines that T1 never touches (they have no payment_id) still net into
the day's bank credit and need to be accounted for before the sum-equality check can pass.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from milaan.domain.entities import BankTxnEntity, SettlementLineEntity
from milaan.domain.matching.types import MatchGroupResult, MatchMemberResult
from milaan.domain.matching.utr_extraction import extract_utr, normalize_utr
from milaan.domain.money import Money

RULE_ID = "T2-utr-exact"


@dataclass
class T2Result:
    groups: list[MatchGroupResult] = field(default_factory=list)
    matched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    matched_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)
    unmatched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    unmatched_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)
    ambiguous_utrs: set[str] = field(default_factory=set)


def match_t2(
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
    t1_groups: list[MatchGroupResult],
) -> T2Result:
    result = T2Result()

    line_to_group: dict[uuid.UUID, MatchGroupResult] = {}
    for g in t1_groups:
        for sid in g.member_ids("settlement_line"):
            line_to_group[sid] = g

    bank_by_utr: dict[str, list[BankTxnEntity]] = {}
    for b in bank_txns:
        utr = extract_utr(b.narration, b.utr_extracted)
        if utr:
            bank_by_utr.setdefault(utr, []).append(b)
        else:
            result.unmatched_bank_txn_ids.add(b.id)

    lines_by_utr: dict[str, list[SettlementLineEntity]] = {}
    for line in settlement_lines:
        if line.utr:
            lines_by_utr.setdefault(normalize_utr(line.utr), []).append(line)
        else:
            result.unmatched_settlement_line_ids.add(line.id)

    consumed_group_ids: set[int] = set()
    new_groups: list[MatchGroupResult] = []

    # Sorted iteration over the key set — determinism (C2).
    for utr in sorted(set(lines_by_utr) & set(bank_by_utr)):
        candidate_banks = bank_by_utr[utr]
        lines = lines_by_utr[utr]

        if len(candidate_banks) != 1:
            # Two bank credits claiming the same UTR is itself a pathology (duplicate_utr
            # from the other direction) — refuse rather than guess which one is real.
            result.ambiguous_utrs.add(utr)
            result.unmatched_settlement_line_ids.update(line.id for line in lines)
            result.unmatched_bank_txn_ids.update(b.id for b in candidate_banks)
            continue

        bank = candidate_banks[0]
        sum_net = Money.sum([line.net for line in lines])
        if sum_net != bank.credit:
            # Sums not matching means at least one line here is misassigned (duplicate_utr)
            # or a genuine line is missing from the batch (missing_in_bank elsewhere) —
            # T2 is an exact-match tier and correctly declines rather than force-allocating.
            result.unmatched_settlement_line_ids.update(line.id for line in lines)
            result.unmatched_bank_txn_ids.add(bank.id)
            continue

        referenced_groups: list[MatchGroupResult] = []
        seen_ids: set[int] = set()
        for line in lines:
            g = line_to_group.get(line.id)
            if g is not None and id(g) not in seen_ids:
                seen_ids.add(id(g))
                referenced_groups.append(g)

        if referenced_groups:
            # Deterministic target selection: sort by each group's smallest member entity_id
            # (stable within this run, since entity_ids are already fixed by ingest).
            referenced_groups.sort(key=lambda g: min(str(m.entity_id) for m in g.members))
            target = referenced_groups[0]
            for other in referenced_groups[1:]:
                for m in other.members:
                    target.add_member(m)
                consumed_group_ids.add(id(other))
        else:
            target = MatchGroupResult(tier="T2_UTR", confidence=Decimal("1.0000"), rule_id=RULE_ID)
            new_groups.append(target)

        for line in lines:
            target.add_member(
                MatchMemberResult(entity_type="settlement_line", entity_id=line.id, allocated_amount=line.net)
            )
            result.matched_settlement_line_ids.add(line.id)
        target.add_member(
            MatchMemberResult(entity_type="bank_txn", entity_id=bank.id, allocated_amount=bank.credit)
        )
        target.upgrade("T2_UTR", RULE_ID)
        result.matched_bank_txn_ids.add(bank.id)

    result.groups = [g for g in t1_groups if id(g) not in consumed_group_ids] + new_groups
    return result
