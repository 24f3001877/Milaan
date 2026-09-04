"""Row-hash idempotency key.

SHA-256 of the row's canonical field values — the idempotency key that makes re-submitting
a batch safe (Schema §5.1). Computed here, in domain/, so the same function backs both
ingest (adapters/ingest) and any future re-validation tooling, with no dependency on how
the row arrived (CSV, XLSX, or a future direct-API source).
"""

from __future__ import annotations

import hashlib


def compute_row_hash(mapped_row: dict[str, str], canonical_fields: tuple[str, ...]) -> str:
    """Deterministic hash over the row's canonical fields, in a fixed field order so the
    hash never depends on source-column ordering — only on content."""
    parts = [f"{field}={mapped_row.get(field, '') or ''}" for field in canonical_fields]
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
