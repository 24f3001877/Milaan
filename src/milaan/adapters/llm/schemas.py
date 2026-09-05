"""Pydantic response schemas for every LLM interaction (Implementation Plan §6.2, tasks
2.10-2.13).

C3 (TRD §2.3): "The LLM never produces amounts. It emits record IDs, enum values,
confidences and prose. The engine recomputes every number." Enforced here structurally —
none of these schemas has a monetary field, so an LLM response literally cannot carry a
number the engine would trust as money. A schema-validated response is the only kind these
adapters ever accept; validation failure triggers the client's retry-then-escalate path
(TRD §2.4), never a best-effort parse.

These schemas — plus the fixed `proposed_action` enum — are also the second half of the
prompt-injection defence (TRD §2.4, task 2.14): even if adversarial text in a narration
field somehow influenced a response, the response can only ever be one of these
structurally limited shapes. It cannot express "match everything" or emit an amount,
because the schema has no field for either.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Mirrors Schema §5.3's exception_category and proposed_action enums exactly — the LLM's
# vocabulary is a closed set, not free text.
EXCEPTION_CATEGORIES = (
    "missing_in_bank",
    "missing_in_gateway",
    "orphan_bank_credit",
    "amount_mismatch",
    "fee_variance",
    "duplicate_utr",
    "partial_settlement",
    "period_boundary_timing",
    "netted_refund_unlinked",
    "chargeback_debit_unlinked",
    "unknown_adjustment",
    "ambiguous_multi_candidate",
)
PROPOSED_ACTIONS = (
    "propose_match",
    "propose_split_allocation",
    "flag_fee_variance",
    "flag_missing_in_bank",
    "request_more_data",
    "escalate_to_human",
)


class SchemaFieldMapping(BaseModel):
    """One proposed source-column -> canonical-field mapping, with the model's own
    confidence in that specific field (UI/UX §3.3 S3: per-field confidence, not a single
    blended number)."""

    source_column: str
    canonical_field: str
    confidence: float = Field(ge=0.0, le=1.0)


class SchemaMappingProposal(BaseModel):
    """Response shape for LLM-proposed schema mapping (task 2.11). No monetary or record
    data — only column-name-to-field-name proposals."""

    mappings: list[SchemaFieldMapping]
    unmapped_columns: list[str] = Field(default_factory=list)
    reasoning: str = Field(max_length=1000)


class TriageProposal(BaseModel):
    """Response shape for LLM exception triage (task 2.12). Adds a hypothesis and
    proposed action ON TOP of the category the deterministic classifier already assigned
    (PDR F4) — it never assigns the category itself, and it never carries an amount.

    `referenced_record_ids` lets the caller verify every ID the model cites actually
    exists in the current run before trusting the rationale (TRD §2.4: "Referenced record
    IDs must exist in the current run or the proposal is rejected")."""

    hypothesis: str = Field(max_length=500)
    proposed_action: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=1000)
    referenced_record_ids: list[str] = Field(default_factory=list)

    @field_validator("proposed_action")
    @classmethod
    def action_must_be_in_enum(cls, v: str) -> str:
        if v not in PROPOSED_ACTIONS:
            raise ValueError(f"proposed_action {v!r} not in {PROPOSED_ACTIONS}")
        return v


class ExplanationResponse(BaseModel):
    """Response shape for plain-language explanation generation (task 2.13) — prose only,
    for the review drawer's AI-assessment panel (UI/UX §3.3 S6b)."""

    explanation: str = Field(max_length=800)
