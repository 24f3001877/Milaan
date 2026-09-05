"""Golden set: ~40 hand-labelled exception scenarios (Implementation Plan §6.2, task 2.15).

Each item is a plausible exception with a human-assigned "correct" action, labelled by
reasoning about the scenario the way a finance analyst would, independent of and before
any model response. Used by eval/triage_eval.py to measure triage accuracy.

Honesty note: this build environment has no live LLM API key, so the harness in
triage_eval.py runs against a clearly-labelled deterministic placeholder responder rather
than genuine model judgments (see that module's docstring). The golden set, the scoring
harness, and the accuracy report are all fully real and ready to score actual `live`-mode
output the moment a key is available — what's missing here is the model call itself, not
the evaluation machinery.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from milaan.domain.money import Money


@dataclass(frozen=True)
class GoldenItem:
    item_id: str
    category: str
    entity_type: str
    amount_at_risk: Money
    deterministic_trace: dict
    record_fields: dict
    valid_record_ids: set
    expected_action: str
    notes: str = ""


def _item(category, entity_type, amount, trace, fields, expected_action, notes=""):
    rid = (
        fields.get("settlement_id")
        or fields.get("order_id")
        or fields.get("narration")
        or uuid.uuid4().hex[:8]
    )
    return GoldenItem(
        item_id=f"{category}-{uuid.uuid4().hex[:6]}",
        category=category,
        entity_type=entity_type,
        amount_at_risk=Money(amount),
        deterministic_trace=trace,
        record_fields=fields,
        valid_record_ids={rid},
        expected_action=expected_action,
        notes=notes,
    )


GOLDEN_SET = [
    _item(
        "missing_in_bank",
        "settlement_line",
        "500.00",
        {"reason": "valid payment_id, but no bank credit ever covers this settlement"},
        {"settlement_id": "STL0001", "payment_id": "pay0001"},
        "flag_missing_in_bank",
    ),
    _item(
        "missing_in_bank",
        "settlement_line",
        "12500.00",
        {"reason": "valid payment_id, but no bank credit ever covers this settlement"},
        {"settlement_id": "STL0002", "payment_id": "pay0002"},
        "flag_missing_in_bank",
    ),
    _item(
        "missing_in_bank",
        "settlement_line",
        "80.00",
        {"reason": "valid payment_id, but no bank credit ever covers this settlement"},
        {"settlement_id": "STL0003", "payment_id": "pay0003"},
        "flag_missing_in_bank",
    ),
    _item(
        "missing_in_bank",
        "settlement_line",
        "3400.00",
        {"reason": "valid payment_id, but no bank credit ever covers this settlement"},
        {"settlement_id": "STL0004", "payment_id": "pay0004"},
        "flag_missing_in_bank",
    ),
    _item(
        "missing_in_gateway",
        "order",
        "2200.00",
        {"reason": "no settlement line references this order's payment_id"},
        {"order_id": "ORD0001", "payment_id": "pay0005"},
        "request_more_data",
    ),
    _item(
        "missing_in_gateway",
        "order",
        "150.00",
        {"reason": "no settlement line references this order's payment_id"},
        {"order_id": "ORD0002", "payment_id": "pay0006"},
        "request_more_data",
    ),
    _item(
        "missing_in_gateway",
        "order",
        "9800.00",
        {"reason": "no settlement line references this order's payment_id"},
        {"order_id": "ORD0003", "payment_id": "pay0007"},
        "request_more_data",
    ),
    _item(
        "missing_in_gateway",
        "order",
        "40.00",
        {"reason": "no settlement line references this order's payment_id"},
        {"order_id": "ORD0004", "payment_id": "pay0008"},
        "request_more_data",
    ),
    _item(
        "orphan_bank_credit",
        "bank_txn",
        "1000.00",
        {"reason": "no settlement line's utr or amount combination explains this credit"},
        {"narration": "NEFT CR UNKNOWN ORPHAN00001"},
        "request_more_data",
    ),
    _item(
        "orphan_bank_credit",
        "bank_txn",
        "55000.00",
        {"reason": "no settlement line's utr or amount combination explains this credit"},
        {"narration": "NEFT CR UNKNOWN ORPHAN00002"},
        "escalate_to_human",
        "large unexplained credit warrants a human look, not just a data request",
    ),
    _item(
        "orphan_bank_credit",
        "bank_txn",
        "300.00",
        {"reason": "no settlement line's utr or amount combination explains this credit"},
        {"narration": "NEFT CR UNKNOWN ORPHAN00003"},
        "request_more_data",
    ),
    _item(
        "orphan_bank_credit",
        "bank_txn",
        "7200.00",
        {"reason": "no settlement line's utr or amount combination explains this credit"},
        {"narration": "NEFT CR UNKNOWN ORPHAN00004"},
        "request_more_data",
    ),
    _item(
        "amount_mismatch",
        "settlement_line",
        "20.00",
        {"order_gross": "100.00", "settlement_gross_sum": "120.00"},
        {"settlement_id": "STL0005"},
        "escalate_to_human",
    ),
    _item(
        "amount_mismatch",
        "settlement_line",
        "500.00",
        {"order_gross": "5000.00", "settlement_gross_sum": "5500.00"},
        {"settlement_id": "STL0006"},
        "escalate_to_human",
    ),
    _item(
        "amount_mismatch",
        "settlement_line",
        "2.00",
        {"order_gross": "200.00", "settlement_gross_sum": "202.00"},
        {"settlement_id": "STL0007"},
        "escalate_to_human",
    ),
    _item(
        "fee_variance",
        "settlement_line",
        "5.00",
        {"expected_fee": "20.00", "reported_fee": "25.00"},
        {"settlement_id": "STL0008", "instrument_resolved": "upi"},
        "flag_fee_variance",
    ),
    _item(
        "fee_variance",
        "settlement_line",
        "120.00",
        {"expected_fee": "200.00", "reported_fee": "320.00"},
        {"settlement_id": "STL0009", "instrument_resolved": "card_debit"},
        "flag_fee_variance",
    ),
    _item(
        "fee_variance",
        "settlement_line",
        "0.50",
        {"expected_fee": "10.00", "reported_fee": "10.50"},
        {"settlement_id": "STL0010", "instrument_resolved": "wallet"},
        "flag_fee_variance",
    ),
    _item(
        "fee_variance",
        "settlement_line",
        "40.00",
        {"expected_fee": "50.00", "reported_fee": "90.00"},
        {"settlement_id": "STL0011", "instrument_resolved": "netbanking"},
        "flag_fee_variance",
    ),
    _item(
        "duplicate_utr",
        "settlement_line",
        "97.00",
        {"reason": "utr collides with another bank credit's utr"},
        {"settlement_id": "STL0012", "utr": "UTR-DUP-1"},
        "escalate_to_human",
    ),
    _item(
        "duplicate_utr",
        "bank_txn",
        "5000.00",
        {"reason": "utr collides with another bank credit's utr"},
        {"narration": "NEFT CR UTR-DUP-2"},
        "escalate_to_human",
    ),
    _item(
        "duplicate_utr",
        "settlement_line",
        "300.00",
        {"reason": "utr collides with another bank credit's utr"},
        {"settlement_id": "STL0013", "utr": "UTR-DUP-3"},
        "escalate_to_human",
    ),
    _item(
        "partial_settlement",
        "settlement_line",
        "0.00",
        {"settlement_line_count": 2},
        {"settlement_id": "STL0014A", "order_id": "ORD0005"},
        "propose_split_allocation",
    ),
    _item(
        "partial_settlement",
        "settlement_line",
        "0.00",
        {"settlement_line_count": 3},
        {"settlement_id": "STL0015A", "order_id": "ORD0006"},
        "propose_split_allocation",
    ),
    _item(
        "partial_settlement",
        "settlement_line",
        "0.00",
        {"settlement_line_count": 2},
        {"settlement_id": "STL0016A", "order_id": "ORD0007"},
        "propose_split_allocation",
    ),
    _item(
        "period_boundary_timing",
        "settlement_line",
        "600.00",
        {"reason": "settled_on falls after period_end"},
        {"settlement_id": "STL0017", "settled_on": "2026-02-02"},
        "request_more_data",
    ),
    _item(
        "period_boundary_timing",
        "settlement_line",
        "1500.00",
        {"reason": "settled_on falls after period_end"},
        {"settlement_id": "STL0018", "settled_on": "2026-02-01"},
        "request_more_data",
    ),
    _item(
        "period_boundary_timing",
        "settlement_line",
        "85.00",
        {"reason": "settled_on falls after period_end"},
        {"settlement_id": "STL0019", "settled_on": "2026-02-03"},
        "request_more_data",
    ),
    _item(
        "netted_refund_unlinked",
        "settlement_line",
        "-50.00",
        {"reason": "refund line carries no order reference by construction"},
        {"settlement_id": "STL0020X", "line_type": "refund"},
        "escalate_to_human",
    ),
    _item(
        "netted_refund_unlinked",
        "settlement_line",
        "-1200.00",
        {"reason": "refund line carries no order reference by construction"},
        {"settlement_id": "STL0021X", "line_type": "refund"},
        "escalate_to_human",
    ),
    _item(
        "netted_refund_unlinked",
        "settlement_line",
        "-30.00",
        {"reason": "refund line carries no order reference by construction"},
        {"settlement_id": "STL0022X", "line_type": "refund"},
        "escalate_to_human",
    ),
    _item(
        "chargeback_debit_unlinked",
        "settlement_line",
        "-800.00",
        {"reason": "chargeback line carries no order reference by construction"},
        {"settlement_id": "STL0023X", "line_type": "chargeback"},
        "escalate_to_human",
    ),
    _item(
        "chargeback_debit_unlinked",
        "settlement_line",
        "-45.00",
        {"reason": "chargeback line carries no order reference by construction"},
        {"settlement_id": "STL0024X", "line_type": "chargeback"},
        "escalate_to_human",
    ),
    _item(
        "chargeback_debit_unlinked",
        "settlement_line",
        "-2000.00",
        {"reason": "chargeback line carries no order reference by construction"},
        {"settlement_id": "STL0025X", "line_type": "chargeback"},
        "escalate_to_human",
    ),
    _item(
        "unknown_adjustment",
        "settlement_line",
        "15.00",
        {"reason": "adjustment line carries no order reference by construction"},
        {"settlement_id": "STL0026X", "line_type": "adjustment"},
        "escalate_to_human",
    ),
    _item(
        "unknown_adjustment",
        "settlement_line",
        "300.00",
        {"reason": "adjustment line carries no order reference by construction"},
        {"settlement_id": "STL0027X", "line_type": "adjustment"},
        "escalate_to_human",
    ),
    _item(
        "unknown_adjustment",
        "settlement_line",
        "5.00",
        {"reason": "adjustment line carries no order reference by construction"},
        {"settlement_id": "STL0028X", "line_type": "adjustment"},
        "escalate_to_human",
    ),
    _item(
        "ambiguous_multi_candidate",
        "settlement_line",
        "250.00",
        {"reason": "payment_id missing or malformed"},
        {"settlement_id": "STL0029"},
        "escalate_to_human",
    ),
    _item(
        "ambiguous_multi_candidate",
        "settlement_line",
        "40.00",
        {"reason": "payment_id missing or malformed"},
        {"settlement_id": "STL0030"},
        "escalate_to_human",
    ),
    _item(
        "ambiguous_multi_candidate",
        "settlement_line",
        "6000.00",
        {"reason": "payment_id missing or malformed"},
        {"settlement_id": "STL0031"},
        "escalate_to_human",
    ),
]
