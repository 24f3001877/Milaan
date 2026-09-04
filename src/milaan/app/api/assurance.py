"""Assurance and ops routes (TRD §2.5)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.adapters.audit.audit_log import verify_chain
from milaan.app.deps import get_db, require_bearer_token, require_development_env

router = APIRouter(prefix="/api/v1", tags=["assurance"], dependencies=[Depends(require_bearer_token)])


@router.get("/runs/{run_id}/audit")
def get_audit_trail(run_id: str, db: Session = Depends(get_db), limit: int = 100):
    rows = db.execute(
        text(
            "SELECT id, ts, actor, action, entity_type, entity_id, hash FROM audit_log "
            "WHERE run_id = :run_id ORDER BY id ASC LIMIT :limit"
        ),
        {"run_id": run_id, "limit": limit},
    ).fetchall()
    return [
        {
            "id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action,
            "entity_type": r.entity_type, "entity_id": str(r.entity_id) if r.entity_id else None,
            "hash_prefix": r.hash[:12],
        }
        for r in rows
    ]


@router.get("/runs/{run_id}/audit/verify")
def verify_audit_trail(run_id: str, db: Session = Depends(get_db)):
    valid, broken_at = verify_chain(db, run_id=run_id)
    return {"valid": valid, "broken_at": broken_at}


@router.get("/runs/{run_id}/llm-calls")
def get_llm_calls(run_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, purpose, prompt_version, input_tokens, output_tokens, cost_micros, "
            "was_cached, validation_attempts, validation_failed FROM llm_call "
            "WHERE run_id = :run_id ORDER BY created_at ASC"
        ),
        {"run_id": run_id},
    ).fetchall()
    return [
        {
            "id": str(r.id), "purpose": r.purpose, "prompt_version": r.prompt_version,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
            "cost_micros": r.cost_micros, "was_cached": r.was_cached,
            "validation_attempts": r.validation_attempts, "validation_failed": r.validation_failed,
        }
        for r in rows
    ]


class DevSeedRequest(BaseModel):
    seed: int = 42
    record_count: int = 5000
    period_start: date = date(2026, 1, 1)
    period_end: date = date(2026, 1, 31)


@router.post("/dev/seed", dependencies=[Depends(require_development_env)])
def dev_seed(body: DevSeedRequest):
    from pathlib import Path

    from milaan.adapters.synthetic.generate import generate, write_outputs
    from milaan.adapters.synthetic.pathology import DEFAULT_WEIGHTS

    batch = generate(
        seed=body.seed, record_count=body.record_count,
        period_start=body.period_start, period_end=body.period_end,
        pathology_weights=DEFAULT_WEIGHTS,
    )
    out_dir = Path("data/synthetic")
    write_outputs(batch, out_dir)
    return {
        "status": "generated", "out_dir": str(out_dir),
        "order_count": len(batch.orders), "pathology_counts": batch.pathology_counts,
    }
