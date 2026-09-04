"""Resolves the synthetic generator's `ground_truth.jsonl` (natural-key based, since DB
UUIDs don't exist at generation time) against a specific set of already-constructed
entities, producing the UUID-keyed link set domain/scoring.py compares predictions
against. I/O (file reading) lives here, in eval/, not in domain/.
"""

from __future__ import annotations

import json
from pathlib import Path

from milaan.domain.entities import BankTxnEntity, OrderEntity, SettlementLineEntity
from milaan.domain.scoring import LinkKey, canonical_link_key


def load_ground_truth_links(
    ground_truth_path: Path,
    orders: list[OrderEntity],
    settlement_lines: list[SettlementLineEntity],
    bank_txns: list[BankTxnEntity],
) -> set[LinkKey]:
    type_maps = {
        "order": {o.order_id: o.id for o in orders},
        "settlement_line": {s.settlement_id: s.id for s in settlement_lines},
        # Bank rows have no reliable structured natural key by design (that's the point of
        # duplicate_utr) — narration is the stable one, unaffected by utr_extracted corruption.
        "bank_txn": {b.narration: b.id for b in bank_txns},
    }

    links: set[LinkKey] = set()
    unresolved = 0
    with ground_truth_path.open(encoding="utf-8") as f:
        for raw_line in f:
            entry = json.loads(raw_line)
            id_a = type_maps[entry["entity_type_a"]].get(entry["natural_key_a"])
            id_b = type_maps[entry["entity_type_b"]].get(entry["natural_key_b"])
            if id_a is None or id_b is None:
                unresolved += 1
                continue
            links.add(
                canonical_link_key(entry["entity_type_a"], id_a, entry["entity_type_b"], id_b)
            )
    if unresolved:
        import structlog

        structlog.get_logger().warning(
            "ground_truth_links_unresolved",
            count=unresolved,
            reason="natural key not found among ingested entities",
        )
    return links


def load_pathology_manifest(pathology_manifest_path: Path) -> list[dict]:
    entries = []
    with pathology_manifest_path.open(encoding="utf-8") as f:
        for raw_line in f:
            entries.append(json.loads(raw_line))
    return entries
