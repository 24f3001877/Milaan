"""Integration tests for the hash-chained audit log (Schema §5.4) against real Postgres."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from milaan.adapters.audit.audit_log import append_entry, verify_chain

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
pytestmark = pytest.mark.skipif(not DATABASE_URL_SYNC, reason="DATABASE_URL_SYNC not set")


@pytest.fixture
def db_session():
    engine = create_engine(DATABASE_URL_SYNC)
    with Session(engine) as session:
        yield session
        session.rollback()


def _make_run(session: Session) -> uuid.UUID:
    run_id = uuid.uuid4()
    version = f"v-audit-{uuid.uuid4().hex[:8]}"
    session.execute(
        text("INSERT INTO ruleset (version, config, created_at) VALUES (:v, '{}', now())"),
        {"v": version},
    )
    session.execute(
        text(
            "INSERT INTO recon_run (id, period_start, period_end, status, orchestrator_state, "
            "ruleset_version, is_synthetic, input_manifest_hash, record_count, llm_mode, "
            "created_by) VALUES (:id, '2026-01-01', '2026-01-31', 'queued', 'INGEST', :v, "
            "true, repeat('a', 64), 0, 'cached', 'operator')"
        ),
        {"id": str(run_id), "v": version},
    )
    session.commit()
    return run_id


def test_chain_verifies_clean_when_untampered(db_session) -> None:
    run_id = _make_run(db_session)
    eid1, eid2 = uuid.uuid4(), uuid.uuid4()
    append_entry(db_session, "operator", "run_created", "recon_run", None, {"period": "2026-01"}, run_id=run_id)
    append_entry(db_session, "analyst_1", "exception_approved", "exception_item", eid1, {"action": "propose_match"}, run_id=run_id)
    append_entry(db_session, "analyst_1", "match_group_created", "match_group", eid2, {"tier": "T1_PAYMENT_ID"}, run_id=run_id)
    db_session.commit()

    valid, broken_at = verify_chain(db_session, run_id=run_id)
    assert valid is True
    assert broken_at is None


def test_tampering_with_a_historical_row_is_detected(db_session) -> None:
    run_id = _make_run(db_session)
    append_entry(db_session, "operator", "run_created", "recon_run", None, {"period": "2026-01"}, run_id=run_id)
    r2 = append_entry(
        db_session, "analyst_1", "exception_approved", "exception_item", uuid.uuid4(),
        {"action": "propose_match"}, run_id=run_id,
    )
    db_session.commit()

    db_session.execute(text("UPDATE audit_log SET actor = 'hacker' WHERE id = :id"), {"id": r2["id"]})
    db_session.commit()

    valid, broken_at = verify_chain(db_session, run_id=run_id)
    assert valid is False
    assert broken_at == r2["id"]


def test_tampering_with_payload_is_detected(db_session) -> None:
    run_id = _make_run(db_session)
    r1 = append_entry(db_session, "operator", "run_created", "recon_run", None, {"period": "2026-01"}, run_id=run_id)
    db_session.commit()

    db_session.execute(
        text('UPDATE audit_log SET payload = \'{"period": "2099-99"}\' WHERE id = :id'),
        {"id": r1["id"]},
    )
    db_session.commit()

    valid, broken_at = verify_chain(db_session, run_id=run_id)
    assert valid is False
    assert broken_at == r1["id"]


def test_runtime_app_role_cannot_update_or_delete_audit_log(db_session) -> None:
    """The DB-level privilege lockdown from migration 0002 — belt-and-braces alongside
    this module's own append-only discipline."""
    app_url = DATABASE_URL_SYNC.replace("milaan:milaan", "milaan_app:milaan_app_dev_only")
    engine = create_engine(app_url)
    with Session(engine) as app_session:
        r = append_entry(app_session, "operator", "test_action", "recon_run", None, {})
        app_session.commit()
        with pytest.raises(Exception, match="permission denied"):
            app_session.execute(text("UPDATE audit_log SET actor = 'x' WHERE id = :id"), {"id": r["id"]})
            app_session.commit()
        app_session.rollback()
