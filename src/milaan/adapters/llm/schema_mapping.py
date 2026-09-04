"""LLM schema mapping for unseen headers (Implementation Plan §6.2, task 2.11).

Only called when deterministic mapping (adapters/ingest/mapping.py) leaves a required
field unmapped — an unseen header, not covered by the exact-match or synonym table. The
LLM proposes a mapping with per-field confidence; the caller (ingest preview flow, TRD
§2.5 `POST /ingest/preview`) is responsible for blocking on low confidence pending human
confirmation, same as the deterministic path.
"""

from __future__ import annotations

from milaan.adapters.llm.client import LLMCallRecord, LLMClient
from milaan.adapters.llm.prompts_loader import load_prompt_template
from milaan.adapters.llm.schemas import SchemaMappingProposal
from milaan.domain.schema_fields import FIELDS_BY_SOURCE, REQUIRED_BY_SOURCE


def propose_mapping_with_llm(
    source_type: str,
    unmapped_headers: list[str],
    sample_rows: list[dict[str, str]],
    llm_client: LLMClient,
) -> tuple[SchemaMappingProposal, LLMCallRecord]:
    template = load_prompt_template("schema_map_v1")
    sample_lines = "\n".join(
        ", ".join(f"{h}={row.get(h, '')!r}" for h in unmapped_headers) for row in sample_rows[:5]
    )
    prompt = template.format(
        source_type=source_type,
        canonical_fields=", ".join(FIELDS_BY_SOURCE[source_type]),
        required_fields=", ".join(REQUIRED_BY_SOURCE[source_type]),
        headers=", ".join(unmapped_headers),
        sample_rows=sample_lines or "(no sample rows)",
    )
    return llm_client.complete("schema_map", prompt, SchemaMappingProposal)
