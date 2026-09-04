"""Enum types, mirroring Schema §5.3 exactly.

Kept as plain `str` Enums (not `StrEnum`) for compatibility with SQLAlchemy's `Enum` column
type across the supported Python range, and so JSON serialisation is automatic.
"""

from __future__ import annotations

import enum


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    awaiting_review = "awaiting_review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class SourceType(str, enum.Enum):
    orders = "orders"
    gateway_settlement = "gateway_settlement"
    bank_statement = "bank_statement"


class MappingMethod(str, enum.Enum):
    deterministic = "deterministic"
    cached = "cached"
    llm = "llm"
    human = "human"


class LineType(str, enum.Enum):
    payment = "payment"
    refund = "refund"
    chargeback = "chargeback"
    adjustment = "adjustment"
    reversal = "reversal"


class MatchTier(str, enum.Enum):
    T1_PAYMENT_ID = "T1_PAYMENT_ID"
    T2_UTR = "T2_UTR"
    T3_ALLOCATION = "T3_ALLOCATION"
    T4_FEE = "T4_FEE"


class MatchStatus(str, enum.Enum):
    auto_confirmed = "auto_confirmed"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class EntityType(str, enum.Enum):
    order = "order"
    settlement_line = "settlement_line"
    bank_txn = "bank_txn"


class ExceptionCategory(str, enum.Enum):
    missing_in_bank = "missing_in_bank"
    missing_in_gateway = "missing_in_gateway"
    orphan_bank_credit = "orphan_bank_credit"
    amount_mismatch = "amount_mismatch"
    fee_variance = "fee_variance"
    duplicate_utr = "duplicate_utr"
    partial_settlement = "partial_settlement"
    period_boundary_timing = "period_boundary_timing"
    netted_refund_unlinked = "netted_refund_unlinked"
    chargeback_debit_unlinked = "chargeback_debit_unlinked"
    unknown_adjustment = "unknown_adjustment"
    ambiguous_multi_candidate = "ambiguous_multi_candidate"


class Severity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ProposedAction(str, enum.Enum):
    propose_match = "propose_match"
    propose_split_allocation = "propose_split_allocation"
    flag_fee_variance = "flag_fee_variance"
    flag_missing_in_bank = "flag_missing_in_bank"
    request_more_data = "request_more_data"
    escalate_to_human = "escalate_to_human"


class ExceptionStatus(str, enum.Enum):
    open = "open"
    approved = "approved"
    rejected = "rejected"
    escalated = "escalated"
    auto_resolved = "auto_resolved"


class LLMPurpose(str, enum.Enum):
    schema_map = "schema_map"
    triage = "triage"
    explain = "explain"


class Instrument(str, enum.Enum):
    upi = "upi"
    card_debit = "card_debit"
    card_credit = "card_credit"
    netbanking = "netbanking"
    wallet = "wallet"
    emi = "emi"
