"""Header fingerprinting and deterministic schema mapping.

Covers task 1.8's "deterministic mapping for known layouts" — an exact canonical match or
a known synonym resolves without ever calling the LLM. Unseen headers that resolve no
required field fall through with `method="unmapped"` and block ingest; the LLM-proposed
fallback for genuinely novel layouts is Phase 2 (task 2.11), not built yet — see
TRD §2.5 `POST /ingest/preview` and UI/UX §3.3 S3.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from milaan.domain.schema_fields import FIELDS_BY_SOURCE, REQUIRED_BY_SOURCE

# A modest, honestly-scoped synonym table for common real-world header variants. This is
# not meant to be exhaustive — that's precisely why the LLM layer exists for anything not
# listed here (PDR §1.1: "LLM proposes a mapping for unseen layouts").
SYNONYMS_BY_SOURCE: dict[str, dict[str, str]] = {
    "orders": {
        "order no": "order_id", "orderid": "order_id",
        "invoice number": "invoice_no", "invoice #": "invoice_no",
        "customer": "customer_ref", "customer id": "customer_ref",
        "amount": "gross", "order amount": "gross", "order value": "gross",
        "curr": "currency",
        "txn id": "payment_id", "transaction id": "payment_id", "payment ref": "payment_id",
        "status": "order_status",
        "order date": "created_at", "date": "created_at", "created": "created_at",
    },
    "gateway_settlement": {
        "settlement no": "settlement_id",
        "utr no": "utr", "utr number": "utr",
        "txn id": "payment_id", "transaction id": "payment_id",
        "merchant order id": "order_ref",
        "type": "line_type", "entry type": "line_type",
        "amount": "gross", "settlement amount": "gross", "transaction amount": "gross",
        "mdr": "fee", "gateway fee": "fee", "processing fee": "fee",
        "gst": "tax", "tax amount": "tax",
        "net amount": "net", "settled amount": "net",
        "payment mode": "instrument", "mode": "instrument", "payment method": "instrument",
        "settlement date": "settled_on", "value date": "settled_on",
    },
    "bank_statement": {
        "txn date": "value_date", "value dt": "value_date", "date": "value_date",
        "description": "narration", "particulars": "narration", "remarks": "narration",
        "ref no": "utr_extracted", "cheque/ref no": "utr_extracted", "utr": "utr_extracted",
        "deposit": "credit", "cr": "credit", "deposit amt": "credit",
        "withdrawal": "debit", "dr": "debit", "withdrawal amt": "debit",
        "closing balance": "balance", "running balance": "balance",
    },
}


def normalize_header(header: str) -> str:
    return " ".join(header.strip().lower().replace("-", " ").replace("_", " ").split())


def fingerprint(headers: list[str]) -> str:
    """Hash of the normalised header row — the key `schema_mapping` caches confirmed
    mappings under (Schema §5.4), so a renamed column costs one confirmation click."""
    normalized = [normalize_header(h) for h in headers]
    joined = "|".join(normalized)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class MappingResult:
    mapping: dict[str, str]  # source column -> canonical field
    field_confidence: dict[str, float]
    overall_confidence: float
    method: str  # "deterministic" | "unmapped"
    unmapped_required: list[str] = field(default_factory=list)


def propose_mapping(source_type: str, headers: list[str]) -> MappingResult:
    canonical_fields = set(FIELDS_BY_SOURCE[source_type])
    required = set(REQUIRED_BY_SOURCE[source_type])
    synonyms = SYNONYMS_BY_SOURCE[source_type]

    mapping: dict[str, str] = {}
    field_confidence: dict[str, float] = {}
    for header in headers:
        norm = normalize_header(header)
        exact_candidate = norm.replace(" ", "_")
        if exact_candidate in canonical_fields:
            mapping[header] = exact_candidate
            field_confidence[header] = 1.0
        elif norm in synonyms:
            mapping[header] = synonyms[norm]
            field_confidence[header] = 0.90
        # else: left unmapped — a genuinely unseen column, deferred to the LLM layer.

    mapped_canonical = set(mapping.values())
    unmapped_required = sorted(required - mapped_canonical)
    overall_confidence = (
        1.0 if not unmapped_required and mapping
        else len(mapped_canonical & required) / max(len(required), 1)
    )
    method = "deterministic" if not unmapped_required else "unmapped"
    return MappingResult(mapping, field_confidence, overall_confidence, method, unmapped_required)
