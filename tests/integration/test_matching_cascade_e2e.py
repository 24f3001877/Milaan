"""End-to-end integration test: synthetic generation -> Postgres ingest -> T1/T2 matching.

This is the regression guard for the full Phase 1 + Phase 2 (T1/T2) pipeline together —
each piece has its own unit tests, but this is what proves they actually compose. Requires
DATABASE_URL_SYNC (see test_ingest_service_postgres.py for the same convention).
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.ingest.service import ingest_rows, preview_file
from milaan.adapters.matching.loader import load_bank_txns, load_orders, load_settlement_lines
from milaan.adapters.synthetic.generate import generate, write_outputs
from milaan.adapters.synthetic.pathology import DEFAULT_WEIGHTS
from milaan.domain.matching.cascade import run_t1_t2

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
    version = f"v-e2e-{uuid.uuid4().hex[:8]}"
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


def test_full_pipeline_at_2000_records(db_session, tmp_path) -> None:
    batch = generate(
        seed=123, record_count=2000, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        pathology_weights=DEFAULT_WEIGHTS,
    )
    out_dir = tmp_path / "synth"
    write_outputs(batch, out_dir)

    run_id = _make_run(db_session)
    for source_type, fname in [
        ("orders", "orders.csv"),
        ("gateway_settlement", "gateway_settlement.csv"),
        ("bank_statement", "bank_statement.csv"),
    ]:
        sfid = uuid.uuid4()
        db_session.execute(
            text(
                "INSERT INTO data_source_file (id, run_id, source_type, filename, "
                "content_sha256, row_count, ingested_at) VALUES "
                "(:id, :run_id, :st, :fn, repeat('b', 64), 0, now())"
            ),
            {"id": str(sfid), "run_id": str(run_id), "st": source_type, "fn": fname},
        )
        db_session.commit()
        content = (out_dir / fname).read_bytes()
        preview = preview_file(source_type, fname, content)
        assert preview.method == "deterministic", f"{source_type} mapping should be exact"
        rows = read_rows(fname, content)
        summary = ingest_rows(db_session, run_id, sfid, source_type, rows, preview.mapping)
        db_session.commit()
        assert summary.validation_errors == [], summary.validation_errors

    orders = load_orders(db_session, run_id)
    lines = load_settlement_lines(db_session, run_id)
    banks = load_bank_txns(db_session, run_id)
    assert len(orders) == 2000

    result = run_t1_t2(orders, lines, banks)

    matched_line_count = sum(len(g.member_ids("settlement_line")) for g in result.groups)
    match_rate = matched_line_count / len(lines)

    # T1+T2 alone (no T3/T4 yet) should clear a large majority — most pathologies still
    # resolve at this stage (see domain/matching/t2_utr.py docstring); only genuinely
    # ambiguous/missing cases should remain. This is a floor, not the final GATE 1 target
    # (that's T1-T3 combined, per Implementation Plan §6.2).
    assert match_rate > 0.95, f"T1+T2 match rate too low: {match_rate:.1%}"

    # No entity should ever appear in more than one final group (mirrors DB constraint C5,
    # checked here in-memory before anything is persisted).
    seen: set[tuple[str, uuid.UUID]] = set()
    for g in result.groups:
        for m in g.members:
            key = (m.entity_type, m.entity_id)
            assert key not in seen, f"entity {key} appears in two match groups"
            seen.add(key)

    # Every group with a bank_txn member must balance exactly (mirrors the DB trigger).
    from decimal import Decimal
    for g in result.groups:
        bank_members = [m for m in g.members if m.entity_type == "bank_txn"]
        if not bank_members:
            continue
        bank_total = sum((m.allocated_amount.amount for m in bank_members), Decimal("0"))
        settlement_total = sum(
            (m.allocated_amount.amount for m in g.members if m.entity_type == "settlement_line"),
            Decimal("0"),
        )
        assert bank_total == settlement_total, f"group imbalance: {bank_total} != {settlement_total}"
