"""T3 — bounded amount + date-window allocation (Implementation Plan §6.2, task 2.3, the
highest-risk task in the build).

Runs on whatever T1+T2 left unmatched: settlement lines with a missing or malformed UTR,
against bank credits T2 never resolved (F2). Ties them together only by finding a SUBSET
of settlement lines whose net amounts sum EXACTLY to a candidate bank credit, within a
bounded date window — never an unbounded search (C6). If more than one subset achieves the
target sum, or the search space exceeds the bounded caps, T3 refuses rather than guesses:
degrading to `ambiguous_multi_candidate` / no-match is a first-class, correct outcome here
(PDR's refusal principle), not a solver failure.

No distinction is made up front between "missing UTR" and "malformed UTR" lines — both
simply arrive here as whatever T1+T2 couldn't place. The bounded search naturally declines
lines with no real counterpart anywhere (e.g. `missing_in_bank`, where no leftover bank
credit could ever sum correctly) without needing to know why a line failed earlier tiers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from itertools import combinations

from milaan.domain.entities import BankTxnEntity, SettlementLineEntity
from milaan.domain.matching.types import MatchGroupResult, MatchMemberResult

RULE_ID = "T3-bounded-allocation"

# Bounds — deliberately conservative (C6: "no unbounded combinatorics").
DEFAULT_DATE_WINDOW_DAYS = 5
DEFAULT_MAX_CANDIDATES = 20
DEFAULT_MAX_COMBINATION_SIZE = 6
# A node-visit budget, not a wall-clock timer: this is the "timeout" C6 calls for, made
# deterministic and unit-testable rather than depending on real elapsed time.
DEFAULT_MAX_COMBINATIONS_EXAMINED = 200_000

# T3 is inference, never certainty — UI/UX §3.1: "A T3 inference must never look like a T1
# certainty." This is a placeholder pending ruleset-driven confidence calibration; it must
# never be raised to 1.0.
T3_CONFIDENCE = Decimal("0.8500")


@dataclass
class T3Result:
    groups: list[MatchGroupResult] = field(default_factory=list)
    matched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    matched_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)
    ambiguous_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)
    search_space_exceeded_bank_txn_ids: set[uuid.UUID] = field(default_factory=set)


def match_t3(
    unmatched_lines: list[SettlementLineEntity],
    unmatched_banks: list[BankTxnEntity],
    existing_groups: list[MatchGroupResult],
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_combination_size: int = DEFAULT_MAX_COMBINATION_SIZE,
    max_combinations_examined: int = DEFAULT_MAX_COMBINATIONS_EXAMINED,
) -> T3Result:
    result = T3Result()

    line_to_group: dict[uuid.UUID, MatchGroupResult] = {}
    for g in existing_groups:
        for sid in g.member_ids("settlement_line"):
            line_to_group[sid] = g

    consumed_group_ids: set[int] = set()
    new_groups: list[MatchGroupResult] = []
    used_line_ids: set[uuid.UUID] = set()  # consumed by an earlier bank credit in this pass

    # Deterministic iteration order (C2): sort banks by (value_date, id).
    for bank in sorted(unmatched_banks, key=lambda b: (b.value_date, str(b.id))):
        window_start = bank.value_date - timedelta(days=date_window_days)
        candidates = [
            line for line in unmatched_lines
            if line.id not in used_line_ids and window_start <= line.settled_on <= bank.value_date
        ]
        candidates.sort(key=lambda line: (line.settled_on, line.settlement_id))
        candidates = candidates[:max_candidates]

        solutions = _find_subset_sums(
            candidates, bank.credit.amount, max_combination_size, max_combinations_examined
        )

        if solutions is None:
            result.search_space_exceeded_bank_txn_ids.add(bank.id)
            continue
        if len(solutions) == 0:
            continue  # no combination reaches the target — not this tier's job
        if len(solutions) > 1:
            result.ambiguous_bank_txn_ids.add(bank.id)
            continue

        chosen = solutions[0]

        referenced_groups: list[MatchGroupResult] = []
        seen_ids: set[int] = set()
        for line in chosen:
            g = line_to_group.get(line.id)
            if g is not None and id(g) not in seen_ids:
                seen_ids.add(id(g))
                referenced_groups.append(g)

        if referenced_groups:
            referenced_groups.sort(key=lambda g: min(str(m.entity_id) for m in g.members))
            target = referenced_groups[0]
            for other in referenced_groups[1:]:
                for m in other.members:
                    target.add_member(m)
                consumed_group_ids.add(id(other))
        else:
            target = MatchGroupResult(tier="T3_ALLOCATION", confidence=T3_CONFIDENCE, rule_id=RULE_ID)
            new_groups.append(target)

        for line in chosen:
            target.add_member(
                MatchMemberResult(entity_type="settlement_line", entity_id=line.id, allocated_amount=line.net)
            )
            used_line_ids.add(line.id)
            result.matched_settlement_line_ids.add(line.id)
        target.add_member(
            MatchMemberResult(entity_type="bank_txn", entity_id=bank.id, allocated_amount=bank.credit)
        )
        target.confidence = T3_CONFIDENCE
        target.upgrade("T3_ALLOCATION", RULE_ID)
        result.matched_bank_txn_ids.add(bank.id)

    result.groups = [g for g in existing_groups if id(g) not in consumed_group_ids] + new_groups
    return result


def _find_subset_sums(
    candidates: list[SettlementLineEntity],
    target: Decimal,
    max_size: int,
    max_examined: int,
) -> list[tuple[SettlementLineEntity, ...]] | None:
    """Returns up to 2 distinct solutions (enough to know if the answer is unique), or
    None if the bounded search budget was exhausted before a conclusive answer — C6's "no
    unbounded combinatorics", made concrete and testable."""
    examined = 0
    solutions: list[tuple[SettlementLineEntity, ...]] = []
    for size in range(1, min(max_size, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            examined += 1
            if examined > max_examined:
                return None
            total = sum((c.net.amount for c in combo), Decimal("0"))
            if total == target:
                solutions.append(combo)
                if len(solutions) >= 2:
                    return solutions  # enough to know it's ambiguous; stop early
    return solutions
