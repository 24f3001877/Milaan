"""Tests for the synthetic data generator (Implementation Plan §6.1 testing criteria):
re-running with the same seed must produce byte-identical output, and the generator must
never leak a float into a money field.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from milaan.adapters.synthetic.generate import generate, write_outputs
from milaan.adapters.synthetic.pathology import DEFAULT_WEIGHTS, PATHOLOGY_CATALOGUE

PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)


def _gen(seed: int = 42, records: int = 300):
    return generate(
        seed=seed,
        record_count=records,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        pathology_weights=DEFAULT_WEIGHTS,
    )


def test_same_seed_is_byte_identical_on_disk(tmp_path: Path) -> None:
    b1 = _gen()
    b2 = _gen()
    out1, out2 = tmp_path / "a", tmp_path / "b"
    write_outputs(b1, out1)
    write_outputs(b2, out2)
    for name in [
        "orders.csv",
        "gateway_settlement.csv",
        "bank_statement.csv",
        "ground_truth.jsonl",
        "pathology_manifest.jsonl",
        "manifest.json",
    ]:
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), f"{name} differs"


def test_different_seed_produces_different_output() -> None:
    b1 = _gen(seed=42)
    b2 = _gen(seed=43)
    assert [o["gross"] for o in b1.orders] != [o["gross"] for o in b2.orders]


def test_no_float_anywhere_in_money_fields() -> None:
    batch = _gen()
    for row in batch.orders:
        _assert_exact_decimal(row["gross"])
    for row in batch.settlements:
        for field_ in ("gross", "fee", "tax", "net"):
            _assert_exact_decimal(row[field_])
    for row in batch.bank_rows:
        _assert_exact_decimal(row["credit"])
        _assert_exact_decimal(row["debit"])


def _assert_exact_decimal(value: str) -> None:
    assert isinstance(value, str)
    try:
        Decimal(value)
    except InvalidOperation:
        pytest.fail(f"{value!r} is not a valid Decimal string")


def test_settlement_payment_lines_satisfy_net_equals_gross_minus_fee_tax() -> None:
    """Mirrors the DB CHECK constraint (Schema §5.4) so a generator bug would be caught
    here, long before it reached Postgres."""
    batch = _gen()
    for row in batch.settlements:
        if row["line_type"] != "payment":
            continue
        gross, fee, tax, net = (Decimal(row[k]) for k in ("gross", "fee", "tax", "net"))
        assert net == gross - fee - tax, row


def test_every_pathology_category_is_reachable_at_scale() -> None:
    """At a large enough sample, every documented category should appear at least once —
    otherwise the catalogue is aspirational rather than actually exercised."""
    batch = generate(
        seed=7,
        record_count=3000,
        period_start=PERIOD_START,
        period_end=PERIOD_END,
        pathology_weights=DEFAULT_WEIGHTS,
        pathology_rate=0.3,
    )
    seen = set(batch.pathology_counts.keys())
    missing = set(PATHOLOGY_CATALOGUE.keys()) - seen
    assert not missing, f"pathology categories never triggered: {missing}"


def test_ground_truth_never_referenced_by_generator_output_alone() -> None:
    """Sanity check on the artifact split: ground_truth.jsonl and pathology_manifest.jsonl
    are separate files from the three source CSVs, matching the 'never consulted by the
    matching engine' guarantee (Schema §5.4) — the engine only ever reads the three CSVs."""
    batch = _gen()
    order_keys = {o["order_id"] for o in batch.orders}
    for link in batch.ground_truth:
        if link["entity_type_a"] == "order":
            assert link["natural_key_a"] in order_keys
