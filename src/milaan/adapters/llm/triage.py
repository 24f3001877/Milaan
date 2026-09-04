"""LLM exception triage (Implementation Plan §6.2, task 2.12).

Adds a plain-language hypothesis and a proposed action ON TOP of the category the
deterministic classifier already assigned (PDR F4) — it never re-decides the category,
and per C3 it never touches an amount. `valid_record_ids` is the set of natural/business
IDs that genuinely exist in this run; any `referenced_record_ids` the model cites outside
that set causes the proposal to be rejected (TRD §2.4: "Referenced record IDs must exist
in the current run or the proposal is rejected") — escalated to human review rather than
trusted, since a hallucinated ID is exactly the kind of error C3's "recompute everything"
philosophy exists to catch even in prose form.
"""

from __future__ import annotations

import json

from milaan.adapters.llm.client import LLMCallRecord, LLMClient
from milaan.adapters.llm.errors import LLMValidationError
from milaan.adapters.llm.prompts_loader import load_prompt_template
from milaan.adapters.llm.schemas import PROPOSED_ACTIONS, TriageProposal
from milaan.domain.exception_classifier import ExceptionRecord


def triage_exception(
    exception: ExceptionRecord,
    record_fields: dict,
    valid_record_ids: set[str],
    llm_client: LLMClient,
) -> tuple[TriageProposal, LLMCallRecord]:
    template = load_prompt_template("triage_v1")
    prompt = template.format(
        category=exception.category,
        proposed_actions=", ".join(PROPOSED_ACTIONS),
        entity_type=exception.entity_type,
        amount_at_risk=exception.amount_at_risk.to_json(),
        deterministic_trace=json.dumps(exception.deterministic_trace, sort_keys=True),
        record_fields=json.dumps(record_fields, sort_keys=True, default=str),
    )
    proposal, record = llm_client.complete("triage", prompt, TriageProposal)

    unknown_ids = set(proposal.referenced_record_ids) - valid_record_ids
    if unknown_ids:
        raise LLMValidationError(
            f"triage proposal references unknown record ids: {sorted(unknown_ids)} "
            "— rejected rather than trusted; escalate to human review"
        )

    return proposal, record
