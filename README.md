# Milaan (मिलान)

### Three-way settlement reconciliation for modern finance teams

> **Razorpay Buildathon 2026 | Track 04: AI Finance Controller**

Milaan turns a fragile spreadsheet exercise into an auditable workflow. It reconciles a
merchant's **orders**, **payment-gateway settlements**, and **bank statement credits**,
then gives an operator a focused queue for the records that still need a human decision.

The core principle is simple: **deterministic code owns every rupee; AI helps explain the
mess around it.** Matching, amount calculations, fee verification, allocation integrity, and
approval boundaries stay in typed, testable application code. The optional LLM layer is used
for schema mapping, exception triage, and plain-language explanations, never for inventing
financial amounts.

## Why Milaan

Real settlement data is rarely a clean one-to-one join:

- One bank credit can contain many gateway settlement lines.
- Refunds and chargebacks can be netted into later settlements.
- Fees and tax introduce paise-level rounding differences.
- Settlement cycles can cross reporting-period boundaries.
- A changed CSV header can break an otherwise correct reconciliation.

Milaan addresses these cases with a four-tier matching cascade:

1. **T1: exact payment ID**
2. **T2: exact UTR / bank reference**
3. **T3: bounded allocation search**
4. **T4: fee verification and exception classification**

When the evidence is insufficient, Milaan refuses to guess and creates a categorised,
reviewable exception instead.

## What a reviewer can try

1. Open the web app.
2. Upload an orders file, gateway settlement file, and bank statement file.
3. Review the detected column mappings and confirm any low-confidence mapping.
4. Start a reconciliation run for the selected period.
5. Inspect the dashboard: match rate, value explained, matching tiers, and exception mix.
6. Approve, reject, or escalate exceptions from the review queue.

The repository includes synthetic demo inputs in [`data/synthetic/`](data/synthetic/):
`orders.csv`, `gateway_settlement.csv`, and `bank_statement.csv`.

## Headline numbers (reproducible — see below)

5,000 orders, 5,166 settlement lines, 80 bank credits, seed 42, period 2026-01-01 to 2026-01-31.

| Metric | Milaan | Naive exact-ID baseline |
|---|---|---|
| Auto-match rate | **99.46%** | 94.35% |
| Value explained | 99.80% | 98.49% |
| False-match rate | **0.0000%** | 0.0000% |
| Throughput | ~1,900-2,900 records/sec | — |
| Human touches / 100 records | 32.1 | n/a |

**Pathology table** (12/12 categories, all injected defects correctly detected, 0 missed):

| Pathology | Injected | Detected | Missed |
|---|---|---|---|
| ambiguous_multi_candidate | 39 | 39 | 0 |
| amount_mismatch | 43 | 43 | 0 |
| chargeback_debit_unlinked | 60 | 60 | 0 |
| duplicate_utr | 3 | 3 | 0 |
| fee_variance | 54 | 54 | 0 |
| missing_in_bank | 39 | 39 | 0 |
| missing_in_gateway | 46 | 46 | 0 |
| netted_refund_unlinked | 60 | 60 | 0 |
| orphan_bank_credit | 51 | 51 | 0 |
| partial_settlement | 82 | 82 | 0 |
| period_boundary_timing | 44 | 44 | 0 |
| unknown_adjustment | 51 | 51 | 0 |

**GATE 1** (Implementation Plan section 6.2, deterministic-only checkpoint): auto-match rate
>=75%, false-match rate <=0.5% — **passed** at 99.46% / 0.0000%, before the LLM layer
contributes anything. The full CI gate (`config/eval_thresholds.json`, >=90% auto-match)
also passes on the deterministic engine alone.

See [`LIMITATIONS.md`](LIMITATIONS.md) for the scope behind these numbers.

## Run locally

### Prerequisites

- Docker Desktop with Docker Compose
- Node.js 22 or later
- npm
- Git

### Start the backend

```bash
git clone <repo-url>
cd milaan
cp .env.example .env
docker compose up -d --build
docker compose exec -T api alembic upgrade head
```

### Start the frontend

Run this in a second terminal:

```bash
cd milaan/frontend
npm ci
npm run dev
```

Open **http://localhost:5173**. The API documentation is available at
**http://localhost:8000/docs**.

The default local configuration uses cached LLM responses, so no API key or network call is
required for the demo. The API and frontend use the development bearer token configured in
`.env` and `frontend/.env`.

### Reproduce the benchmark

```bash
docker compose exec -T api python -m milaan.adapters.synthetic.generate \
  --seed 42 --records 5000 --out data/synthetic
docker compose exec -T api python -m milaan.eval.run --data-dir data/synthetic
docker compose exec -T api python -m milaan.eval.gate metrics.json --mode deterministic_only
```

Equivalent project shortcuts are available through the [`Makefile`](Makefile): `make up`,
`make seed`, `make eval`, `make lint`, and `make test`.

## Deploy as a demo

