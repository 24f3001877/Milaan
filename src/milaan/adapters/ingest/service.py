"""Ingest orchestration.

`preview_file` backs `POST /ingest/preview` (TRD §2.5) — parses and proposes a mapping,
persists nothing. `ingest_rows` backs the actual load: applies a confirmed mapping,
transforms and validates every row via the domain layer, and writes rows with
`INSERT ... ON CONFLICT (run_id, row_hash) DO NOTHING` so re-submitting an identical batch
is a no-op (C4 idempotency) rather than a duplicate-row error.

Uses a synchronous SQLAlchemy `Session` deliberately: ingest runs inside the Celery worker
(TRD §2.2), not on the FastAPI request path, so there's no event loop to integrate with —
sync SQLAlchemy is the simpler, correct choice here even though the API layer itself is
async (TRD's asyncpg choice governs request/response routes, not worker-side batch writes).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from milaan.adapters.db.models import BankTxn, OrderRecord, SettlementLine
from milaan.adapters.ingest.mapping import MappingResult, fingerprint, propose_mapping
from milaan.adapters.ingest.parsers import UploadValidationError, read_rows
from milaan.domain.errors import RowValidationError
from milaan.domain.ingest_transform import (
    transform_bank_row,
    transform_order_row,
    transform_settlement_row,
)
from milaan.domain.records import BankTxnDraft, OrderRecordDraft, SettlementLineDraft

TRANSFORM_BY_SOURCE = {
    "orders": transform_order_row,
    "gateway_settlement": transform_settlement_row,
    "bank_statement": transform_bank_row,
}
MODEL_BY_SOURCE = {
    "orders": OrderRecord,
    "gateway_settlement": SettlementLine,
    "bank_statement": BankTxn,
}


@dataclass
class PreviewResult:
    source_type: str
    header_fingerprint: str
    mapping: dict[str, str]
    field_confidence: dict[str, float]
    overall_confidence: float
    method: str
    unmapped_required: list[str]
    sample_rows: list[dict[str, str]]
    total_rows: int


def preview_file(source_type: str, filename: str, content: bytes) -> PreviewResult:
    """Never persists — TRD §2.5: 'Does not persist records.'"""
    rows = read_rows(filename, content)
    if not rows:
        raise UploadValidationError("File has no data rows")
    headers = list(rows[0].keys())
    fp = fingerprint(headers)
    result: MappingResult = propose_mapping(source_type, headers)
    return PreviewResult(
        source_type=source_type,
        header_fingerprint=fp,
        mapping=result.mapping,
        field_confidence=result.field_confidence,
        overall_confidence=result.overall_confidence,
        method=result.method,
        unmapped_required=result.unmapped_required,
        sample_rows=rows[:5],
        total_rows=len(rows),
    )


def apply_mapping(raw_row: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    """source-column-keyed row -> canonical-field-keyed row, per the confirmed mapping."""
    return {canonical: raw_row.get(source_col, "") for source_col, canonical in mapping.items()}


@dataclass
class IngestSummary:
    total_rows: int
    inserted: int
    duplicates_skipped: int
    validation_errors: list[str] = field(default_factory=list)


def ingest_rows(
    session: Session,
    run_id: uuid.UUID,
    source_file_id: uuid.UUID,
    source_type: str,
    raw_rows: list[dict[str, str]],
    mapping: dict[str, str],
) -> IngestSummary:
    transform = TRANSFORM_BY_SOURCE[source_type]
    model = MODEL_BY_SOURCE[source_type]

    drafts: list[OrderRecordDraft | SettlementLineDraft | BankTxnDraft] = []
    errors: list[str] = []
    for i, raw_row in enumerate(raw_rows):
        mapped = apply_mapping(raw_row, mapping)
        try:
            drafts.append(transform(mapped))
        except RowValidationError as exc:
            errors.append(f"row {i}: {exc}")

    if not drafts:
        return IngestSummary(len(raw_rows), 0, 0, errors)

    values = [_draft_to_values(d, run_id, source_file_id) for d in drafts]
    # Postgres caps bound parameters at 65535 per statement. Batch conservatively so no
    # single INSERT approaches that ceiling regardless of how many columns a table has.
    batch_size = max(1, 20_000 // max(len(values[0]), 1))
    inserted = 0
    for i in range(0, len(values), batch_size):
        batch = values[i : i + batch_size]
        stmt = pg_insert(model).values(batch)
        stmt = stmt.on_conflict_do_nothing(index_elements=["run_id", "row_hash"])
        stmt = stmt.returning(model.id)
        result = session.execute(stmt)
        inserted += len(result.fetchall())
    duplicates = len(values) - inserted
    return IngestSummary(len(raw_rows), inserted, duplicates, errors)


def _draft_to_values(
    draft: OrderRecordDraft | SettlementLineDraft | BankTxnDraft,
    run_id: uuid.UUID,
    source_file_id: uuid.UUID,
) -> dict:
    base = {"run_id": run_id, "source_file_id": source_file_id}
    if isinstance(draft, OrderRecordDraft):
        return {
            **base,
            "order_id": draft.order_id,
            "invoice_no": draft.invoice_no,
            "customer_ref": draft.customer_ref,
            "gross": draft.gross.amount,
            "currency": draft.currency,
            "payment_id": draft.payment_id,
            "order_status": draft.order_status,
            "created_at": draft.created_at,
            "row_hash": draft.row_hash,
        }
    if isinstance(draft, SettlementLineDraft):
        return {
            **base,
            "settlement_id": draft.settlement_id,
            "payment_id": draft.payment_id,
            "order_ref": draft.order_ref,
            "line_type": draft.line_type,
            "gross": draft.gross.amount,
            "fee": draft.fee.amount,
            "tax": draft.tax.amount,
            "net": draft.net.amount,
            "instrument": draft.instrument,
            "settled_on": draft.settled_on,
            "utr": draft.utr,
            "row_hash": draft.row_hash,
        }
    # BankTxnDraft
    return {
        **base,
        "value_date": draft.value_date,
        "narration": draft.narration,
        "utr_extracted": draft.utr_extracted,
        "credit": draft.credit.amount,
        "debit": draft.debit.amount,
        "balance": draft.balance.amount if draft.balance is not None else None,
        "row_hash": draft.row_hash,
    }
