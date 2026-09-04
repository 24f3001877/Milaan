"""SQLAlchemy 2.0 typed models — part 1: run isolation, schema mapping, source files.

Every table follows Schema §5.1 conventions: UUID PKs (BIGSERIAL only for audit_log),
TIMESTAMPTZ, NUMERIC(20,4) for money (never float/real/double), row_hash idempotency keys,
no soft deletes. See §5.4 for the authoritative column-by-column spec this mirrors.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from milaan.adapters.db.base import Base, uuid_pk
from milaan.adapters.db.enums import MappingMethod, RunStatus, SourceType


class ReconRun(Base):
    """The isolation and idempotency unit. A run is immutable once complete (Schema §5.1)."""

    __tablename__ = "recon_run"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="period_end_after_start"),
        UniqueConstraint(
            "input_manifest_hash", "ruleset_version", name="uq_recon_run_manifest_ruleset"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        default=RunStatus.queued, nullable=False, server_default=RunStatus.queued.value
    )
    orchestrator_state: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(
        String, ForeignKey("ruleset.version"), nullable=False
    )
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    rng_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_manifest_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_mode: Mapped[str] = mapped_column(String, nullable=False)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="operator")

    source_files: Mapped[list["DataSourceFile"]] = relationship(back_populates="run")


class SchemaMapping(Base):
    """Learned source layouts — a renamed column costs a confirmation click, not a deploy."""

    __tablename__ = "schema_mapping"
    __table_args__ = (
        UniqueConstraint(
            "source_type", "header_fingerprint", name="uq_schema_mapping_source_fingerprint"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_type: Mapped[SourceType] = mapped_column(nullable=False)
    header_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False)
    field_confidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    method: Mapped[MappingMethod] = mapped_column(nullable=False)
    confirmed_by_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DataSourceFile(Base):
    """Exactly one file per source per run (Schema §5.4)."""

    __tablename__ = "data_source_file"
    __table_args__ = (UniqueConstraint("run_id", "source_type", name="uq_data_source_file_run_source"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recon_run.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("schema_mapping.id"), nullable=True
    )
    mapping_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[ReconRun] = relationship(back_populates="source_files")
