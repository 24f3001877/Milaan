"""T1 — exact `payment_id` matching, settlement -> order (Implementation Plan §6.2, task 2.1).

Groups payment-type settlement lines by `payment_id` rather than matching one-line-at-a-time:
a partial settlement (Schema/pathology `partial_settlement`) puts two settlement lines
against one order, and C5's active-membership constraint forbids the same order entity
from sitting in two separate match groups. One group per `payment_id` is therefore not an
optimisation — it is required for correctness.

Only `line_type == 'payment'` lines are eligible: refunds, chargebacks, and adjustments
don't tie 1:1 to an order via payment_id in this model (PDR §1.1's netting behaviour is
exactly why they need separate handling downstream, not a T1 job).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from milaan.domain.entities import OrderEntity, SettlementLineEntity
from milaan.domain.matching.types import MatchGroupResult, MatchMemberResult

RULE_ID = "T1-payment_id-exact"


@dataclass
class T1Result:
    groups: list[MatchGroupResult] = field(default_factory=list)
    matched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    unmatched_settlement_line_ids: set[uuid.UUID] = field(default_factory=set)
    ambiguous_payment_ids: set[str] = field(default_factory=set)


def match_t1(orders: list[OrderEntity], settlement_lines: list[SettlementLineEntity]) -> T1Result:
    result = T1Result()

    # Index orders by payment_id; a payment_id claimed by >1 order is a data problem —
    # exact-ID matching must refuse rather than guess which order is correct (PDR's
    # "refusal is a first-class outcome").
    orders_by_payment_id: dict[str, list[OrderEntity]] = {}
    for order in orders:
        if order.payment_id:
            orders_by_payment_id.setdefault(order.payment_id, []).append(order)

    payment_lines = [sl for sl in settlement_lines if sl.line_type == "payment"]
    lines_by_payment_id: dict[str, list[SettlementLineEntity]] = {}
    for line in payment_lines:
        if line.payment_id:
            lines_by_payment_id.setdefault(line.payment_id, []).append(line)
        else:
            result.unmatched_settlement_line_ids.add(line.id)  # malformed/missing payment_id

    # Iterate in sorted key order — determinism (C2): no dict/set iteration-order dependence
    # in what gets written out downstream.
    for payment_id in sorted(lines_by_payment_id):
        lines = lines_by_payment_id[payment_id]
        candidate_orders = orders_by_payment_id.get(payment_id, [])

        if len(candidate_orders) != 1:
            result.ambiguous_payment_ids.add(payment_id)
            result.unmatched_settlement_line_ids.update(line.id for line in lines)
            continue

        order = candidate_orders[0]
        group = MatchGroupResult(
            tier="T1_PAYMENT_ID", confidence=Decimal("1.0000"), rule_id=RULE_ID
        )
        group.add_member(
            MatchMemberResult(entity_type="order", entity_id=order.id, allocated_amount=order.gross)
        )
        for line in sorted(lines, key=lambda settlement_line: settlement_line.settlement_id):
            group.add_member(
                MatchMemberResult(
                    entity_type="settlement_line", entity_id=line.id, allocated_amount=line.gross
                )
            )
            result.matched_settlement_line_ids.add(line.id)
        result.groups.append(group)

    return result
