"""Integration tests for the FastAPI layer (Implementation Plan §6.2, task 2.17)."""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
pytestmark = pytest.mark.skipif(not DATABASE_URL_SYNC, reason="DATABASE_URL_SYNC not set")


@pytest.fixture
def client():
    from milaan.app.main import app

    return TestClient(app)


@pytest.fixture
def auth_headers():
    token = os.environ.get("API_TOKEN", "test-token")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def db_session():
    engine = create_engine(DATABASE_URL_SYNC)
    with Session(engine) as session:
        yield session
        session.rollback()


def _make_completed_run(db_session, tmp_path: Path) -> uuid.UUID:
    from milaan.adapters.ingest.mapping import propose_mapping
    from milaan.adapters.ingest.parsers import read_rows
    from milaan.adapters.llm.client import LLMClient
    from milaan.adapters.synthetic.generate import generate, write_outputs
    from milaan.adapters.synthetic.pathology import DEFAULT_WEIGHTS
    from milaan.app.orchestrator.orchestrator import Orchestrator, SourceFileInput

    batch = generate(
        seed=55, record_count=100, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        pathology_weights=DEFAULT_WEIGHTS,
    )
    data_dir = tmp_path / "synth"
    write_outputs(batch, data_dir)

    run_id = uuid.uuid4()
    version = f"v-api-{uuid.uuid4().hex[:8]}"
    db_session.execute(
        text("INSERT INTO ruleset (version, config, created_at) VALUES (:v, '{}', now())"), {"v": version}
    )
    db_session.execute(
        text(
            "INSERT INTO recon_run (id, period_start, period_end, status, orchestrator_state, "
            "ruleset_version, is_synthetic, input_manifest_hash, record_count, llm_mode, "
            "created_by) VALUES (:id, '2026-01-01', '2026-01-31', 'queued', 'INGEST', :v, "
            "true, repeat('d', 64), 0, 'disabled', 'operator')"
        ),
        {"id": str(run_id), "v": version},
    )
    db_session.commit()

    sources = []
    for source_type, fname in [
        ("orders", "orders.csv"), ("gateway_settlement", "gateway_settlement.csv"),
        ("bank_statement", "bank_statement.csv"),
    ]:
        content = (data_dir / fname).read_bytes()
        rows = read_rows(fname, content)
        mapping = propose_mapping(source_type, list(rows[0].keys())).mapping
        sources.append(SourceFileInput(source_type=source_type, filename=fname, content=content, mapping=mapping))

    llm_client = LLMClient(mode="disabled", cache_dir=tmp_path / "llm_cache")
    orch = Orchestrator(db_session, run_id, llm_client, date(2026, 1, 1), date(2026, 1, 31), ruleset_version=version)
    orch.run(sources)
    return run_id


def test_healthz_requires_no_auth(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200


def test_protected_route_requires_auth(client) -> None:
    r = client.get("/api/v1/runs")
    assert r.status_code == 401


def test_protected_route_rejects_wrong_token(client) -> None:
    r = client.get("/api/v1/runs", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_protected_route_accepts_correct_token(client, auth_headers) -> None:
    r = client.get("/api/v1/runs", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_run_detail_and_metrics(client, auth_headers, db_session, tmp_path) -> None:
    run_id = _make_completed_run(db_session, tmp_path)

    r = client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "awaiting_review"

    r = client.get(f"/api/v1/runs/{run_id}/metrics", headers=auth_headers)
    assert r.status_code == 200
    assert "auto_match_rate" in r.json()


def test_run_not_found_returns_404(client, auth_headers) -> None:
    r = client.get(f"/api/v1/runs/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


def test_exception_approve_reject_are_idempotent_against_double_action(
    client, auth_headers, db_session, tmp_path
) -> None:
    run_id = _make_completed_run(db_session, tmp_path)
    r = client.get(f"/api/v1/runs/{run_id}/exceptions", headers=auth_headers, params={"limit": 1})
    exceptions = r.json()
    if not exceptions:
        pytest.skip("this seed produced no exceptions to test against")
    exc_id = exceptions[0]["id"]

    r1 = client.post(
        f"/api/v1/exceptions/{exc_id}/approve",
        headers={**auth_headers, "Idempotency-Key": "k1"}, json={"action": "propose_match"},
    )
    assert r1.status_code == 200

    r2 = client.post(
        f"/api/v1/exceptions/{exc_id}/approve",
        headers={**auth_headers, "Idempotency-Key": "k1"}, json={"action": "propose_match"},
    )
    assert r2.status_code == 409


def test_approve_without_idempotency_key_is_rejected(client, auth_headers, db_session, tmp_path) -> None:
    run_id = _make_completed_run(db_session, tmp_path)
    r = client.get(f"/api/v1/runs/{run_id}/exceptions", headers=auth_headers, params={"limit": 1})
    exceptions = r.json()
    if not exceptions:
        pytest.skip("this seed produced no exceptions to test against")
    r2 = client.post(
        f"/api/v1/exceptions/{exceptions[0]['id']}/approve", headers=auth_headers, json={"action": "propose_match"}
    )
    assert r2.status_code == 400


def test_audit_verify_endpoint(client, auth_headers, db_session, tmp_path) -> None:
    run_id = _make_completed_run(db_session, tmp_path)
    r = client.get(f"/api/v1/runs/{run_id}/audit/verify", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_dev_seed_route_available_in_development(client, auth_headers, monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    r = client.post(
        "/api/v1/dev/seed", headers=auth_headers,
        json={"seed": 1, "record_count": 20, "period_start": "2026-01-01", "period_end": "2026-01-31"},
    )
    assert r.status_code == 200
    assert r.json()["order_count"] == 20
