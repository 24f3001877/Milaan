"""The orchestrator (Implementation Plan §6.2, task 2.16).

Wires everything built in Phase 2 into one run: ingest -> T1 -> T2 -> T3 -> fee
verification -> exception classification -> LLM triage -> LLM explanation -> persist ->
metrics -> await review. Every transition writes an audit-log entry (the state machine IS
the audit trail, TRD §2.2) via the run-scoped hash chain.

Cooperative cancel: `cancel_check()` is polled before each state transition; a True
result stops the run cleanly at a state boundary rather than mid-operation.

Graceful degradation (C7): if the LLM is unavailable (LLMDisabledError or
LLMCacheMissError), TRIAGE and EXPLAIN stop attempting further calls — exceptions still
get their deterministic category, severity, and amount-at-risk; they simply arrive
uncategorised by hypothesis/action, matching the UI/UX spec's amber "deterministic-only
mode" banner (§3.2).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from milaan.adapters.audit.audit_log import append_entry
from milaan.adapters.db.models import LLMCall
from milaan.adapters.ingest.parsers import read_rows
from milaan.adapters.ingest.service import IngestSummary, ingest_rows
from milaan.adapters.llm.client import LLMClient
from milaan.adapters.llm.errors import LLMCacheMissError, LLMDisabledError, LLMValidationError
from milaan.adapters.llm.explain import explain_exception
from milaan.adapters.llm.schemas import TriageProposal
from milaan.adapters.llm.triage import triage_exception
from milaan.adapters.matching.loader import load_bank_txns, load_orders, load_settlement_lines
from milaan.app.orchestrator.states import OrchestratorState
from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.exception_classifier import ExceptionRecord, classify_exceptions
from milaan.domain.fee_verification import FeeVarianceRecord, verify_fees
from milaan.domain.matching.baseline import naive_match
from milaan.domain.matching.cascade import CascadeResult
from milaan.domain.matching.t1_payment_id import match_t1
from milaan.domain.matching.t2_utr import match_t2
from milaan.domain.matching.t3_allocation import match_t3
from milaan.domain.matching.types import MatchGroupResult
from milaan.domain.money import Money
from milaan.eval.rate_card import RATE_CARD_VERSION, default_rate_card


@dataclass
class SourceFileInput:
    source_type: str
    filename: str
    content: bytes
    mapping: dict


@dataclass
class TriagedException:
    exception: ExceptionRecord
    triage_llm_call_id: uuid.UUID | None = None
    explain_llm_call_id: uuid.UUID | None = None
    hypothesis: str | None = None
    proposed_action: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    explanation: str | None = None


@dataclass
class OrchestratorResult:
    final_state: OrchestratorState
    cancelled: bool = False
    llm_degraded: bool = False
    groups: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    fee_records: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        session: Session,
        run_id: uuid.UUID,
        llm_client: LLMClient,
        period_start: date,
        period_end: date,
        ruleset_version: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.session = session
        self.run_id = run_id
        self.llm_client = llm_client
        self.period_start = period_start
        self.period_end = period_end
        self.ruleset_version = ruleset_version
        self.cancel_check = cancel_check or (lambda: False)

    def _transition(self, state: OrchestratorState, **payload) -> None:
        self.session.execute(
            text("UPDATE recon_run SET orchestrator_state = :state WHERE id = :run_id"),
            {"state": state.value, "run_id": str(self.run_id)},
        )
        append_entry(
            self.session,
            actor="orchestrator",
            action="state_transition",
            entity_type="recon_run",
            entity_id=self.run_id,
            payload={"state": state.value, **payload},
            run_id=self.run_id,
        )
        self.session.commit()

    def _cancelled(self) -> bool:
        if self.cancel_check():
            self._transition(OrchestratorState.CANCELLED)
            self.session.execute(
                text("UPDATE recon_run SET status = 'cancelled' WHERE id = :run_id"),
                {"run_id": str(self.run_id)},
            )
            self.session.commit()
            return True
        return False

    def run(self, sources: list[SourceFileInput]) -> OrchestratorResult:
        result = OrchestratorResult(final_state=OrchestratorState.INGEST)
        run_started = time.monotonic()

        self._transition(OrchestratorState.INGEST)
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.SCHEMA_MAP)
        ingest_summaries: dict[str, IngestSummary] = {}
        for src in sources:
            sfid = self._ensure_source_file_row(src)
            rows = read_rows(src.filename, src.content)
            summary = ingest_rows(
                self.session, self.run_id, sfid, src.source_type, rows, src.mapping
            )
            self.session.commit()
            ingest_summaries[src.source_type] = summary

        self._transition(
            OrchestratorState.VALIDATE,
            ingest_summaries={
                k: {
                    "total": v.total_rows,
                    "inserted": v.inserted,
                    "errors": len(v.validation_errors),
                }
                for k, v in ingest_summaries.items()
            },
        )
        if self._cancelled():
            return self._as_cancelled(result)

        orders = load_orders(self.session, self.run_id)
        settlement_lines = load_settlement_lines(self.session, self.run_id)
        bank_txns = load_bank_txns(self.session, self.run_id)

        # Throughput is measured over the deterministic core only (load -> classify), the
        # same span eval/run.py times, so the API-reported rec/s is comparable with the
        # headline number in metrics.md rather than being deflated by LLM latency.
        deterministic_started = time.monotonic()

        self._transition(OrchestratorState.MATCH_T1)
        t1 = match_t1(orders, settlement_lines)
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.MATCH_T2, t1_groups=len(t1.groups))
        t2 = match_t2(settlement_lines, bank_txns, t1.groups)
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.MATCH_T3, t2_groups=len(t2.groups))
        groups_with_bank_tie = [g for g in t2.groups if g.member_ids("bank_txn")]
        lines_with_bank_tie = {
            eid for g in groups_with_bank_tie for eid in g.member_ids("settlement_line")
        }
        banks_already_tied = {eid for g in groups_with_bank_tie for eid in g.member_ids("bank_txn")}
        still_unmatched_lines = [sl for sl in settlement_lines if sl.id not in lines_with_bank_tie]
        still_unmatched_banks = [b for b in bank_txns if b.id not in banks_already_tied]
        t3 = match_t3(still_unmatched_lines, still_unmatched_banks, t2.groups)
        if self._cancelled():
            return self._as_cancelled(result)

        result.groups = t3.groups

        self._transition(OrchestratorState.VERIFY_FEES, group_count=len(t3.groups))
        bands = default_rate_card(self.period_start)
        fee_records = verify_fees(settlement_lines, bands, RATE_CARD_VERSION)
        result.fee_records = fee_records
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.CLASSIFY_EXCEPTIONS)
        cascade_result = _cascade_result_from(t3.groups, settlement_lines, bank_txns, t1, t2)
        exceptions = classify_exceptions(
            orders,
            settlement_lines,
            bank_txns,
            cascade_result,
            fee_records,
            self.period_start,
            self.period_end,
        )
        triaged = [TriagedException(exception=e) for e in exceptions]
        deterministic_elapsed = time.monotonic() - deterministic_started
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.TRIAGE, exception_count=len(triaged))
        settlement_by_id = {s.id: s for s in settlement_lines}
        order_by_id = {o.id: o for o in orders}
        bank_by_id = {b.id: b for b in bank_txns}
        valid_ids = (
            {s.settlement_id for s in settlement_lines}
            | {o.order_id for o in orders}
            | {b.narration for b in bank_txns}
        )

        llm_calls_to_persist: list[dict] = []
        for te in triaged:
            if self.cancel_check():
                break
            record_fields = _record_fields_for(
                te.exception, settlement_by_id, order_by_id, bank_by_id
            )
            try:
                proposal, call_record = triage_exception(
                    te.exception, record_fields, valid_ids, self.llm_client
                )
            except (LLMDisabledError, LLMCacheMissError):
                result.llm_degraded = True
                break
            except LLMValidationError:
                continue
            te.hypothesis = proposal.hypothesis
            te.proposed_action = proposal.proposed_action
            te.confidence = proposal.confidence
            te.rationale = proposal.rationale
            call_id = uuid.uuid4()
            te.triage_llm_call_id = call_id
            llm_calls_to_persist.append(_llm_call_row(call_id, self.run_id, call_record))

        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.EXPLAIN)
        if not result.llm_degraded:
            for te in triaged:
                if te.hypothesis is None or self.cancel_check():
                    continue
                triage_proposal = TriageProposal(
                    hypothesis=te.hypothesis,
                    proposed_action=te.proposed_action,
                    confidence=te.confidence,
                    rationale=te.rationale,
                    referenced_record_ids=[],
                )
                try:
                    explanation, call_record = explain_exception(
                        te.exception, triage_proposal, self.llm_client
                    )
                except (LLMDisabledError, LLMCacheMissError):
                    result.llm_degraded = True
                    break
                te.explanation = explanation.explanation
                call_id = uuid.uuid4()
                te.explain_llm_call_id = call_id
                llm_calls_to_persist.append(_llm_call_row(call_id, self.run_id, call_record))

        result.exceptions = triaged
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.PERSIST, llm_degraded=result.llm_degraded)
        self._persist_llm_calls(llm_calls_to_persist)
        self._persist_groups(t3.groups)
        self._persist_fee_variances(fee_records)
        self._persist_exceptions(triaged)
        self.session.commit()
        if self._cancelled():
            return self._as_cancelled(result)

        self._transition(OrchestratorState.METRICS)
        metrics = self._compute_metrics(
            orders=orders,
            settlement_lines=settlement_lines,
            bank_txns=bank_txns,
            groups=t3.groups,
            triaged=triaged,
            fee_records=fee_records,
            deterministic_elapsed=deterministic_elapsed,
            run_elapsed=time.monotonic() - run_started,
        )
        result.metrics = metrics
        self.session.execute(
            text(
                "UPDATE recon_run SET metrics = CAST(:metrics AS JSONB), record_count = :rc "
                "WHERE id = :run_id"
            ),
            {"metrics": json.dumps(metrics), "rc": len(orders), "run_id": str(self.run_id)},
        )
        self.session.commit()

        self._transition(OrchestratorState.AWAIT_REVIEW)
        self.session.execute(
            text(
                "UPDATE recon_run SET status = 'awaiting_review', finished_at = now() "
                "WHERE id = :run_id"
            ),
            {"run_id": str(self.run_id)},
        )
        self.session.commit()

        result.final_state = OrchestratorState.AWAIT_REVIEW
        return result

    def _as_cancelled(self, result: OrchestratorResult) -> OrchestratorResult:
        result.final_state = OrchestratorState.CANCELLED
        result.cancelled = True
        return result

    def _ensure_source_file_row(self, src: SourceFileInput) -> uuid.UUID:
        existing = self.session.execute(
            text(
                "SELECT id FROM data_source_file "
                "WHERE run_id = :run_id AND source_type = :source_type"
            ),
            {"run_id": str(self.run_id), "source_type": src.source_type},
        ).fetchone()
        if existing:
            return existing.id
        sfid = uuid.uuid4()
        content_hash = hashlib.sha256(src.content).hexdigest()
        self.session.execute(
            text(
                "INSERT INTO data_source_file (id, run_id, source_type, filename, "
                "content_sha256, row_count, ingested_at) VALUES "
                "(:id, :run_id, :st, :fn, :hash, 0, now())"
            ),
            {
                "id": str(sfid),
                "run_id": str(self.run_id),
                "st": src.source_type,
                "fn": src.filename,
                "hash": content_hash,
            },
        )
        self.session.commit()
        return sfid

    def _persist_llm_calls(self, rows: list[dict]) -> None:
        if not rows:
            return
        stmt = pg_insert(LLMCall).values(rows)
        self.session.execute(stmt)

    def _persist_groups(self, groups: list[MatchGroupResult]) -> None:
        for g in groups:
            gid = uuid.uuid4()
            self.session.execute(
                text(
                    "INSERT INTO match_group (id, run_id, tier, confidence, status, rule_id, "
                    "ruleset_version, created_at) VALUES "
                    "(:id, :run_id, :tier, :conf, 'pending_review', :rule_id, :rv, now())"
                ),
                {
                    "id": str(gid),
                    "run_id": str(self.run_id),
                    "tier": g.tier,
                    "conf": str(g.confidence),
                    "rule_id": g.rule_id,
                    "rv": self.ruleset_version,
                },
            )
            for m in g.members:
                self.session.execute(
                    text(
                        "INSERT INTO match_member (id, group_id, entity_type, entity_id, "
                        "allocated_amount, run_id) VALUES (:id, :gid, :et, :eid, :amt, :run_id)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "gid": str(gid),
                        "et": m.entity_type,
                        "eid": str(m.entity_id),
                        "amt": str(m.allocated_amount.amount),
                        "run_id": str(self.run_id),
                    },
                )

    def _persist_fee_variances(self, records: list[FeeVarianceRecord]) -> None:
        for r in records:
            self.session.execute(
                text(
                    "INSERT INTO fee_variance (id, run_id, settlement_line_id, expected_fee, "
                    "expected_tax, reported_fee, reported_tax, delta, rate_card_version, "
                    "within_tolerance, instrument_resolved) VALUES "
                    "(:id, :run_id, :slid, :ef, :et, :rf, :rt, :delta, :rv, :wt, :inst)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "run_id": str(self.run_id),
                    "slid": str(r.settlement_line_id),
                    "ef": str(r.expected_fee.amount),
                    "et": str(r.expected_tax.amount),
                    "rf": str(r.reported_fee.amount),
                    "rt": str(r.reported_tax.amount),
                    "delta": str(r.delta.amount),
                    "rv": r.rate_card_version,
                    "wt": r.within_tolerance,
                    "inst": r.instrument_resolved,
                },
            )

    def _persist_exceptions(self, triaged: list[TriagedException]) -> None:
        for te in triaged:
            e = te.exception
            self.session.execute(
                text(
                    "INSERT INTO exception_item (id, run_id, category, severity, entity_type, "
                    "entity_id, amount_at_risk, deterministic_trace, hypothesis, proposed_action, "
                    "confidence, rationale, llm_call_id, status) VALUES "
                    "(:id, :run_id, :cat, :sev, :et, :eid, :amt, CAST(:trace AS JSONB), :hyp, "
                    ":action, :conf, :rat, :llm_call_id, 'open')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "run_id": str(self.run_id),
                    "cat": e.category,
                    "sev": e.severity,
                    "et": e.entity_type,
                    "eid": str(e.entity_id),
                    "amt": str(e.amount_at_risk.amount),
                    "trace": json.dumps(e.deterministic_trace, default=str),
                    "hyp": te.hypothesis,
                    "action": te.proposed_action,
                    "conf": te.confidence,
                    "rat": te.rationale,
                    "llm_call_id": str(te.triage_llm_call_id) if te.triage_llm_call_id else None,
                },
            )

    def _compute_metrics(
        self,
        orders: list[OrderEntity],
        settlement_lines: list[SettlementLineEntity],
        bank_txns: list[BankTxnEntity],
        groups: list[MatchGroupResult],
        triaged: list[TriagedException],
        fee_records: list[FeeVarianceRecord],
        deterministic_elapsed: float,
        run_elapsed: float,
    ) -> dict:
        """The payload behind S5 Run Dashboard (UI/UX §3.3).

        Deliberately absent here, and absent by necessity rather than omission:
        `false_match_rate` and `pathology_table`. Both are scored against the synthetic
        generator's authored ground truth, which a run created from uploaded files does not
        have — there is no correct answer to compare against. They are reported by
        `python -m milaan.eval.run` on a seeded batch instead, and the dashboard says so
        rather than rendering an em-dash that could be misread as "zero".
        """
        matched_ids = {
            m.entity_id for g in groups for m in g.members if m.entity_type == "settlement_line"
        }
        matched_value = (
            Money.sum([s.gross for s in settlement_lines if s.id in matched_ids])
            if matched_ids
            else Money.zero()
        )
        total_value = (
            Money.sum([s.gross for s in settlement_lines]) if settlement_lines else Money.zero()
        )
        auto_match_rate = len(matched_ids) / len(settlement_lines) if settlement_lines else 0.0
        value_explained_pct = (
            round(float(matched_value.amount / total_value.amount), 6)
            if total_value.amount != 0
            else 0.0
        )

        # The naive exact-ID baseline the headline claim is measured against (UI/UX §3.3:
        # "the most persuasive object in the product"). Recomputed here rather than stored,
        # because it is pure domain logic over records already in memory and costs one
        # linear pass — see domain/matching/baseline.py for why it scores poorly.
        baseline_groups = naive_match(orders, settlement_lines, bank_txns)
        baseline_matched_ids = {
            m.entity_id
            for g in baseline_groups
            for m in g.members
            if m.entity_type == "settlement_line"
        }
        baseline_matched_value = (
            Money.sum([s.gross for s in settlement_lines if s.id in baseline_matched_ids])
            if baseline_matched_ids
            else Money.zero()
        )

        matched_by_tier: dict[str, int] = {}
        for g in groups:
            matched_by_tier[g.tier] = matched_by_tier.get(g.tier, 0) + 1

        # Three-way coverage, reported per side. `auto_match_rate` is a settlement-line
        # rate, so on its own it says nothing about how much of the bank statement was
        # actually tied — the dashboard shows all three so the headline cannot be misread
        # as "the bank side reconciled too".
        matched_order_ids = {
            m.entity_id for g in groups for m in g.members if m.entity_type == "order"
        }
        matched_bank_ids = {
            m.entity_id for g in groups for m in g.members if m.entity_type == "bank_txn"
        }

        exceptions_by_category: dict[str, int] = {}
        for te in triaged:
            cat = te.exception.category
            exceptions_by_category[cat] = exceptions_by_category.get(cat, 0) + 1

        flagged_fees = [r for r in fee_records if not r.within_tolerance]
        fee_variance_total = (
            Money.sum([r.delta for r in flagged_fees]) if flagged_fees else Money.zero()
        )

        return {
            "auto_match_rate": round(auto_match_rate, 6),
            "value_explained_pct": value_explained_pct,
            "unexplained_value_pct": round(1 - value_explained_pct, 6),
            "exception_count": len(triaged),
            "human_touches_per_100": (
                round(len(triaged) / len(orders) * 100, 3) if orders else 0.0
            ),
            "llm_degraded": any(te.hypothesis is None for te in triaged) if triaged else False,
            "matched_settlement_lines": len(matched_ids),
            "total_settlement_lines": len(settlement_lines),
            "baseline": {
                "auto_match_rate": round(
                    len(baseline_matched_ids) / len(settlement_lines) if settlement_lines else 0.0,
                    6,
                ),
                "value_explained_pct": round(
                    float(baseline_matched_value.amount / total_value.amount)
                    if total_value.amount != 0
                    else 0.0,
                    6,
                ),
                "matched_settlement_lines": len(baseline_matched_ids),
            },
            "matched_by_tier": dict(sorted(matched_by_tier.items())),
            "exceptions_by_category": dict(sorted(exceptions_by_category.items())),
            "fee_variance": {
                "flagged_count": len(flagged_fees),
                "total_amount_at_risk": fee_variance_total.to_json(),
            },
            "throughput": {
                "records_per_second": (
                    round(len(orders) / deterministic_elapsed, 1)
                    if deterministic_elapsed > 0
                    else 0.0
                ),
                "elapsed_seconds": round(deterministic_elapsed, 4),
                "run_elapsed_seconds": round(run_elapsed, 4),
            },
            "record_counts": {
                "orders": len(orders),
                "settlement_lines": len(settlement_lines),
                "bank_txns": len(bank_txns),
            },
            "coverage": {
                "orders_matched": len(matched_order_ids),
                "settlement_lines_matched": len(matched_ids),
                "bank_txns_matched": len(matched_bank_ids),
            },
        }


def _cascade_result_from(groups, settlement_lines, bank_txns, t1, t2) -> CascadeResult:
    matched_settlement_ids = {eid for g in groups for eid in g.member_ids("settlement_line")}
    matched_bank_ids = {eid for g in groups for eid in g.member_ids("bank_txn")}
    return CascadeResult(
        groups=groups,
        unmatched_settlement_line_ids={s.id for s in settlement_lines} - matched_settlement_ids,
        unmatched_bank_txn_ids={b.id for b in bank_txns} - matched_bank_ids,
        ambiguous_payment_ids=t1.ambiguous_payment_ids,
        ambiguous_utrs=t2.ambiguous_utrs,
    )


def _record_fields_for(exc: ExceptionRecord, settlement_by_id, order_by_id, bank_by_id) -> dict:
    if exc.entity_type == "settlement_line":
        s = settlement_by_id.get(exc.entity_id)
        return {"settlement_id": s.settlement_id, "payment_id": s.payment_id} if s else {}
    if exc.entity_type == "order":
        o = order_by_id.get(exc.entity_id)
        return {"order_id": o.order_id, "payment_id": o.payment_id} if o else {}
    b = bank_by_id.get(exc.entity_id)
    return {"narration": b.narration} if b else {}


def _llm_call_row(call_id: uuid.UUID, run_id: uuid.UUID, call_record) -> dict:
    from datetime import UTC, datetime

    return {
        "id": call_id,
        "run_id": run_id,
        "purpose": call_record.purpose,
        "model": call_record.model,
        "prompt_version": call_record.prompt_version,
        "prompt_sha256": call_record.prompt_sha256,
        "request_payload": call_record.request_payload,
        "response_payload": call_record.response_payload,
        "input_tokens": call_record.input_tokens,
        "output_tokens": call_record.output_tokens,
        "cost_micros": call_record.cost_micros,
        "latency_ms": call_record.latency_ms,
        "was_cached": call_record.was_cached,
        "validation_attempts": call_record.validation_attempts,
        "validation_failed": call_record.validation_failed,
        "created_at": datetime.now(UTC),
    }
