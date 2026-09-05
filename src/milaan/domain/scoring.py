"""Scoring — compares predicted match groups against authored ground truth
(Implementation Plan §6.2, task 2.7). Pure domain logic: operates on already-resolved
UUID-keyed links, never touches a file or a database (that's eval/ground_truth.py's job).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from milaan.domain.matching.types import MatchGroupResult
from milaan.domain.money import Money

# Canonical pairwise link: (type_a, id_a, type_b, id_b), with (type_a, id_a) <= (type_b, id_b)
# so a link is always represented the same way regardless of which side it was built from.
LinkKey = tuple[str, uuid.UUID, str, uuid.UUID]


def canonical_link_key(type_a: str, id_a: uuid.UUID, type_b: str, id_b: uuid.UUID) -> LinkKey:
    a, b = (type_a, id_a), (type_b, id_b)
    return (a[0], a[1], b[0], b[1]) if a <= b else (b[0], b[1], a[0], a[1])


def links_from_groups(
    groups: list[MatchGroupResult],
    order_payment_id: dict[uuid.UUID, str | None] | None = None,
    settlement_payment_id: dict[uuid.UUID, str | None] | None = None,
) -> set[LinkKey]:
    """Flattens each match group into its implied pairwise links — order<->settlement and
    settlement<->bank — the same shape the synthetic generator's ground_truth.jsonl uses,
    so the two are directly comparable.

    order_payment_id/settlement_payment_id enable precise order<->settlement pairing by
    matching `payment_id` within a group, rather than a blind cross-product of every order
    against every settlement line in the group. The blind cross-product is only correct
    for a group with at most one order; once T2 merges several T1 groups sharing one day's
    UTR (the ordinary many-orders-one-credit case), a group can contain many orders and
    many settlement lines together, and cross-producing them fabricates links that were
    never actually established — this was a real bug caught by comparing against ground
    truth at scale (a 5,000-record run produced ~715K "predicted" links against ~10K true
    ones). Settlement<->bank pairing has no equivalent issue: a group only ever has one
    bank_txn member, so that cross-product is always safe.
    """
    links: set[LinkKey] = set()
    for g in groups:
        orders = [m for m in g.members if m.entity_type == "order"]
        lines = [m for m in g.members if m.entity_type == "settlement_line"]
        banks = [m for m in g.members if m.entity_type == "bank_txn"]

        if order_payment_id is not None and settlement_payment_id is not None:
            orders_by_pid: dict[str, list] = {}
            for o in orders:
                pid = order_payment_id.get(o.entity_id)
                if pid:
                    orders_by_pid.setdefault(pid, []).append(o)
            lines_by_pid: dict[str, list] = {}
            for line in lines:
                pid = settlement_payment_id.get(line.entity_id)
                if pid:
                    lines_by_pid.setdefault(pid, []).append(line)
            for pid, pid_orders in orders_by_pid.items():
                for o in pid_orders:
                    for line in lines_by_pid.get(pid, []):
                        links.add(
                            canonical_link_key(
                                "order", o.entity_id, "settlement_line", line.entity_id
                            )
                        )
        else:
            # Safe fallback only when groups have at most one order (true in unit tests
            # that don't exercise the multi-order merge case).
            for o in orders:
                for line in lines:
                    links.add(
                        canonical_link_key("order", o.entity_id, "settlement_line", line.entity_id)
                    )

        for line in lines:
            for b in banks:
                links.add(
                    canonical_link_key("settlement_line", line.entity_id, "bank_txn", b.entity_id)
                )
    return links


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total_settlement_lines: int
    matched_settlement_lines: int
    auto_match_rate: float
    total_links_predicted: int
    true_positive_links: int
    false_positive_links: int
    false_negative_links: int
    false_match_rate: float
    value_explained_pct: float
    human_touches_per_100: float


def score(
    predicted_groups: list[MatchGroupResult],
    true_links: set[LinkKey],
    total_settlement_lines: int,
    total_settlement_value: Money,
    matched_settlement_value: Money,
    exception_count: int,
    total_records: int,
    order_payment_id: dict[uuid.UUID, str | None] | None = None,
    settlement_payment_id: dict[uuid.UUID, str | None] | None = None,
) -> ScoreResult:
    predicted_links = links_from_groups(predicted_groups, order_payment_id, settlement_payment_id)
    true_positive = predicted_links & true_links
    false_positive = predicted_links - true_links
    false_negative = true_links - predicted_links

    matched_settlement_lines = len(
        {
            m.entity_id
            for g in predicted_groups
            for m in g.members
            if m.entity_type == "settlement_line"
        }
    )

    auto_match_rate = (
        matched_settlement_lines / total_settlement_lines if total_settlement_lines else 0.0
    )
    false_match_rate = len(false_positive) / len(predicted_links) if predicted_links else 0.0
    value_explained_pct = (
        float(matched_settlement_value.amount / total_settlement_value.amount)
        if total_settlement_value.amount != Decimal("0")
        else 0.0
    )
    human_touches_per_100 = (exception_count / total_records) * 100 if total_records else 0.0

    return ScoreResult(
        total_settlement_lines=total_settlement_lines,
        matched_settlement_lines=matched_settlement_lines,
        auto_match_rate=auto_match_rate,
        total_links_predicted=len(predicted_links),
        true_positive_links=len(true_positive),
        false_positive_links=len(false_positive),
        false_negative_links=len(false_negative),
        false_match_rate=false_match_rate,
        value_explained_pct=value_explained_pct,
        human_touches_per_100=human_touches_per_100,
    )
