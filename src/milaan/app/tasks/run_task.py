"""Celery task wrapping the orchestrator (Implementation Plan §6.2, task 2.17).

The actual reconciliation work happens here, in the worker process — `POST /runs`
(app/api/runs.py) only enqueues and returns 202 immediately.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from milaan.adapters.ingest.mapping import propose_mapping
from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.llm.client import LLMClient
from milaan.app.orchestrator.orchestrator import Orchestrator, SourceFileInput
from milaan.app.settings import get_settings
from milaan.app.tasks.celery_app import celery_app


def _cancel_requested(session: Session, run_id: str) -> bool:
    row = session.execute(
        text("SELECT 1 FROM audit_log WHERE run_id = :run_id AND action = 'cancel_requested' LIMIT 1"),
        {"run_id": run_id},
    ).fetchone()
    return row is not None


@celery_app.task(name="milaan.run_reconciliation")
def run_reconciliation(run_id_str: str) -> dict:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync)

    with Session(engine) as session:
        run_row = session.execute(
            text(
                "SELECT period_start, period_end, ruleset_version, llm_mode FROM recon_run "
                "WHERE id = :id"
            ),
            {"id": run_id_str},
        ).fetchone()
        if run_row is None:
            return {"status": "error", "detail": "run not found"}

        session.execute(
            text("UPDATE recon_run SET status = 'running', started_at = now() WHERE id = :id"),
            {"id": run_id_str},
        )
        session.commit()

        data_dir = Path("data/uploads")
        sources = []
        file_rows = session.execute(
            text("SELECT id, source_type, filename FROM data_source_file WHERE run_id = :run_id"),
            {"run_id": run_id_str},
        ).fetchall()
        for file_row in file_rows:
            fname = file_row.filename
            content = (data_dir / str(file_row.id)).read_bytes()
            rows = read_rows(fname, content)
            mapping = propose_mapping(file_row.source_type, list(rows[0].keys())).mapping
            sources.append(SourceFileInput(
                source_type=file_row.source_type, filename=fname, content=content, mapping=mapping
            ))

        llm_client = LLMClient(
            mode=run_row.llm_mode, cache_dir=Path("data/llm_cache"),
            model=settings.llm_model, prompt_version=settings.llm_prompt_version,
            provider=settings.llm_provider, api_key=settings.llm_api_key or None,
        )

        orch = Orchestrator(
            session, uuid.UUID(run_id_str), llm_client,
            period_start=run_row.period_start, period_end=run_row.period_end,
            ruleset_version=run_row.ruleset_version,
            cancel_check=lambda: _cancel_requested(session, run_id_str),
        )
        result = orch.run(sources)

        if not result.cancelled:
            session.execute(
                text("UPDATE recon_run SET status = 'completed' WHERE id = :id AND status != 'cancelled'"),
                {"id": run_id_str},
            )
            session.commit()

        return {
            "status": "cancelled" if result.cancelled else "completed",
            "final_state": result.final_state.value,
            "llm_degraded": result.llm_degraded,
            "group_count": len(result.groups),
            "exception_count": len(result.exceptions),
        }
