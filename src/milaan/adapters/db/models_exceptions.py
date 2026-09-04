"""SQLAlchemy 2.0 typed models — part 4: exceptions, config, fee variance."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from milaan.adapters.db.base import Base, uuid_pk
from milaan.adapters.db.enums import (
    EntityType,
    ExceptionCategory,
    ExceptionStatus,
    Instrument,
    ProposedAction,
    Severity,
)


class ExceptionItem(Base):
    """Nullable `proposed_action` IS the refusal path in the data model (Schema §5.4):
    no proposal means the agent declined to guess rather than emitting a low-confidence one.
    """

    __tablename__ = "exception_item"
    __table_args__ = (
        CheckConstraint(
            "status <> 'rejected' OR reject_reason_code IS NOT NULL",
            name="rejected_requires_reason_code",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[ExceptionCategory] = mapped_column(nullable=False)
    severity: Mapped[Severity] = mapped_column(nullable=False)
    entity_type: Mapped[EntityType] = mapped_column(nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    amount_at_risk: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    deterministic_trace: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidates: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[ProposedAction | None] = mapped_column(nullable=True)
    action_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("llm_call.id"), nullable=True
    )
    status: Mapped[ExceptionStatus] = mapped_column(
        nullable=False, default=ExceptionStatus.open, server_default=ExceptionStatus.open.value
    )
    reject_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_match_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("match_group.id"), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RateCard(Base):
    """Versioned, never mutated. Basis points as integers so tier boundaries are exact."""

    __tablename__ = "rate_card"
    __table_args__ = (
        UniqueConstraint(
            "version", "instrument", "min_amount", name="uq_rate_card_version_instrument_min"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[str] = mapped_column(Text, nullable=False)
    instrument: Mapped[Instrument] = mapped_column(nullable=False)
    min_amount: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    max_amount: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    percent_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    flat_fee: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=0)
    tax_percent_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class Ruleset(Base):
    """Tolerances, date windows, confidence thresholds, allocation caps. Immutable once
    referenced by a run — a historical run must remain explainable with the rules in force
    at the time (PDR user story, Ingest and setup)."""

    __tablename__ = "ruleset"

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeeVariance(Base):
    __tablename__ = "fee_variance"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    settlement_line_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("settlement_line.id"), nullable=False, unique=True
    )
    expected_fee: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    expected_tax: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    reported_fee: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    reported_tax: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    rate_card_version: Mapped[str] = mapped_column(Text, nullable=False)
    within_tolerance: Mapped[bool] = mapped_column(nullable=False)
    instrument_resolved: Mapped[Instrument | None] = mapped_column(nullable=True)
