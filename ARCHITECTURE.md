# Architecture — Decision Log

This is the *why* behind the structure, plus an honest log of what broke during the build
and how it got fixed. The second half exists because a system that never revealed a wrong
assumption almost certainly wasn't tested hard enough — see PDR persona P4.

## Layering

`domain/` has zero I/O and zero framework imports, enforced by an `import-linter` contract
checked in CI, not just documented. Every domain module is a plain dataclass or pure
function operating on other plain dataclasses (`Money`, `OrderEntity`,
`SettlementLineEntity`, `MatchGroupResult`). `adapters/` translates between the domain and
the outside world (Postgres, the LLM API, CSV files). `app/` wires adapters and domain
together into FastAPI routes, Celery tasks, and the orchestrator.

This paid off directly: the entire T1-T4 matching cascade, the exception classifier, and
the scoring logic were built and tested with zero database connection required — `pytest
tests/unit/` runs in under a second. The slower, DB-backed integration tests exist
specifically to prove the domain logic and the database's own constraints agree with each
other, not to test the domain logic itself.

## The matching cascade builds ONE group per reconciliation unit, not one per tier

The schema's own language (`MATCH_GROUP` + `MATCH_MEMBER` as "a single auditable unit"
tying order(s) to settlement line(s) to a bank credit) doesn't fully specify the merge
mechanics between tiers. The design settled on: T1 creates order<->settlement groups; T2
either attaches a bank_txn to an existing T1 group or merges several T1 groups sharing one
day's UTR into one combined group (the ordinary many-orders-one-credit case); T3 does the
same via bounded allocation. This was necessary, not a style choice — C5's active-
membership constraint forbids an entity from being in two active groups simultaneously, so
a naive "one group per tier" design would have made partial settlements and multi-order
days structurally impossible to represent correctly.

## C5 integrity is a database fact, not an application promise

`uniq_active_member` and `trg_allocation_balances` (migration `0002_c5_allocation_
integrity.py`) make double-allocation and unbalanced groups literally impossible to commit,
independent of whether the application code has a bug. This was worth the extra migration
complexity (a partial unique index can't reference a subquery in Postgres, so it's a
trigger-maintained boolean flag instead) because it was tested by deliberately trying to
violate it from raw SQL, not just by unit-testing the Python code path that's supposed to
prevent it.

## The audit-log hash chain is scoped per run, not global

`GET /runs/{run_id}/audit/verify` needs to prove one run's trail is intact as a
self-contained chain. An earlier version chained every entry in the whole table together
regardless of run — which meant a run's first audit entry pointed at unrelated activity
from a different run, and `verify_chain(run_id=X)` (which correctly starts each run's
chain at `prev_hash=None`) could never reconstruct it. Fixed by scoping the "last hash"
lookup in `append_entry` to `WHERE run_id = :run_id`. Caught by an integration test, not
by inspection — the bug only showed up once two different runs' entries were genuinely
interleaved in the same table.

## The orchestrator is a hand-rolled state machine, not a framework

Twelve explicit states (`INGEST` through `AWAIT_REVIEW`), each transition writing an
audit-log entry. This makes "what did the agent do and why" answerable by reading a table,
not by tracing through a framework's internal callback graph — which is what this track
actually grades. Cooperative cancel and graceful LLM degradation are both just `if`
branches in a linear method, not special framework hooks.

## Eval harness runs in-memory, not against Postgres

`src/milaan/eval/load_batch.py` reads the seeded CSVs directly into domain entities,
bypassing Postgres entirely, while still exercising the real ingest mapping and row-
validation logic. This was a deliberate trade against "test exactly what production does":
`make eval` needs to be fast and dependency-free for CI (matching Appflow's
`LLM_MODE=cached` philosophy of zero network, zero flake), and the Postgres-backed path is
separately, thoroughly tested by `tests/integration/` — including a full run through the
real orchestrator with real persistence. Both paths use the identical domain logic; only
the persistence step differs.

