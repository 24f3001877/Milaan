#!/usr/bin/env bash
#
# Container entrypoint for the single-image deployment (Dockerfile.deploy). Runs migrations,
# then the Celery worker and the API in one container — they share `data/uploads`, which is
# not optional: the API writes uploaded files there and the worker reads them back.
set -euo pipefail

PORT="${PORT:-8000}"
CONCURRENCY="${CELERY_CONCURRENCY:-2}"
# `solo` runs the task in the worker's own process instead of forking a child. On a small
# instance that matters: pandas is imported per process at roughly 90 MB, so prefork pays
# for it twice for no benefit here — the orchestrator processes one run at a time anyway.
POOL="${CELERY_POOL:-prefork}"

# A managed Postgres often accepts TCP a moment before it accepts queries, and `alembic
# upgrade head` against a not-quite-ready database exits non-zero — which the platform
# reports as a failed deploy rather than as "try again in two seconds".
echo "==> waiting for the database"
for attempt in $(seq 1 40); do
  if python - <<'PY'
import os, sys
from sqlalchemy import create_engine, text
url = os.environ.get("DATABASE_URL_SYNC")
if not url:
    print("DATABASE_URL_SYNC is not set", file=sys.stderr)
    sys.exit(2)
try:
    with create_engine(url, pool_pre_ping=True).connect() as conn:
        conn.execute(text("SELECT 1"))
except Exception as exc:
    print(f"not ready: {exc}", file=sys.stderr)
    sys.exit(1)
PY
  then
    echo "==> database is ready (attempt ${attempt})"
    break
  fi
  if [ "${attempt}" -eq 40 ]; then
    echo "==> database never became reachable; giving up" >&2
    exit 1
  fi
  sleep 2
done

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> starting celery worker (pool=${POOL}, concurrency=${CONCURRENCY})"
worker_args=(--loglevel="${LOG_LEVEL:-info}" --pool="${POOL}")
# The solo pool has no child processes, so --concurrency is meaningless there.
if [ "${POOL}" != "solo" ]; then
  worker_args+=(--concurrency="${CONCURRENCY}")
fi
# Gossip and mingle only coordinate between multiple workers. With one worker they are pure
# broker traffic, which is worth avoiding on a small managed Redis.
celery -A milaan.app.tasks.celery_app worker "${worker_args[@]}" --without-gossip --without-mingle &
worker_pid=$!

echo "==> starting uvicorn on 0.0.0.0:${PORT}"
uvicorn milaan.app.main:app --host 0.0.0.0 --port "${PORT}" &
api_pid=$!

# Exit as soon as either process does. An API that is up with a dead worker still accepts
# run submissions and simply never finishes them, which presents as a hang — far harder to
# diagnose than a container that restarts and says why in its logs.
exit_code=0
wait -n "${worker_pid}" "${api_pid}" || exit_code=$?
echo "==> a child process exited (code ${exit_code}); shutting down the container" >&2
kill "${worker_pid}" "${api_pid}" 2>/dev/null || true
wait || true
exit "${exit_code}"
