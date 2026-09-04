"""Celery application (TRD §2.1: "A 5,000-record reconciliation is a background job, not
an HTTP request. Provides progress reporting and retry semantics.")."""

from __future__ import annotations

from celery import Celery

from milaan.app.settings import get_settings

_settings = get_settings()

celery_app = Celery(
    "milaan",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["milaan.app.tasks.run_task"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)
