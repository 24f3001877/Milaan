"""Unit tests for the LLM-powered features built on LLMClient (Implementation Plan §6.2,
tasks 2.11-2.13). Each test populates a cache entry keyed by the EXACT prompt the module
under test builds, then exercises it in cached mode — no network, fully deterministic.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from milaan.adapters.llm.cache import prompt_hash
from milaan.adapters.llm.client import DEFAULT_MODEL, LLMClient
from milaan.adapters.llm.errors import LLMValidationError
from milaan.adapters.llm.explain import explain_exception
from milaan.adapters.llm.prompts_loader import load_prompt_template
from milaan.adapters.llm.schema_mapping import propose_mapping_with_llm
from milaan.adapters.llm.schemas import PROPOSED_ACTIONS, TriageProposal
from milaan.adapters.llm.triage import triage_exception
from milaan.domain.exception_classifier import ExceptionRecord
from milaan.domain.money import Money
from milaan.domain.schema_fields import FIELDS_BY_SOURCE, REQUIRED_BY_SOURCE


def _cache(cache_dir: Path, prompt: str, response: dict) -> None:
    key = prompt_hash(DEFAULT_MODEL, "v1", prompt)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({"response": response, "input_tokens": 10, "output_tokens": 10})
    )


def test_propose_mapping_with_llm_returns_validated_proposal(tmp_path: Path) -> None:
    unmapped = ["Merchant Txn Ref"]
    sample_rows = [{"Merchant Txn Ref": "pay0000001"}]
    template = load_prompt_template("schema_map_v1")
    sample_lines = "\n".join(
        ", ".join(f"{h}={row.get(h, '')!r}" for h in unmapped) for row in sample_rows
    )
    prompt = template.format(
        source_type="orders",
        canonical_fields=", ".join(FIELDS_BY_SOURCE["orders"]),
        required_fields=", ".join(REQUIRED_BY_SOURCE["orders"]),
        headers=", ".join(unmapped),
        sample_rows=sample_lines,
    )
    _cache(
        tmp_path,
        prompt,
        {
            "mappings": [
                {
                    "source_column": "Merchant Txn Ref",
                    "canonical_field": "payment_id",
                    "confidence": 0.88,
                }
            ],
            "unmapped_columns": [],
            "reasoning": "Resembles a transaction identifier.",
        },
    )
    client = LLMClient(mode="cached", cache_dir=tmp_path)
    proposal, record = propose_mapping_with_llm("orders", unmapped, sample_rows, client)
    assert proposal.mappings[0].canonical_field == "payment_id"
    assert record.purpose == "schema_map"


def _make_exception() -> ExceptionRecord:
    return ExceptionRecord(
        category="missing_in_bank",
        severity="medium",
        entity_type="settlement_line",
        entity_id=uuid.uuid4(),
        amount_at_risk=Money("500.00"),
        deterministic_trace={
            "reason": "valid payment_id, but no bank credit ever covers this settlement"
        },
    )


def _triage_prompt(exc: ExceptionRecord, record_fields: dict) -> str:
    template = load_prompt_template("triage_v1")
    return template.format(
        category=exc.category,
        proposed_actions=", ".join(PROPOSED_ACTIONS),
        entity_type=exc.entity_type,
        amount_at_risk=exc.amount_at_risk.to_json(),
        deterministic_trace=json.dumps(exc.deterministic_trace, sort_keys=True),
        record_fields=json.dumps(record_fields, sort_keys=True, default=str),
    )


def test_triage_exception_returns_validated_proposal(tmp_path: Path) -> None:
    exc = _make_exception()
    record_fields = {"settlement_id": "STL001", "payment_id": "pay001"}
    prompt = _triage_prompt(exc, record_fields)
    _cache(
        tmp_path,
        prompt,
        {
            "hypothesis": "No bank credit arrived for this settlement this period.",
            "proposed_action": "flag_missing_in_bank",
            "confidence": 0.8,
            "rationale": "STL001 has payment_id pay001 but no bank_txn group.",
            "referenced_record_ids": ["STL001"],
        },
    )
    client = LLMClient(mode="cached", cache_dir=tmp_path)
    proposal, record = triage_exception(exc, record_fields, {"STL001", "pay001"}, client)
    assert proposal.proposed_action == "flag_missing_in_bank"
    assert record.purpose == "triage"


def test_triage_exception_rejects_hallucinated_record_id(tmp_path: Path) -> None:
    exc = _make_exception()
    record_fields = {"settlement_id": "STL001"}
    prompt = _triage_prompt(exc, record_fields)
    _cache(
        tmp_path,
        prompt,
        {
            "hypothesis": "test",
            "proposed_action": "escalate_to_human",
            "confidence": 0.5,
            "rationale": "test",
            "referenced_record_ids": ["DOES-NOT-EXIST"],
        },
    )
    client = LLMClient(mode="cached", cache_dir=tmp_path)
    with pytest.raises(LLMValidationError, match="unknown record ids"):
        triage_exception(exc, record_fields, {"STL001"}, client)


def test_triage_exception_never_carries_an_amount_field() -> None:
    """Structural check for C3: the TriageProposal schema has no monetary field at all."""
    field_names = set(TriageProposal.model_fields.keys())
    for forbidden in ("amount", "gross", "net", "fee", "tax", "credit", "delta"):
        assert forbidden not in field_names


def test_explain_exception_returns_prose(tmp_path: Path) -> None:
    exc = _make_exception()
    triage = TriageProposal(
        hypothesis="No bank credit arrived.",
        proposed_action="flag_missing_in_bank",
        confidence=0.8,
        rationale="see trace",
        referenced_record_ids=[],
    )
    template = load_prompt_template("explain_v1")
    prompt = template.format(
        category=exc.category,
        hypothesis=triage.hypothesis,
        proposed_action=triage.proposed_action,
        amount_at_risk=exc.amount_at_risk.to_json(),
    )
    _cache(
        tmp_path,
        prompt,
        {
            "explanation": "This payment settled but the money never showed up in the bank "
            "statement this period, so it needs a human decision."
        },
    )
    client = LLMClient(mode="cached", cache_dir=tmp_path)
    response, record = explain_exception(exc, triage, client)
    assert "bank statement" in response.explanation
    assert record.purpose == "explain"
