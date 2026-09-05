"""SQLAlchemy 2.0 typed models — part 3: matching.

`MatchGroup` + `MatchMember` implements the many-to-many across three heterogeneous record
tables via a polymorphic member row (Schema §5.2). This is the central modelling decision:
a direct foreign-key design cannot express many-to-one settlement.

The two integrity constraints for C5 (no double allocation, allocation sums exact) are DDL,
not Python — they're added as raw SQL in the Alembic migration because SQLAlchemy's
declarative layer can't express a partial unique index with a subquery predicate or a
deferred constraint trigger. `run_id` is denormalised onto `match_member` deliberately, to
make the partial index possible (Schema §5.4 note).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from milaan.adapters.db.base import Base, uuid_pk
from milaan.adapters.db.enums import EntityType, MatchStatus, MatchTier


class MatchGroup(Base):
    __tablename__ = "match_group"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recon_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tier: Mapped[MatchTier] = mapped_column(nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_between_0_and_1"),
        nullable=False,
    )
    status: Mapped[MatchStatus] = mapped_column(nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    ruleset_version: Mapped[str] = mapped_column(
        String, ForeignKey("ruleset.version"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    members: Mapped[list[MatchMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class MatchMember(Base):
    """Polymorphic membership — the integrity core (Schema §5.4).

    Two DB-level constraints (added in the Alembic migration, not expressible declaratively)
    carry C5:
      - `uniq_active_member`: partial unique index on (run_id, entity_type, entity_id)
        WHERE the parent group's status <> 'rejected' — a record cannot belong to two
        active match groups.
      - `trg_allocation_balances`: deferred constraint trigger asserting allocated amounts
        for a group sum exactly to the parent bank credit, checked at COMMIT so a group can
        be built incrementally across several inserts within one transaction.
    """

    __tablename__ = "match_member"

    id: Mapped[uuid.UUID] = uuid_pk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("match_group.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[EntityType] = mapped_column(nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    allocated_amount: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )  # denormalised deliberately — see module docstring

    group: Mapped[MatchGroup] = relationship(back_populates="members")
