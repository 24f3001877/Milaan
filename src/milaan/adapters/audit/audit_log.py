"""Append-only, hash-chained audit log adapter (Schema §5.4).

Writing computes each entry's hash from the previous entry's hash, forming a chain.
Tampering with any historical row breaks every hash computed after it — `verify_chain`
proves this by recomputing the whole chain from scratch and comparing.

The DB migration (0002_c5_allocation_integrity) already revokes UPDATE/DELETE on
`audit_log` from the runtime application role — append-only is enforced by privilege,
not just by this module's own discipline (Schema §5.4).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.domain.audit_hash import compute_entry_hash


def append_entry(
    session: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    payload: dict,
    run_id: uuid.UUID | str | None = None,
) -> dict:
    next_id = session.execute(
        text("SELECT nextval(pg_get_serial_sequence('audit_log', 'id'))")
    ).scalar()
    # The chain is scoped per run_id, not global across the whole table: `GET
    # /runs/{id}/audit/verify` (TRD §2.5) verifies ONE run's trail as a self-contained
    # chain starting from prev_hash=None, so the write side must build that same
    # per-run chain — a global "last row in the table" lookup would make a run's first
    # entry's prev_hash point at unrelated activity from a different run, which
    # verify_chain(run_id=X) could never reconstruct (it also starts each run at None).
    # Entries with no run_id (e.g. system-level actions) form their own separate chain.
    if run_id is not None:
        last_hash = session.execute(
            text("SELECT hash FROM audit_log WHERE run_id = :run_id ORDER BY id DESC LIMIT 1"),
            {"run_id": str(run_id)},
        ).scalar()
    else:
        last_hash = session.execute(
            text("SELECT hash FROM audit_log WHERE run_id IS NULL ORDER BY id DESC LIMIT 1")
        ).scalar()
    ts = datetime.now(UTC)
    entity_id_str = str(entity_id) if entity_id is not None else None

    entry_hash = compute_entry_hash(
        next_id, ts, actor, action, entity_type, entity_id_str, payload, last_hash
    )
    session.execute(
        text(
            "INSERT INTO audit_log (id, ts, run_id, actor, action, entity_type, entity_id, "
            "payload, prev_hash, hash) VALUES "
            "(:id, :ts, :run_id, :actor, :action, :entity_type, :entity_id, "
            "CAST(:payload AS JSONB), :prev_hash, :hash)"
        ),
        {
            "id": next_id,
            "ts": ts,
            "run_id": str(run_id) if run_id else None,
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id_str,
            "payload": json.dumps(payload, default=str),
            "prev_hash": last_hash,
            "hash": entry_hash,
        },
    )
    return {"id": next_id, "hash": entry_hash, "prev_hash": last_hash}


def verify_chain(session: Session, run_id: uuid.UUID | str | None = None) -> tuple:
    """Returns (valid, broken_at_id). Recomputes every hash from scratch — this file never
    trusts the stored `hash` column as ground truth, only as the value being checked."""
    query = (
        "SELECT id, ts, actor, action, entity_type, entity_id, payload, prev_hash, hash "
        "FROM audit_log"
    )
    params: dict = {}
    if run_id is not None:
        query += " WHERE run_id = :run_id"
        params["run_id"] = str(run_id)
    query += " ORDER BY id ASC"

    rows = session.execute(text(query), params).fetchall()
    prev_hash = None
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
        expected = compute_entry_hash(
            row.id,
            row.ts,
            row.actor,
            row.action,
            row.entity_type,
            str(row.entity_id) if row.entity_id is not None else None,
            payload,
            prev_hash,
        )
        if row.prev_hash != prev_hash or expected != row.hash:
            return False, row.id
        prev_hash = row.hash
    return True, None
