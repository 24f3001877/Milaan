"""Runs routes (TRD §2.5)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.adapters.audit.audit_log import append_entry
from milaan.app.deps import get_db, require_bearer_token
from milaan.app.settings import Settings, get_settings

router = APIRouter(
    prefix="/api/v1/runs", tags=["runs"], dependencies=[Depends(require_bearer_token)]
)


class CreateRunRequest(BaseModel):
    orders_file_id: str
    gateway_settlement_file_id: str
    bank_statement_file_id: str
    period_start: date
    period_end: date
    ruleset_version: str


@router.post("", status_code=202)
def create_run(
    body: CreateRunRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    upload_dir = Path("data/uploads")
    uploads = {}
    for source_type, file_id in (
        ("orders", body.orders_file_id),
        ("gateway_settlement", body.gateway_settlement_file_id),
        ("bank_statement", body.bank_statement_file_id),
    ):
        try:
            upload_id = uuid.UUID(file_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid {source_type} file_id") from exc
        content_path = upload_dir / str(upload_id)
        metadata_path = upload_dir / f"{upload_id}.json"
        if not content_path.is_file() or not metadata_path.is_file():
            raise HTTPException(status_code=422, detail=f"uploaded {source_type} file not found")
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("source_type") != source_type:
            raise HTTPException(status_code=422, detail=f"file type mismatch for {source_type}")
        uploads[source_type] = {"id": str(upload_id), "metadata": metadata}

    manifest_hash = hashlib.sha256(
        "|".join(
            sorted(
                [body.orders_file_id, body.gateway_settlement_file_id, body.bank_statement_file_id]
            )
        ).encode()
    ).hexdigest()

    existing = db.execute(
        text(
            "SELECT id, status FROM recon_run "
            "WHERE input_manifest_hash = :h AND ruleset_version = :rv"
        ),
        {"h": manifest_hash, "rv": body.ruleset_version},
    ).fetchone()
    if existing:
        # C4 idempotency: identical inputs under identical rules return the existing run.
        return {"run_id": str(existing.id), "status": existing.status}

    ruleset_exists = db.execute(
        text("SELECT 1 FROM ruleset WHERE version = :v"), {"v": body.ruleset_version}
    ).fetchone()
    if not ruleset_exists:
        if body.ruleset_version != "v1":
            raise HTTPException(
                status_code=422, detail=f"unknown ruleset_version {body.ruleset_version!r}"
            )
        db.execute(
            text(
                "INSERT INTO ruleset (version, config, created_at, description) "
                "VALUES ('v1', CAST('{}' AS JSONB), now(), 'Default application ruleset') "
                "ON CONFLICT (version) DO NOTHING"
            )
        )

    run_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO recon_run (id, period_start, period_end, status, orchestrator_state, "
            "ruleset_version, is_synthetic, input_manifest_hash, record_count, llm_mode, "
            "created_by) VALUES (:id, :ps, :pe, 'queued', 'INGEST', :rv, false, :hash, 0, "
            ":llm_mode, 'operator')"
        ),
        {
            "id": str(run_id),
            "ps": body.period_start,
            "pe": body.period_end,
            "rv": body.ruleset_version,
            "hash": manifest_hash,
            "llm_mode": settings.llm_mode.value,
        },
    )
    for source_type, upload in uploads.items():
        db.execute(
            text(
                "INSERT INTO data_source_file (id, run_id, source_type, filename, "
                "content_sha256, row_count, ingested_at) VALUES "
                "(:id, :run_id, :source_type, :filename, :sha256, 0, now())"
            ),
            {
                "id": upload["id"],
                "run_id": str(run_id),
                "source_type": source_type,
                "filename": upload["metadata"]["filename"],
                "sha256": upload["metadata"]["sha256"],
            },
        )
    append_entry(
        db,
        "operator",
        "run_created",
        "recon_run",
        run_id,
        {"idempotency_key": idempotency_key},
        run_id=run_id,
    )
    db.commit()

    # Actual orchestration is dispatched to the Celery worker (app/tasks/run_task.py) —
    # this route only enqueues and returns immediately (202), per TRD §2.5.
    from milaan.app.tasks.run_task import run_reconciliation

    run_reconciliation.delay(str(run_id))

    return {"run_id": str(run_id), "status": "queued"}


@router.get("")
def list_runs(db: Session = Depends(get_db), limit: int = 50, offset: int = 0):
    rows = db.execute(
        text(
            "SELECT id, period_start, period_end, status, record_count, metrics, started_at "
            "FROM recon_run ORDER BY started_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
        ),
        {"limit": limit, "offset": offset},
    ).fetchall()
    # The columns S1 asks for (UI/UX §3.3) all come from the `metrics` JSONB already
    # selected above, so surfacing them costs no extra query — the list previously
    # discarded them and the screen could only render four columns.
    return [
        {
            "id": str(r.id),
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "status": r.status,
            "record_count": r.record_count,
            "auto_match_rate": (r.metrics or {}).get("auto_match_rate"),
            "value_explained_pct": (r.metrics or {}).get("value_explained_pct"),
            "exception_count": (r.metrics or {}).get("exception_count"),
            "started_at": r.started_at.isoformat() if r.started_at else None,
        }
        for r in rows
    ]


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT id, period_start, period_end, status, orchestrator_state, ruleset_version, "
            "prompt_version, rng_seed, record_count, llm_mode, metrics, started_at, finished_at "
            "FROM recon_run WHERE id = :id"
        ),
        {"id": run_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return {
        "id": str(row.id),
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "status": row.status,
        "orchestrator_state": row.orchestrator_state,
        "ruleset_version": row.ruleset_version,
        "prompt_version": row.prompt_version,
        "rng_seed": row.rng_seed,
        "record_count": row.record_count,
        "llm_mode": row.llm_mode,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.get("/{run_id}/metrics")
def get_run_metrics(run_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT metrics FROM recon_run WHERE id = :id"), {"id": run_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row.metrics or {}


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT status FROM recon_run WHERE id = :id"), {"id": run_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    if row.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"run already {row.status}")
    # Cooperative, not a status enum value (run_status has no 'cancel_requested' member):
    # writing this audit entry IS the signal. The orchestrator's cancel_check
    # (app/orchestrator/orchestrator.py, wired from the Celery task) polls for exactly
    # this — "has a cancel_requested entry been recorded for this run" — at each state
    # boundary, and only then flips status to 'cancelled' itself once it actually stops.
    append_entry(db, "operator", "cancel_requested", "recon_run", run_id, {}, run_id=run_id)
    db.commit()
    return {"status": "cancel_requested"}
