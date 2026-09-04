"""Exception review routes — the core workflow (TRD §2.5, UI/UX §3.3 S6/S6b)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.adapters.audit.audit_log import append_entry
from milaan.app.deps import get_db, require_bearer_token

router = APIRouter(prefix="/api/v1", tags=["exceptions"], dependencies=[Depends(require_bearer_token)])

BULK_APPROVE_MAX = 50
BULK_APPROVE_MIN_CONFIDENCE = 0.85


def _money(value) -> str | None:
    return str(value) if value is not None else None


@router.get("/runs/{run_id}/exceptions")
def list_exceptions(
    run_id: str, db: Session = Depends(get_db),
    category: str | None = None, status: str | None = None, severity: str | None = None,
    limit: int = 50, cursor: str | None = None,
):
    conditions = ["run_id = :run_id"]
    params: dict = {"run_id": run_id, "limit": limit}
    if category:
        conditions.append("category = :category")
        params["category"] = category
    if status:
        conditions.append("status = :status")
        params["status"] = status
    if severity:
        conditions.append("severity = :severity")
        params["severity"] = severity
    if cursor:
        conditions.append("id > :cursor")
        params["cursor"] = cursor

    where = " AND ".join(conditions)
    rows = db.execute(
        text(
            f"SELECT id, category, severity, entity_type, entity_id, amount_at_risk, "
            f"confidence, proposed_action, status FROM exception_item WHERE {where} "
            f"ORDER BY amount_at_risk DESC LIMIT :limit"
        ),
        params,
    ).fetchall()
    return [
        {
            "id": str(r.id), "category": r.category, "severity": r.severity,
            "entity_type": r.entity_type, "entity_id": str(r.entity_id),
            "amount_at_risk": _money(r.amount_at_risk), "confidence": float(r.confidence) if r.confidence else None,
            "proposed_action": r.proposed_action, "status": r.status,
        }
        for r in rows
    ]


@router.get("/exceptions/{exception_id}")
def get_exception(exception_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT id, run_id, category, severity, entity_type, entity_id, amount_at_risk, "
            "deterministic_trace, candidates, hypothesis, proposed_action, action_payload, "
            "confidence, rationale, llm_call_id, status, reject_reason_code "
            "FROM exception_item WHERE id = :id"
        ),
        {"id": exception_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="exception not found")
    return {
        "id": str(row.id), "run_id": str(row.run_id), "category": row.category, "severity": row.severity,
        "entity_type": row.entity_type, "entity_id": str(row.entity_id),
        "amount_at_risk": _money(row.amount_at_risk), "deterministic_trace": row.deterministic_trace,
        "candidates": row.candidates, "hypothesis": row.hypothesis, "proposed_action": row.proposed_action,
        "action_payload": row.action_payload, "confidence": float(row.confidence) if row.confidence else None,
        "rationale": row.rationale, "llm_call_id": str(row.llm_call_id) if row.llm_call_id else None,
        "status": row.status, "reject_reason_code": row.reject_reason_code,
    }


class ApproveRequest(BaseModel):
    action: str
    action_payload: dict = {}
    note: str | None = None


class RejectRequest(BaseModel):
    reason_code: str
    note: str | None = None


def _require_idempotency_key(idempotency_key: str | None) -> None:
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")


@router.post("/exceptions/{exception_id}/approve")
def approve_exception(
    exception_id: str, body: ApproveRequest, db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(idempotency_key)
    row = db.execute(
        text("SELECT id, run_id, status FROM exception_item WHERE id = :id"), {"id": exception_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status != "open":
        raise HTTPException(status_code=409, detail=f"exception already {row.status}")

    # Proposals stop at approval; nothing posts to a ledger (PDR §1.6). Approving here
    # marks the exception resolved and records the human decision. A fuller build would
    # dispatch on `body.action` to create the corresponding match_group row; documented
    # here as intentionally minimal — the audit trail and status transition are real.
    db.execute(
        text(
            "UPDATE exception_item SET status = 'approved', resolved_by = 'operator', "
            "resolved_at = now() WHERE id = :id"
        ),
        {"id": exception_id},
    )
    append_entry(
        db, "operator", "exception_approved", "exception_item", exception_id,
        {"action": body.action, "note": body.note}, run_id=row.run_id,
    )
    db.commit()
    return {"status": "approved"}


@router.post("/exceptions/{exception_id}/reject")
def reject_exception(exception_id: str, body: RejectRequest, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT id, run_id, status FROM exception_item WHERE id = :id"), {"id": exception_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status != "open":
        raise HTTPException(status_code=409, detail=f"exception already {row.status}")

    db.execute(
        text(
            "UPDATE exception_item SET status = 'rejected', reject_reason_code = :reason, "
            "resolved_by = 'operator', resolved_at = now() WHERE id = :id"
        ),
        {"id": exception_id, "reason": body.reason_code},
    )
    append_entry(
        db, "operator", "exception_rejected", "exception_item", exception_id,
        {"reason_code": body.reason_code, "note": body.note}, run_id=row.run_id,
    )
    db.commit()
    return {"status": "rejected"}


@router.post("/exceptions/{exception_id}/escalate")
def escalate_exception(exception_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT id, run_id, status FROM exception_item WHERE id = :id"), {"id": exception_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status != "open":
        raise HTTPException(status_code=409, detail=f"exception already {row.status}")

    db.execute(text("UPDATE exception_item SET status = 'escalated' WHERE id = :id"), {"id": exception_id})
    append_entry(db, "operator", "exception_escalated", "exception_item", exception_id, {}, run_id=row.run_id)
    db.commit()
    return {"status": "escalated"}


class BulkApproveRequest(BaseModel):
    ids: list[str]
    action: str


@router.post("/exceptions/bulk-approve")
def bulk_approve(
    body: BulkApproveRequest, db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_idempotency_key(idempotency_key)
    if len(body.ids) > BULK_APPROVE_MAX:
        raise HTTPException(status_code=422, detail=f"cannot bulk-approve more than {BULK_APPROVE_MAX} at once")

    rows = db.execute(
        text("SELECT id, run_id, status, confidence FROM exception_item WHERE id = ANY(:ids)"),
        {"ids": body.ids},
    ).fetchall()
    below_threshold = [
        str(r.id) for r in rows
        if r.confidence is None or float(r.confidence) < BULK_APPROVE_MIN_CONFIDENCE
    ]
    if below_threshold:
        # Bulk approval of low-confidence items is refused by design (TRD §2.5).
        raise HTTPException(
            status_code=422,
            detail=f"{len(below_threshold)} item(s) below the auto-confidence threshold "
            f"({BULK_APPROVE_MIN_CONFIDENCE}); bulk approval refused for: {below_threshold}",
        )

    approved = []
    for r in rows:
        if r.status != "open":
            continue
        db.execute(
            text(
                "UPDATE exception_item SET status = 'approved', resolved_by = 'operator', "
                "resolved_at = now() WHERE id = :id"
            ),
            {"id": str(r.id)},
        )
        append_entry(
            db, "operator", "exception_approved", "exception_item", str(r.id),
            {"action": body.action, "bulk": True}, run_id=r.run_id,
        )
        approved.append(str(r.id))
    db.commit()
    return {"approved": approved, "count": len(approved)}
