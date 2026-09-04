"""SQLAlchemy 2.0 typed models — part 5: assurance and evaluation tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from milaan.adapters.db.base import Base, uuid_pk
from milaan.adapters.db.enums import EntityType, LLMPurpose


class LLMCall(Base):
    """Every AI decision traceable to its exact prompt version and cost (Schema §5.4).
    Makes the AI layer inspectable — this table backs `GET /runs/{run_id}/llm-calls`.
    """

    __tablename__ = "llm_call"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[LLMPurpose] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_cached: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    validation_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """Append-only, hash-chained. `REVOKE UPDATE, DELETE` is granted to the application role
    in the Alembic migration — append-only is enforced by privilege, not convention
    (Schema §5.4). BIGSERIAL PK because the hash chain requires a strict monotonic sequence,
    the one deliberate exception to the UUID-PK convention (§5.1)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id"), nullable=True
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class GroundTruthLink(Base):
    """Synthetic evaluation only. Never consulted by the matching engine — only by the eval
    harness. The generator emits this; the matcher never sees it (Schema §5.4 note, and the
    reviewer's first sanity check per PDR persona P4)."""

    __tablename__ = "ground_truth_link"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type_a: Mapped[EntityType] = mapped_column(nullable=False)
    entity_id_a: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    entity_type_b: Mapped[EntityType] = mapped_column(nullable=False)
    entity_id_b: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    expected_allocated_amount: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    pathology: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvalMetric(Base):
    """Baseline and system metrics in the same shape — makes lift-over-baseline a query,
    not a manual calculation (Schema §5.4)."""

    __tablename__ = "eval_metric"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "metric_name", "is_baseline", name="uq_eval_metric_run_name_baseline"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
