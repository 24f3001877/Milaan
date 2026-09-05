"""Loads a run's persisted records from Postgres into the domain matching entities.

This is the adapter-side boundary the matching cascade needs: domain/matching/ never
touches SQLAlchemy, so something has to translate ORM rows (`Numeric` columns, etc.) into
`Money`-typed dataclasses first. Used by the orchestrator (task 2.16, not yet built) and
directly by integration tests in the meantime.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from milaan.adapters.db.models import BankTxn, OrderRecord, SettlementLine
from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.money import Money


def load_orders(session: Session, run_id: uuid.UUID) -> list[OrderEntity]:
    rows = session.execute(select(OrderRecord).where(OrderRecord.run_id == run_id)).scalars().all()
    return [
        OrderEntity(id=r.id, order_id=r.order_id, payment_id=r.payment_id, gross=Money(r.gross))
        for r in rows
    ]


def load_settlement_lines(session: Session, run_id: uuid.UUID) -> list[SettlementLineEntity]:
    rows = (
        session.execute(select(SettlementLine).where(SettlementLine.run_id == run_id))
        .scalars()
        .all()
    )
    return [
        SettlementLineEntity(
            id=r.id,
            settlement_id=r.settlement_id,
            payment_id=r.payment_id,
            order_ref=r.order_ref,
            line_type=r.line_type.value if hasattr(r.line_type, "value") else r.line_type,
            gross=Money(r.gross),
            net=Money(r.net),
            utr=r.utr,
            settled_on=r.settled_on,
            fee=Money(r.fee),
            tax=Money(r.tax),
            instrument=r.instrument.value if hasattr(r.instrument, "value") else r.instrument,
        )
        for r in rows
    ]


def load_bank_txns(session: Session, run_id: uuid.UUID) -> list[BankTxnEntity]:
    rows = session.execute(select(BankTxn).where(BankTxn.run_id == run_id)).scalars().all()
    return [
        BankTxnEntity(
            id=r.id,
            value_date=r.value_date,
            narration=r.narration,
            utr_extracted=r.utr_extracted,
            credit=Money(r.credit),
            debit=Money(r.debit),
        )
        for r in rows
    ]
