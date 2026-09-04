"""Orchestrator states (TRD §2.2): a hand-rolled explicit state machine, deliberately not
a framework like LangChain — auditability is what this track grades, and framework
abstraction makes it harder both to show what the agent decided and to defend it under
questioning. The state machine IS the audit trail.
"""

from __future__ import annotations

from enum import StrEnum


class OrchestratorState(StrEnum):
    INGEST = "INGEST"
    SCHEMA_MAP = "SCHEMA_MAP"
    VALIDATE = "VALIDATE"
    MATCH_T1 = "MATCH_T1"
    MATCH_T2 = "MATCH_T2"
    MATCH_T3 = "MATCH_T3"
    VERIFY_FEES = "VERIFY_FEES"
    CLASSIFY_EXCEPTIONS = "CLASSIFY_EXCEPTIONS"
    TRIAGE = "TRIAGE"
    EXPLAIN = "EXPLAIN"
    PERSIST = "PERSIST"
    METRICS = "METRICS"
    AWAIT_REVIEW = "AWAIT_REVIEW"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


STATE_SEQUENCE = [
    OrchestratorState.INGEST,
    OrchestratorState.SCHEMA_MAP,
    OrchestratorState.VALIDATE,
    OrchestratorState.MATCH_T1,
    OrchestratorState.MATCH_T2,
    OrchestratorState.MATCH_T3,
    OrchestratorState.VERIFY_FEES,
    OrchestratorState.CLASSIFY_EXCEPTIONS,
    OrchestratorState.TRIAGE,
    OrchestratorState.EXPLAIN,
    OrchestratorState.PERSIST,
    OrchestratorState.METRICS,
    OrchestratorState.AWAIT_REVIEW,
]
