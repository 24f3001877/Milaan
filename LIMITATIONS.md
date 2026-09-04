# Limitations

Stated plainly, per the project's own principle (PDR section 1.6): a defensible cut is one
you disclose, not one you hide. This file is what a reviewer should read to judge whether
the headline numbers in `README.md` mean what they appear to mean.

## 1. The LLM layer runs on a placeholder responder, not a live model

This build environment has no provisioned `ANTHROPIC_API_KEY`. `LLMClient` (task 2.10) is
a real, working client — `live` mode genuinely POSTs to `api.anthropic.com/v1/messages`,
parses and schema-validates the response, retries once on validation failure, and writes
every real response to the disk cache. But no live call has actually been made in this
build.

What's demonstrated instead: a clearly-labelled, deterministic **placeholder responder**
(`src/milaan/eval/triage_eval.py`, `_PLACEHOLDER_ACTION_BY_CATEGORY`) that maps each
exception category to a plausible action via simple, independently-written rules — not
copied from the golden set's hand labels, so scoring against them is a genuine (if limited)
comparison, not a tautology. On the 40-item golden set this placeholder scores **82.5%**
accuracy, with the deliberate wrong answers concentrated exactly where the placeholder's
simplified rules diverge from a human's judgment (`amount_mismatch`, `unknown_adjustment`).

This is not "the LLM works, trust us" — it is "the LLM plumbing works, and here is
precisely what was and wasn't exercised." The prompt templates, Pydantic response schemas,
retry-then-escalate logic, referenced-ID validation, and prompt-injection defence are all
real and were tested end-to-end through the orchestrator (two-pass test: discover 109
exceptions with LLM disabled, populate a cache with placeholder responses for those exact
prompts, then re-run fresh with `LLM_MODE=cached` — 109/109 triaged, 109/109 explained, 218
`llm_call` rows persisted, audit chain valid). What's untested is whether a *real* model's
judgment matches a human's as often as the placeholder's simplified rules do. Running
`LLM_MODE=live` once with a real key would populate the same cache with genuine responses
and this harness would score them identically, with no code changes.

## 2. `duplicate_utr` has an outsized blast radius at realistic scale

Only 3 `duplicate_utr` incidents are injected into the 5,000-record batch (deliberately
capped — see the fix documented in `adapters/synthetic/generate.py`'s
`DUPLICATE_UTR_MAX_INCIDENTS`, after an earlier per-order injection scheme cascaded to
break nearly every bank credit in the batch). But even 3 incidents, at ~178 settlement
lines per calendar day, invalidate **bank-tie confirmation** for up to 6 days' worth of
lines (~1,000 lines) — because T2 correctly declines the *entire* day rather than guess
which lines belong where, and T3's bounded candidate window (20 candidates by default)
can't attempt a day-scale search.

This is not a bug: refusing to guess across ~178 candidates is exactly the "no unbounded
combinatorics" discipline C6 calls for, and every one of those lines still keeps its
**order** tie (T1 payment_id matching is unaffected) — only the specific bank-credit
confirmation is withheld. But it means a single UTR-field corruption incident in a real
gateway export could plausibly generate a very large `missing_in_bank` /
`duplicate_utr`-flagged exception count for that day, which a real deployment would want a
day-level anomaly workflow for (flag the whole day for investigation, rather than 178
individual line-level exceptions) — not built here.

## 3. No real rate card, no DB-backed rate card loader

`src/milaan/eval/rate_card.py`'s `default_rate_card` — a flat 2% MDR / 18% tax across all
instruments, matching the synthetic generator's own formula — stands in for the real
`rate_card` DB table (Schema section 5.4), which exists and is migrated but has no
seeding, no `POST /rate-cards` endpoint, and no per-instrument realistic rates (real UPI
MDR in India is frequently zero by regulation; cards and wallets differ meaningfully). Fee
variance detection is real and cross-validated at 32/32 precision and recall against
independently-injected `fee_variance` pathology — against *this* rate card. A production
deployment needs the real one.

## 4. The API layer implements the core workflow, not every endpoint in TRD section 2.5

Built and tested: ingest preview/confirm, run creation with idempotency, run detail/
metrics/cancel, exception list/detail/approve/reject/escalate/bulk-approve (with the
confidence-gated bulk-approve refusal), match explorer, audit trail + verify, dev/seed.
Not built: `GET /runs/{run_id}/export`, `GET /rulesets/{version}`, and CSV-formula-injection
neutralisation on export. The exception-approval endpoint also intentionally does not
dispatch on `action` to create the corresponding `match_group` row — it records the human
decision and status transition (both real, both audited), but the specific downstream
effect of each action enum value is not fully wired.

## 5. Frontend covers the three GATE-2-critical screens, not all nine

Per the Implementation Plan's own explicit droppable list: S1 (Runs List) was built as a
bonus; S2 (New Run) is folded into the Mapping Review screen without a separate period/
ruleset-selection step; S4 (Run Progress stepper), S7 (Match Explorer UI), and S8 (Audit
Trail UI) were not built, though their backing API endpoints exist and are tested. The
three screens the plan calls load-bearing — S3 Mapping Review, S5 Run Dashboard, S6/S6b
Exception Queue with detail drawer — are built, keyboard-navigable, and compile cleanly
under TypeScript strict mode.

## 6. Demo deploy and demo recording not produced

Appflow section 4.3's secondary CD target (tag-triggered deploy to Fly.io/Railway) and the
3-minute demo recording (Implementation Plan task 3.9) were not built in this environment
— there is no live URL and no video. The primary reproduction path (`make up && make seed
&& make eval`) is what was actually exercised, repeatedly, against a real Postgres
instance in this build.

## 7. Orchestrator's rate card and match-approval wiring are simplified

The orchestrator (`app/orchestrator/orchestrator.py`) uses the same placeholder rate card
as the eval harness (see #3) rather than reading a run-specific one from the DB. Cooperative
cancel is polled via an audit-log marker rather than a dedicated `cancel_requested` status
value (there is no such enum member in `run_status` — see the fix documented in
`app/api/runs.py`), which works correctly but is a slightly indirect mechanism worth
knowing about if extending it.

## What is NOT a limitation — deliberately verified, not assumed

To be equally clear about what *was* actually proven rather than claimed: the C5 database
constraints (no double allocation, exact balance) were tested by deliberately trying to
violate them; the audit-log hash chain was tested by tampering with a row after the fact
and confirming `verify_chain` catches it; money exactness was proven via 1,000+
hypothesis-generated cases; the ingest pipeline's idempotency was tested by re-submitting
an identical 5,000-row batch and confirming zero duplicates; and the full deterministic
matching + classification pipeline was cross-validated against independently-generated
ground truth at every stage (T4 fee detection: 32/32 precision and recall; exception
classification: 131/131 correct against directly-comparable ground truth; the pathology
table in `README.md`: 12/12 categories, 0 missed). These numbers are not self-reported by
the same code that produced them — the synthetic generator and the matching/classification
engine are independently written and cross-checked.
