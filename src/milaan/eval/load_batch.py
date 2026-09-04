"""Loads a seeded synthetic batch (three CSVs) from disk directly into domain entities,
in-memory — no Postgres round-trip. This still exercises the REAL ingest mapping and
row-validation logic (adapters/ingest), which is what's actually being measured; only the
persistence step is skipped, for speed and to keep `make eval` runnable without a live
database connection, matching Appflow's `LLM_MODE=cached` philosophy of a deterministic,
dependency-free CI path (job 3 mounts Postgres for the full DB-backed suite; this harness
is the fast, portable measurement path task 2.7 asks for).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from milaan.adapters.ingest.mapping import propose_mapping
from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.ingest.service import apply_mapping
from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.ingest_transform import (
    transform_bank_row,
    transform_order_row,
    transform_settlement_row,
)


@dataclass
class LoadedBatch:
    orders: list[OrderEntity]
    settlement_lines: list[SettlementLineEntity]
    bank_txns: list[BankTxnEntity]
    validation_errors: dict[str, list[str]]


def load_batch_from_directory(data_dir: Path) -> LoadedBatch:
    orders = _load_orders(data_dir / "orders.csv")
    settlement_lines = _load_settlement_lines(data_dir / "gateway_settlement.csv")
    bank_txns = _load_bank_txns(data_dir / "bank_statement.csv")
    return LoadedBatch(
        orders=orders.entities, settlement_lines=settlement_lines.entities,
        bank_txns=bank_txns.entities,
        validation_errors={
            "orders": orders.errors,
            "gateway_settlement": settlement_lines.errors,
            "bank_statement": bank_txns.errors,
        },
    )


@dataclass
class _Loaded:
    entities: list
    errors: list[str]


def _load_orders(path: Path) -> _Loaded:
    content = path.read_bytes()
    rows = read_rows(path.name, content)
    headers = list(rows[0].keys()) if rows else []
    mapping = propose_mapping("orders", headers).mapping
    entities, errors = [], []
    for i, raw_row in enumerate(rows):
        mapped = apply_mapping(raw_row, mapping)
        try:
            d = transform_order_row(mapped)
        except Exception as exc:  # noqa: BLE001 - accumulate, don't abort the whole file
            errors.append(f"row {i}: {exc}")
            continue
        entities.append(
            OrderEntity(id=uuid.uuid4(), order_id=d.order_id, payment_id=d.payment_id, gross=d.gross)
        )
    return _Loaded(entities, errors)


def _load_settlement_lines(path: Path) -> _Loaded:
    content = path.read_bytes()
    rows = read_rows(path.name, content)
    headers = list(rows[0].keys()) if rows else []
    mapping = propose_mapping("gateway_settlement", headers).mapping
    entities, errors = [], []
    for i, raw_row in enumerate(rows):
        mapped = apply_mapping(raw_row, mapping)
        try:
            d = transform_settlement_row(mapped)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"row {i}: {exc}")
            continue
        entities.append(
            SettlementLineEntity(
                id=uuid.uuid4(), settlement_id=d.settlement_id, payment_id=d.payment_id,
                order_ref=d.order_ref, line_type=d.line_type, gross=d.gross, net=d.net,
                utr=d.utr, settled_on=d.settled_on, fee=d.fee, tax=d.tax, instrument=d.instrument,
            )
        )
    return _Loaded(entities, errors)


def _load_bank_txns(path: Path) -> _Loaded:
    content = path.read_bytes()
    rows = read_rows(path.name, content)
    headers = list(rows[0].keys()) if rows else []
    mapping = propose_mapping("bank_statement", headers).mapping
    entities, errors = [], []
    for i, raw_row in enumerate(rows):
        mapped = apply_mapping(raw_row, mapping)
        try:
            d = transform_bank_row(mapped)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"row {i}: {exc}")
            continue
        entities.append(
            BankTxnEntity(
                id=uuid.uuid4(), value_date=d.value_date, narration=d.narration,
                utr_extracted=d.utr_extracted, credit=d.credit, debit=d.debit,
            )
        )
    return _Loaded(entities, errors)
