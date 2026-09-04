"""Plain-language explanation generation (Implementation Plan §6.2, task 2.13) — the
AI-assessment panel content in the review drawer (UI/UX §3.3 S6b)."""

from __future__ import annotations

from milaan.adapters.llm.client import LLMCallRecord, LLMClient
from milaan.adapters.llm.prompts_loader import load_prompt_template
from milaan.adapters.llm.schemas import ExplanationResponse, TriageProposal
from milaan.domain.exception_classifier import ExceptionRecord


def explain_exception(
    exception: ExceptionRecord,
    triage: TriageProposal,
    llm_client: LLMClient,
) -> tuple[ExplanationResponse, LLMCallRecord]:
    template = load_prompt_template("explain_v1")
    prompt = template.format(
        category=exception.category,
        hypothesis=triage.hypothesis,
        proposed_action=triage.proposed_action,
        amount_at_risk=exception.amount_at_risk.to_json(),
    )
    return llm_client.complete("explain", prompt, ExplanationResponse)