[`Dockerfile.deploy`](Dockerfile.deploy) builds the Vue application and serves it from the
FastAPI process, while starting the Celery worker in the same container. This produces one
public URL and keeps uploaded files available to both the API and worker.

For a buildathon demo, deploy the image to a container platform such as Railway or Fly.io,
attach managed PostgreSQL and Redis, and configure the variables in [`.env.example`](.env.example).
Use `APP_ENV=demo`, `LLM_MODE=cached`, and `DEV_SEED_ENABLED=false`. The platform must provide
the database and Redis URLs, and must forward its public port to the container's `PORT` value.

After deployment, verify:

```text
https://your-demo-domain.example/healthz
https://your-demo-domain.example/docs
```

The first endpoint should return `{"status":"ok"}`. Use synthetic data only for this demo.

## Validation

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src/
uv run pytest -m "not slow" --cov --cov-fail-under=80
```

GitHub Actions runs static checks, database-backed tests, security/property tests, and the
deterministic evaluation gate on pushes to `main` and pull requests. See
`.github/workflows/ci.yml` for the pipeline definition.

## Architecture and stack

Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2.0, Celery + Redis, Vue 3 + TypeScript +
Vite, pandas/Decimal (never float) for money. Full rationale for every choice in
`docs/02-TRD.md` section 2.1.

## Repository layout

```
src/milaan/
  domain/       pure business logic - money, matching cascade (T1-T4), fee verification,
                exception classification, scoring. ZERO I/O, ZERO framework imports,
                enforced by import-linter in CI.
  adapters/     csv/xlsx ingest, Postgres repositories, LLM client + cache, synthetic
                data generator, audit log.
  app/          FastAPI routes, Celery tasks, the orchestrator state machine, settings.
  eval/         the eval harness - ground truth scoring, naive baseline, metrics
                emitters, the golden-set triage accuracy report, the CI regression gate.
frontend/       Vue 3 + TS + Vite SPA (S3 Mapping Review, S5 Dashboard, S6/S6b Exception
                Queue - the three GATE-2-critical screens; S1 Runs List built as a bonus).
migrations/     Alembic - 16 tables, plus the hand-written C5 allocation-integrity
                triggers and audit-log privilege lockdown (0002_c5_allocation_integrity.py).
tests/          unit/ (pure domain, fast), property/ (hypothesis money invariants),
                integration/ (real Postgres - ingest, matching, orchestrator, API, audit).
docs/           the six blueprint documents (PDR, TRD, UI/UX, Appflow, Schema, Plan).
```

## Honest scope

Everything under `src/milaan/domain/` and the matching cascade, fee verification, and
exception classification built on it is fully real and tested against synthetic data with
independently-injected ground truth (not self-consistent by construction — see
`tests/integration/` for the actual proofs, including deliberately trying to break the C5
allocation-integrity constraints and the audit-log hash chain).

The LLM layer (`src/milaan/adapters/llm/`) is a real, working client — live mode genuinely
calls Anthropic's API — but this build environment has no provisioned API key, so the
demonstrated cached-mode runs use a clearly-labelled deterministic placeholder responder
(see `src/milaan/eval/triage_eval.py`'s docstring) rather than genuine model judgments. The
harness, schemas, retry-then-escalate logic, and prompt-injection defence are all real and
would work unchanged against real model output. See `LIMITATIONS.md`.

## Money safety

Money uses `NUMERIC(20,4)` in PostgreSQL and `Decimal` in Python. JSON money values are
serialised as strings. A custom check rejects `float(` in the domain money path, and Hypothesis
tests verify exactness across more than 1,000 generated cases.

## Security and demo data

- API routes use a static bearer token for this single-operator MVP.
- Uploaded content is treated as untrusted data, including prompt-injection attempts.
- LLM output is schema-validated and referenced record IDs are checked.
- Database writes use parameterised queries and reconciliation runs require idempotency keys.
- The audit log is hash-chained and can be verified through the API.
- The repository contains synthetic data only. Do not upload real customer or banking data to
  the public demo.

This is a buildathon MVP, not a production banking platform. Production use would require
SSO/RBAC, tenancy isolation, managed encryption, durable object storage, observability, and
separation of analyst and controller approvals.

## Project documents

- [`docs/00-README-index.md`](docs/00-README-index.md): blueprint index
- [`docs/01-PDR.md`](docs/01-PDR.md): product requirements
- [`docs/02-TRD.md`](docs/02-TRD.md): technical design and API surface
- [`docs/03-UIUX.md`](docs/03-UIUX.md): user flows and interface specification
- [`docs/04-Appflow-CICD.md`](docs/04-Appflow-CICD.md): CI/CD and deployment strategy
- [`docs/05-Backend-Schema.md`](docs/05-Backend-Schema.md): database schema and integrity rules
- [`docs/06-Implementation-Plan.md`](docs/06-Implementation-Plan.md): implementation plan

## Buildathon links

- **Repository:** add your public GitHub URL here before submission.
- **Live demo:** add your deployed application URL here before submission.
