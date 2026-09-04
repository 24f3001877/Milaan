"""Integration tests for the orchestrator (Implementation Plan §6.2, task 2.16)."""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from milaan.adapters.ingest.mapping import propose_mapping
from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.llm.client import LLMClient
from milaan.adapters.synthetic.generate import generate, write_outputs
from milaan.adapters.synthetic.pathology import DEFAULT_WEIGHTS
from milaan.app.orchestrator.orchestrator import Orchestrator, SourceFileInput
from milaan.app.orchestrator.states import OrchestratorState

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
pytestmark = pytest.mark.skipif(not DATABASE_URL_SYNC, reason="DATABASE_URL_SYNC not set")


@pytest.fixture
def db_session():
    engine = create_engine(DATABASE_URL_SYNC)
    with Session(engine) as session:
        yield session
        session.rollback()


def _make_run(session: Session, llm_mode: str):
    run_id = uuid.uuid4()
    version = f"v-orch-test-{uuid.uuid4().hex[:8]}"
    session.execute(
        text("INSERT INTO ruleset (version, config, created_at) VALUES (:v, '{}', now())"),
        {"v": version},
    )
    session.execute(
        text(
            "INSERT INTO recon_run (id, period_start, period_end, status, orchestrator_state, "
            "ruleset_version, is_synthetic, input_manifest_hash, record_count, llm_mode, "
            "created_by) VALUES (:id, '2026-01-01', '2026-01-31', 'queued', 'INGEST', :v, "
            "true, repeat('a', 64), 0, :mode, 'operator')"
        ),
        {"id": str(run_id), "v": version, "mode": llm_mode},
    )
    session.commit()
    return run_id, version


def _make_sources(data_dir: Path):
    sources = []
    for source_type, fname in [
        ("orders", "orders.csv"),
        ("gateway_settlement", "gateway_settlement.csv"),
        ("bank_statement", "bank_statement.csv"),
    ]:
        content = (data_dir / fname).read_bytes()
        rows = read_rows(fname, content)
        mapping = propose_mapping(source_type, list(rows[0].keys())).mapping
        sources.append(SourceFileInput(source_type=source_type, filename=fname, content=content, mapping=mapping))
    return sources


@pytest.fixture
def small_batch(tmp_path: Path) -> Path:
    batch = generate(
        seed=99, record_count=150, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        pathology_weights=DEFAULT_WEIGHTS,
    )
    out_dir = tmp_path / "synth"
    write_outputs(batch, out_dir)
    return out_dir


def test_orchestrator_completes_with_llm_disabled_gracefully_degraded(db_session, small_batch, tmp_path) -> None:
    run_id, version = _make_run(db_session, "disabled")
    llm_client = LLMClient(mode="disabled", cache_dir=tmp_path / "llm_cache")
    orch = Orchestrator(db_session, run_id, llm_client, date(2026, 1, 1), date(2026, 1, 31), ruleset_version=version)

    result = orch.run(_make_sources(small_batch))

    assert result.final_state == OrchestratorState.AWAIT_REVIEW
    assert result.cancelled is False
    assert result.llm_degraded is True
    assert len(result.exceptions) > 0
    assert all(te.hypothesis is None for te in result.exceptions)

    status_row = db_session.execute(
        text("SELECT status FROM recon_run WHERE id = :id"), {"id": str(run_id)}
    ).fetchone()
    assert status_row.status == "awaiting_review"

    group_count = db_session.execute(
        text("SELECT count(*) FROM match_group WHERE run_id = :id"), {"id": str(run_id)}
    ).scalar()
    assert group_count == len(result.groups)
    assert group_count > 0


def test_orchestrator_cooperative_cancel_stops_cleanly(db_session, small_batch, tmp_path) -> None:
    run_id, version = _make_run(db_session, "disabled")
    llm_client = LLMClient(mode="disabled", cache_dir=tmp_path / "llm_cache")
    orch = Orchestrator(
        db_session, run_id, llm_client, date(2026, 1, 1), date(2026, 1, 31),
        ruleset_version=version, cancel_check=lambda: True,
    )

    result = orch.run(_make_sources(small_batch))

    assert result.final_state == OrchestratorState.CANCELLED
    assert result.cancelled is True

    status_row = db_session.execute(
        text("SELECT status, orchestrator_state FROM recon_run WHERE id = :id"), {"id": str(run_id)}
    ).fetchone()
    assert status_row.status == "cancelled"

    group_count = db_session.execute(
        text("SELECT count(*) FROM match_group WHERE run_id = :id"), {"id": str(run_id)}
    ).scalar()
    assert group_count == 0


def test_orchestrator_writes_a_verifiable_audit_trail(db_session, small_batch, tmp_path) -> None:
    from milaan.adapters.audit.audit_log import verify_chain

    run_id, version = _make_run(db_session, "disabled")
    llm_client = LLMClient(mode="disabled", cache_dir=tmp_path / "llm_cache")
    orch = Orchestrator(db_session, run_id, llm_client, date(2026, 1, 1), date(2026, 1, 31), ruleset_version=version)
    orch.run(_make_sources(small_batch))

    valid, broken_at = verify_chain(db_session, run_id=run_id)
    assert valid is True
    assert broken_at is None

    transition_count = db_session.execute(
        text("SELECT count(*) FROM audit_log WHERE run_id = :id AND action = 'state_transition'"),
        {"id": str(run_id)},
    ).scalar()
    assert transition_count >= len(OrchestratorState) - 2
