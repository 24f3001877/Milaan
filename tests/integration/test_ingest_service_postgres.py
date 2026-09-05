"""Integration tests for the ingest pipeline against a real Postgres.

Requires DATABASE_URL_SYNC in the environment (set by CI's postgres service container,
per Appflow §4.2 job 2). Skipped automatically if not configured, so `pytest -m "not slow"`
still runs cleanly on a machine without a database — these are exercised by the `test`
CI job specifically, not by a bare `pytest` invocation.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.ingest.service import ingest_rows, preview_file

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
    version = f"v-test-{uuid.uuid4().hex[:8]}"
    session.execute(
        text("INSERT INTO ruleset (version, config, created_at) VALUES (:v, '{}', now())"),
        {"v": version},
    )
    session.execute(
        text(
            "INSERT INTO recon_run (id, period_start, period_end, status, "
            "orchestrator_state, ruleset_version, is_synthetic, input_manifest_hash, "
            "record_count, llm_mode, created_by) VALUES "
            "(:id, '2026-01-01', '2026-01-31', 'queued', 'INGEST', :v, true, "
            "repeat('a', 64), 0, 'cached', 'operator')"
        ),
        {"id": str(run_id), "v": version},
    )
    session.commit()
    return run_id


def _make_source_file(
    session: Session, run_id: uuid.UUID, source_type: str, filename: str
) -> uuid.UUID:
    sfid = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO data_source_file (id, run_id, source_type, filename, "
            "content_sha256, row_count, ingested_at) VALUES "
            "(:id, :run_id, :st, :fn, repeat('b', 64), 0, now())"
        ),
        {"id": str(sfid), "run_id": str(run_id), "st": source_type, "fn": filename},
    )
    session.commit()
    return sfid


def test_large_batch_ingest_survives_postgres_parameter_limit(db_session, tmp_path) -> None:
    """Regression test: a 5,000+ row settlement file previously blew past Postgres's
    65535-bound-parameters-per-statement ceiling. This proves the batching fix holds."""
    from datetime import date

    from milaan.adapters.synthetic.generate import generate, write_outputs

    batch = generate(
        seed=99,
        record_count=5000,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        pathology_weights={"missing_in_bank": 1.0},
        pathology_rate=0.1,
    )
    out_dir = tmp_path / "synth"
    write_outputs(batch, out_dir)

    run_id = _make_run(db_session)
    sfid = _make_source_file(db_session, run_id, "gateway_settlement", "gateway_settlement.csv")
    content = (out_dir / "gateway_settlement.csv").read_bytes()
    preview = preview_file("gateway_settlement", "gateway_settlement.csv", content)
    rows = read_rows("gateway_settlement.csv", content)

    summary = ingest_rows(db_session, run_id, sfid, "gateway_settlement", rows, preview.mapping)
    db_session.commit()

    assert summary.inserted == len(rows)
    assert summary.duplicates_skipped == 0
    assert summary.validation_errors == []


def test_reingesting_identical_batch_is_idempotent(db_session, tmp_path) -> None:
    """C4: re-submitting an identical batch produces zero duplicate rows."""
    from datetime import date

    from milaan.adapters.synthetic.generate import generate, write_outputs

    batch = generate(
        seed=7,
        record_count=300,
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        pathology_weights={"missing_in_bank": 1.0},
    )
    out_dir = tmp_path / "synth"
    write_outputs(batch, out_dir)

    run_id = _make_run(db_session)
    sfid = _make_source_file(db_session, run_id, "orders", "orders.csv")
    content = (out_dir / "orders.csv").read_bytes()
    preview = preview_file("orders", "orders.csv", content)
    rows = read_rows("orders.csv", content)

    first = ingest_rows(db_session, run_id, sfid, "orders", rows, preview.mapping)
    db_session.commit()
    second = ingest_rows(db_session, run_id, sfid, "orders", rows, preview.mapping)
    db_session.commit()

    assert first.inserted == len(rows)
    assert second.inserted == 0
    assert second.duplicates_skipped == len(rows)

    count = db_session.execute(
        text("SELECT count(*) FROM order_record WHERE run_id = :rid"), {"rid": str(run_id)}
    ).scalar()
    assert count == len(rows)
