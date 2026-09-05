"""Audit log hash-chain computation (Schema §5.4 `audit_log`). Pure function, no I/O, so
the exact same logic that computes a hash on write can independently recompute it on
verify — the chain being intact is a fact about this function's determinism, not about
trusting whatever wrote the row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def compute_entry_hash(
    entry_id: int,
    ts: datetime,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None,
    payload: dict,
    prev_hash: str | None,
) -> str:
    canonical_payload = json.dumps(payload, sort_keys=True, default=str)
    joined = "|".join(
        [
            str(entry_id),
            ts.isoformat(),
            actor,
            action,
            entity_type,
            entity_id or "",
            canonical_payload,
            prev_hash or "",
        ]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
