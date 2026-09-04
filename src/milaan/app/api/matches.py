"""Match routes — backs the Match Explorer (UI/UX §3.3 S7): search any order/payment/
UTR/amount, see how it tied."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.app.deps import get_db, require_bearer_token

router = APIRouter(prefix="/api/v1", tags=["matches"], dependencies=[Depends(require_bearer_token)])


def _group_to_dict(group_row, members) -> dict:
    return {
        "id": str(group_row.id), "tier": group_row.tier, "confidence": float(group_row.confidence),
        "status": group_row.status, "rule_id": group_row.rule_id,
        "ruleset_version": group_row.ruleset_version,
        "members": [
            {"entity_type": m.entity_type, "entity_id": str(m.entity_id), "allocated_amount": str(m.allocated_amount)}
            for m in members
        ],
    }


@router.get("/runs/{run_id}/matches")
def list_matches(
    run_id: str, db: Session = Depends(get_db),
    tier: str | None = None, status: str | None = None, min_confidence: float | None = None,
    limit: int = 50,
):
    conditions = ["run_id = :run_id"]
    params: dict = {"run_id": run_id, "limit": limit}
    if tier:
        conditions.append("tier = :tier")
        params["tier"] = tier
    if status:
        conditions.append("status = :status")
        params["status"] = status
    if min_confidence is not None:
        conditions.append("confidence >= :min_confidence")
        params["min_confidence"] = min_confidence
    where = " AND ".join(conditions)

    groups = db.execute(
        text(
            f"SELECT id, tier, confidence, status, rule_id, ruleset_version FROM match_group "
            f"WHERE {where} ORDER BY created_at DESC LIMIT :limit"
        ),
        params,
    ).fetchall()
    results = []
    for g in groups:
        members = db.execute(
            text("SELECT entity_type, entity_id, allocated_amount FROM match_member WHERE group_id = :gid"),
            {"gid": str(g.id)},
        ).fetchall()
        results.append(_group_to_dict(g, members))
    return results


@router.get("/matches/{group_id}")
def get_match_group(group_id: str, db: Session = Depends(get_db)):
    g = db.execute(
        text("SELECT id, tier, confidence, status, rule_id, ruleset_version FROM match_group WHERE id = :id"),
        {"id": group_id},
    ).fetchone()
    if not g:
        raise HTTPException(status_code=404, detail="match group not found")
    members = db.execute(
        text("SELECT entity_type, entity_id, allocated_amount FROM match_member WHERE group_id = :gid"),
        {"gid": group_id},
    ).fetchall()
    return _group_to_dict(g, members)