## Bugs found during the build, and what caught them

Kept here because each one shaped a design decision above, and because "we tested it
carefully" is a claim worth backing with specifics rather than asserting.

1. **`duplicate_utr` pathology injection scaled with order volume, not calendar days.**
   The number of distinct settlement dates in a period is bounded by the period length,
   not by record count — injecting one incident per N orders meant that at 5,000 orders
   over ~29 real days, nearly every day collided with another and T2 correctly-but-
   uselessly refused almost the entire batch. Caught by comparing pathology-table
   detection rates against expectations at full scale, not by unit tests (which used
   small batches where the effect wasn't visible). Fixed by capping incidents at the day
   level, independent of order volume (`DUPLICATE_UTR_MAX_INCIDENTS`).

2. **Cascade "unmatched" accounting trusted each tier's self-reported miss set.** A
   settlement line whose UTR simply had no bank counterpart *at all* (as opposed to one
   present but failing the sum check) was never proactively added to any tier's own
   bookkeeping — so a union of "what each tier says it missed" silently under-reported
   real gaps. Fixed by deriving "unmatched" authoritatively from final group membership
   (all-IDs-minus-matched-IDs) instead. Caught by a regression test specifically targeting
   a stray line with a real but bank-less UTR.

3. **`MatchGroupResult.add_member` had "skip if present" semantics.** T1 records a
   settlement line's `allocated_amount` as its `gross` (before any bank credit exists); T2
   needs to update the same line to its `net` once a bank_txn joins the group, because the
   database's balance trigger checks net-of-fee sums, not gross. The original "skip if
   already a member" logic silently dropped that update, so every T2-confirmed group
   failed the balance check by exactly the fee+tax delta. Caught by an end-to-end test
   against real Postgres — the in-memory unit tests hadn't exercised a case where the
   *same* entity got added twice with different amounts. Fixed to upsert.

4. **Scoring's `links_from_groups` cross-producted every order against every settlement
   line in a group.** Correct for a group with one order; wrong once T2 merges several
   orders sharing a day's credit into one combined group — a 5,000-record run produced
   ~715,000 "predicted links" against ~10,000 real ones. Fixed by pairing order<->
   settlement links via matching `payment_id` within the group instead of a blind
   cross-product. The exact same root cause reappeared independently in the exception
   classifier's order-tie and bank-tie checks (a group-level "does this group contain any
   order" boolean instead of "does this group contain *this line's* order") and had to be
   fixed there too — both caught by cross-validating against independently-generated
   ground truth at full scale, not by code review.

5. **Generator's `orphan_bank_credit` ground-truth natural key didn't match the actual
   bank row.** The pathology manifest recorded the bare token (`"ORPHAN00000"`); the real
   narration was `"NEFT CR UNKNOWN ORPHAN00000"`. Every other bank pathology entry
   correctly used the full narration as its natural key; this one didn't, so cross-
   validation silently reported 0/51 detected for a category the classifier was actually
   getting entirely right. Fixed to match the pattern used everywhere else.

6. **Two Postgres-specific type mismatches, both only visible against a real database.**
   `verify_chain` received `entity_id` back from psycopg as a native `UUID` object, not a
   string, and crashed on the first real round-trip. Separately, a 5,166-row settlement
   batch's multi-VALUES INSERT exceeded Postgres's 65,535-bound-parameters-per-statement
   ceiling. Neither is visible in unit tests with mocked or small in-memory data; both
   were caught by insisting on testing against a real Postgres instance at realistic scale
   rather than trusting that "it worked for 3 rows" generalises.

The throughline: every one of these was caught by either (a) testing at the actual target
scale (5,000 records, not 3), or (b) cross-validating against independently-generated
ground truth rather than checking that the code agrees with itself. Neither is exotic —
both are just more expensive than trusting a green test suite at small scale, which is
exactly why it's worth stating plainly that this is where the real bugs were.
