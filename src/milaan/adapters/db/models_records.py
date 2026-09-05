"""SQLAlchemy 2.0 typed models — part 2: the three ingested record tables.

Money columns are `Numeric(20, 4)` — never `Float` — per C1. The domain layer converts these
to/from the `Money` value object at the adapter boundary; raw ORM rows never leak a float.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from milaan.adapters.db.base import Base, uuid_pk
from milaan.adapters.db.enums import Instrument, LineType


class OrderRecord(Base):
    __tablename__ = "order_record"
    __table_args__ = (
        CheckConstraint("gross >= 0", name="gross_non_negative"),
        UniqueConstraint("run_id", "row_hash", name="uq_order_record_run_row_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recon_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("data_source_file.id"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # pseudonymised
    gross: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")
    payment_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    order_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    injected_pathology: Mapped[str | None] = mapped_column(Text, nullable=True)  # synthetic only


class SettlementLine(Base):
    __tablename__ = "settlement_line"
    __table_args__ = (
        # Naive identity holds only for payment lines; refunds/chargebacks invert it (§5.4 note).
        CheckConstraint(
            "line_type <> 'payment' OR net = gross - fee - tax",
            name="net_equals_gross_minus_fee_tax_for_payment_lines",
        ),
        UniqueConstraint("run_id", "row_hash", name="uq_settlement_line_run_row_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recon_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("data_source_file.id"), nullable=False
    )
    settlement_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    payment_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    order_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_type: Mapped[LineType] = mapped_column(nullable=False)
    gross: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    fee: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    tax: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    net: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    instrument: Mapped[Instrument | None] = mapped_column(nullable=True)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    utr: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    row_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    injected_pathology: Mapped[str | None] = mapped_column(Text, nullable=True)


class BankTxn(Base):
    __tablename__ = "bank_txn"
    __table_args__ = (
        CheckConstraint("credit = 0 OR debit = 0", name="credit_xor_debit"),
        UniqueConstraint("run_id", "row_hash", name="uq_bank_txn_run_row_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("recon_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("data_source_file.id"), nullable=False
    )
    value_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    utr_extracted: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    credit: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=0)
    debit: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=0)
    balance: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    row_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    injected_pathology: Mapped[str | None] = mapped_column(Text, nullable=True)
