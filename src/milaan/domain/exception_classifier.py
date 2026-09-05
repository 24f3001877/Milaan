"""Exception classification — the 12-category taxonomy (PDR F4, Schema §5.3
`exception_category`, Implementation Plan §6.2 task 2.5).

Deterministic and rule-based: every classification is derived from evidence actually
observable on the entity and the cascade's own tier-by-tier results (line_type, presence
of identifiers, dates vs. period boundaries, T2's ambiguous-UTR set) — never from the
synthetic generator's injected pathology label, which a real system could never see. The
cross-validation tests in this module's test file compare classifier output against that
label anyway, but only as an external check, not as an input to the logic.

Covers two distinct situations, both surfaced as exceptions:
  1. Entities the cascade left completely unmatched (9 of the 12 categories).
  2. Entities that DID match, but still carry something worth an analyst's attention —
     `fee_variance`, `amount_mismatch`, `partial_settlement` (the schema explicitly allows
     `exception_item.resolved_match_group_id` to be populated, meaning an exception can
     coexist with an already-formed match group).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.fee_verification import FeeVarianceRecord
from milaan.domain.matching.cascade import CascadeResult
from milaan.domain.matching.utr_extraction import extract_utr, normalize_utr
from milaan.domain.money import Money

DEFAULT_HIGH_THRESHOLD = Money("10000.00")
DEFAULT_CRITICAL_THRESHOLD = Money("100000.00")
DEFAULT_AMOUNT_MISMATCH_TOLERANCE = Money("0.01")

# Non-payment line_type -> category is a direct, reliable signal (no inference needed):
# these lines carry no order reference by construction (Schema §5.4).
_STRAY_LINE_TYPE_CATEGORY = {
    "refund": "netted_refund_unlinked",
    "chargeback": "chargeback_debit_unlinked",
    "adjustment": "unknown_adjustment",
    "reversal": "unknown_adjustment",
}


@dataclass(frozen=True, slots=True)
class ExceptionRecord:
    category: str
    severity: str  # "low" | "medium" | "high" | "critical"
    entity_type: str
    entity_id: uuid.UUID
    amount_at_risk: Money
    deterministic_trace: dict
    resolved_match_group_index: int | None = None  # set when the entity DID match


def _abs_money(m: Money) -> Money:
    return Money(abs(m.amount))


def _severity_for_amount(amount: Money) -> str:
    if amount.amount >= DEFAULT_CRITICAL_THRESHOLD.amount:
        return "critical"
    if amount.amount >= DEFAULT_HIGH_THRESHOLD.amount:
        return "high"
    if amount.amount > Decimal("0"):
        return "medium"
    return "low"


def classify_exceptions(
    orders: list[OrderEntity],
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
    cascade: CascadeResult,
    fee_variance_records: list[FeeVarianceRecord],
    period_start: date,
    period_end: date,
    amount_mismatch_tolerance: Money = DEFAULT_AMOUNT_MISMATCH_TOLERANCE,
) -> list[ExceptionRecord]:
    records: list[ExceptionRecord] = []
    settlement_by_id = {sl.id: sl for sl in settlement_lines}
    order_by_id = {o.id: o for o in orders}

    # --- 1. Settlement lines: classify every line by its actual final state, not just
    # the fully-unmatched ones. A line can be "matched" (belongs to some group, so it
    # doesn't appear in cascade.unmatched_settlement_line_ids) while still missing a
    # bank tie (missing_in_bank), missing an order tie (ambiguous_multi_candidate), or
    # simply being a stray non-payment line that structurally never gets an order
    # reference — all of which still deserve an exception record.
    line_to_group_idx: dict = {}
    for idx, g in enumerate(cascade.groups):
        for sid in g.member_ids("settlement_line"):
            line_to_group_idx[sid] = idx

    for line in sorted(settlement_lines, key=lambda settlement_line: settlement_line.settlement_id):
        group = cascade.groups[line_to_group_idx[line.id]] if line.id in line_to_group_idx else None
        # Precisely "does THIS line's own payment_id match an order in the group" — not
        # "does the group contain any order at all". The latter is wrong the same way the
        # scoring cross-product bug was wrong: a merged multi-order group (many settlement
        # lines, many orders, one bank credit) has orders in it that belong to OTHER
        # lines, and a naive group-level check would wrongly treat an unrelated line
        # (e.g. one with a blanked payment_id) as order-resolved just because its
        # groupmates happen to have orders.
        has_own_order_tie = False
        if group is not None and line.payment_id:
            has_own_order_tie = any(
                order_by_id[oid].payment_id == line.payment_id for oid in group.member_ids("order")
            )
        category, trace = _classify_settlement_line(
            line, group, has_own_order_tie, period_end, cascade.ambiguous_utrs
        )
        if category is None:
            continue
        amount = _abs_money(line.net)
        records.append(
            ExceptionRecord(
                category=category,
                severity=_severity_for_amount(amount),
                entity_type="settlement_line",
                entity_id=line.id,
                amount_at_risk=amount,
                deterministic_trace=trace,
                resolved_match_group_index=line_to_group_idx.get(line.id),
            )
        )

    # --- 2. Unmatched bank credits ------------------------------------------------------
    for bank_id in sorted(cascade.unmatched_bank_txn_ids, key=str):
        bank = next(b for b in bank_txns if b.id == bank_id)
        category, trace = _classify_unmatched_bank_txn(bank, cascade.ambiguous_utrs)
        amount = _abs_money(bank.credit)
        records.append(
            ExceptionRecord(
                category=category,
                severity=_severity_for_amount(amount),
                entity_type="bank_txn",
                entity_id=bank.id,
                amount_at_risk=amount,
                deterministic_trace=trace,
            )
        )

    # --- 3. Orders never referenced by any matched settlement line ---------------------
    matched_order_ids = {eid for g in cascade.groups for eid in g.member_ids("order")}
    for order in sorted(orders, key=lambda o: o.order_id):
        if order.id in matched_order_ids:
            continue
        trace = {
            "tiers_attempted": ["T1"],
            "reason": "no settlement line references this order's payment_id",
        }
        amount = _abs_money(order.gross)
        records.append(
            ExceptionRecord(
                category="missing_in_gateway",
                severity=_severity_for_amount(amount),
                entity_type="order",
                entity_id=order.id,
                amount_at_risk=amount,
                deterministic_trace=trace,
            )
        )

    # --- 4. Fee variance beyond tolerance (already computed by T4) ---------------------
    for r in fee_variance_records:
        if r.within_tolerance:
            continue
        amount = _abs_money(r.delta)
        trace = {
            "tiers_attempted": ["T4"],
            "expected_fee": r.expected_fee.to_json(),
            "expected_tax": r.expected_tax.to_json(),
            "reported_fee": r.reported_fee.to_json(),
            "reported_tax": r.reported_tax.to_json(),
            "rate_card_version": r.rate_card_version,
            "instrument_resolved": r.instrument_resolved,
        }
        records.append(
            ExceptionRecord(
                category="fee_variance",
                severity=_severity_for_amount(amount),
                entity_type="settlement_line",
                entity_id=r.settlement_line_id,
                amount_at_risk=amount,
                deterministic_trace=trace,
            )
        )

    # --- 5. Amount mismatch and partial settlement — checks on MATCHED groups ----------
    # Per-order, via payment_id pairing within the group — NOT "if the group has exactly
    # one order", which silently skipped every order caught up in a merged many-orders-
    # one-credit group (the ordinary, common case once T2 merges a day's T1 groups
    # together). Same root cause as the order-tie fix above and the scoring.py
    # cross-product fix: a group-level shortcut that was only valid for the simple
    # single-order case silently failed to generalise once groups started merging.
    for idx, g in enumerate(cascade.groups):
        order_ids = g.member_ids("order")
        settlement_ids = g.member_ids("settlement_line")
        if not order_ids or not settlement_ids:
            continue

        lines_by_payment_id: dict[str, list] = {}
        for sid in settlement_ids:
            line = settlement_by_id[sid]
            if line.payment_id:
                lines_by_payment_id.setdefault(line.payment_id, []).append(line)

        for oid in sorted(order_ids, key=str):
            order = order_by_id[oid]
            if not order.payment_id:
                continue
            own_lines = sorted(
                lines_by_payment_id.get(order.payment_id, []),
                key=lambda settlement_line: settlement_line.settlement_id,
            )
            if not own_lines:
                continue  # this order's group membership came from a merge, not its own tie

            settlement_gross_sum = Money.sum([line.gross for line in own_lines])
            delta = Money(abs(order.gross.amount - settlement_gross_sum.amount))
            if delta.amount > amount_mismatch_tolerance.amount:
                # Attached to the settlement line, not the order — matches how the
                # generator's own ground truth models the pathology.
                offending_line = own_lines[0]
                trace = {
                    "order_gross": order.gross.to_json(),
                    "settlement_gross_sum": settlement_gross_sum.to_json(),
                }
                records.append(
                    ExceptionRecord(
                        category="amount_mismatch",
                        severity=_severity_for_amount(delta),
                        entity_type="settlement_line",
                        entity_id=offending_line.id,
                        amount_at_risk=delta,
                        deterministic_trace=trace,
                        resolved_match_group_index=idx,
                    )
                )

            if len(own_lines) > 1:
                # ONE order tied to several settlement lines — a genuine partial
                # settlement. One record per settlement line, matching the generator's
                # own ground truth, which tags every split line individually.
                for line in own_lines:
                    trace = {"settlement_line_count": len(own_lines)}
                    records.append(
                        ExceptionRecord(
                            category="partial_settlement",
                            severity="low",
                            entity_type="settlement_line",
                            entity_id=line.id,
                            amount_at_risk=Money.zero(),
                            deterministic_trace=trace,
                            resolved_match_group_index=idx,
                        )
                    )

    return records


def _classify_settlement_line(
    line: SettlementLineEntity,
    group: object | None,  # MatchGroupResult | None, kept loosely typed to avoid a cycle
    has_own_order_tie: bool,
    period_end: date,
    ambiguous_utrs: set[str],
) -> tuple[str | None, dict]:
    """Comprehensive per-line classification, covering both fully-unmatched lines and
    lines that belong to a group but are still missing an order or bank tie. Returns
    (None, {}) when the line is fully resolved (order tie AND bank tie, or not
    line_type=='payment' and correctly excluded — see below)."""
    trace: dict = {"line_type": line.line_type}

    if line.line_type != "payment":
        # Stray lines never carry an order reference by construction — this is a standing
        # fact about the line, not a matching failure, so it's always flagged regardless
        # of whether T2 found a bank tie for it.
        trace["reason"] = f"{line.line_type} line carries no order reference by construction"
        return _STRAY_LINE_TYPE_CATEGORY[line.line_type], trace

    if line.settled_on > period_end:
        trace["reason"] = f"settled_on {line.settled_on} falls after period_end {period_end}"
        return "period_boundary_timing", trace

    has_bank_tie = group is not None and bool(group.member_ids("bank_txn"))  # type: ignore[union-attr]

    if has_own_order_tie and has_bank_tie:
        return None, {}  # fully resolved

    if not line.payment_id:
        trace["reason"] = "payment_id missing or malformed; cannot resolve to a single order"
        return "ambiguous_multi_candidate", trace

    if not has_own_order_tie:
        # Has a payment_id but T1 still couldn't place it — most likely payment_id
        # collided across orders (T1's ambiguous_payment_ids case).
        trace["reason"] = "payment_id present but no single order could be resolved"
        return "ambiguous_multi_candidate", trace

    # has_own_order_tie is True, has_bank_tie is False from here on.
    normalized = normalize_utr(line.utr) if line.utr else None
    if normalized and normalized in ambiguous_utrs:
        trace["reason"] = f"utr {normalized} collides with another bank credit's utr"
        return "duplicate_utr", trace

    trace["reason"] = "valid payment_id, but no bank credit ever covers this settlement"
    return "missing_in_bank", trace


def _classify_unmatched_bank_txn(bank: BankTxnEntity, ambiguous_utrs: set[str]) -> tuple[str, dict]:
    trace: dict = {"tiers_attempted": ["T2", "T3"]}
    utr = extract_utr(bank.narration, bank.utr_extracted)
    normalized = normalize_utr(utr) if utr else None
    if normalized and normalized in ambiguous_utrs:
        trace["reason"] = f"utr {normalized} collides with another bank credit's utr"
        return "duplicate_utr", trace

    trace["reason"] = "no settlement line's utr or amount combination explains this credit"
    return "orphan_bank_credit", trace
