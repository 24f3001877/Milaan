"""Seeded synthetic data generator (Implementation Plan §6.1, task 1.7).

Emits three source CSVs shaped exactly like the real files Milaan ingests — orders,
gateway settlement, bank statement — plus two files the matching engine never sees:

  - `ground_truth.jsonl`   — the authored truth linking orders <-> settlement lines <->
                             bank credits, keyed by natural business identifiers (not DB
                             UUIDs, which don't exist until ingest). Consumed only by the
                             eval harness (PDR persona P4's first sanity check).
  - `pathology_manifest.jsonl` — which natural-key records carry which injected pathology.
                             Real gateway/bank files would never carry this column, so it's
                             a side-channel, not a field smuggled into the CSVs.

C2 (determinism): given the same seed, record count and pathology weights, output is
byte-identical. This is achieved by: a single `random.Random(seed)` instance, strictly
ascending iteration by record index, no `set()` iteration order anywhere in the write path,
and no wall-clock value in any written file (the manifest's `generated_at` field is written
to a separate, unhashed sidecar, never into the deterministic outputs).
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random

from milaan.domain.money import Money
from milaan.domain.schema_fields import BANK_FIELDS, ORDER_FIELDS, SETTLEMENT_FIELDS

ORDER_HEADERS = list(ORDER_FIELDS)
SETTLEMENT_HEADERS = list(SETTLEMENT_FIELDS)
BANK_HEADERS = list(BANK_FIELDS)

INSTRUMENTS = ["upi", "card_debit", "card_credit", "netbanking", "wallet"]
MDR_RATE = Decimal("0.02")
TAX_RATE = Decimal("0.18")
# duplicate_utr is a day-scoped pathology (see _finalise_bank_batches) — capped small and
# independent of record_count, since the number of distinct calendar days in a period
# doesn't grow with order volume the way per-order pathology counts do.
DUPLICATE_UTR_MAX_INCIDENTS = 3


@dataclass
class GeneratedBatch:
    orders: list[dict] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    bank_rows: list[dict] = field(default_factory=list)
    ground_truth: list[dict] = field(default_factory=list)
    pathology_manifest: list[dict] = field(default_factory=list)
    pathology_counts: dict[str, int] = field(default_factory=dict)


def _cents_amount(rng: Random, lo: int = 500, hi: int = 999_999) -> Money:
    return Money(Decimal(rng.randint(lo, hi)) / Decimal(100))


def _compute_fee_tax(gross: Money) -> tuple[Money, Money, Money]:
    fee = Money((gross.amount * MDR_RATE))
    tax = Money((fee.amount * TAX_RATE))
    net = gross - fee - tax
    return fee, tax, net


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_date(d: date) -> str:
    return d.isoformat()


def generate(
    seed: int,
    record_count: int,
    period_start: date,
    period_end: date,
    pathology_weights: dict[str, float],
    pathology_rate: float = 0.12,
) -> GeneratedBatch:
    """Pure generation — no file I/O here, so the byte-identical property can be tested
    directly on the returned structures before touching disk."""
    rng = Random(seed)
    batch = GeneratedBatch()
    period_len = (period_end - period_start).days

    categories = list(pathology_weights.keys())
    weights = list(pathology_weights.values())

    for i in range(record_count):
        order_id = f"ORD{i:07d}"
        payment_id = f"pay{i:010x}"
        settlement_id = f"STL{i:07d}"
        gross = _cents_amount(rng)

        created_offset = rng.randint(0, max(period_len - 4, 0))
        created_hour = rng.randint(6, 22)
        created_minute = rng.randint(0, 59)
        created_at = datetime.combine(period_start, datetime.min.time()) + timedelta(
            days=created_offset, hours=created_hour, minutes=created_minute
        )
        settle_offset = rng.randint(1, 3)
        settled_on = created_at.date() + timedelta(days=settle_offset)
        instrument = rng.choice(INSTRUMENTS)

        pathology: str | None = None
        if rng.random() < pathology_rate:
            pathology = rng.choices(categories, weights=weights, k=1)[0]

        order_row = {
            "order_id": order_id,
            "invoice_no": f"INV{i:07d}",
            "customer_ref": f"cust_{i % 500:04d}",  # pseudonymised, bounded cardinality
            "gross": str(gross.amount),
            "currency": "INR",
            "payment_id": payment_id,
            "order_status": "paid",
            "created_at": _fmt_dt(created_at),
        }

        def emit_clean_pair(settlement_gross: Money, s_payment_id: str = payment_id) -> None:
            fee, tax, net = _compute_fee_tax(settlement_gross)
            batch.settlements.append(
                {
                    "settlement_id": settlement_id,
                    "payment_id": s_payment_id,
                    "order_ref": order_id,
                    "line_type": "payment",
                    "gross": str(settlement_gross.amount),
                    "fee": str(fee.amount),
                    "tax": str(tax.amount),
                    "net": str(net.amount),
                    "instrument": instrument,
                    "settled_on": _fmt_date(settled_on),
                    "utr": "",  # filled in during bank-batch aggregation below
                }
            )
            batch.ground_truth.append(
                {
                    "entity_type_a": "order",
                    "natural_key_a": order_id,
                    "entity_type_b": "settlement_line",
                    "natural_key_b": settlement_id,
                    "expected_allocated_amount": None,
                    "pathology": pathology,
                }
            )

        if pathology is None:
            batch.orders.append(order_row)
            emit_clean_pair(gross)
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )

        elif pathology == "missing_in_bank":
            batch.orders.append(order_row)
            emit_clean_pair(gross)
            # Deliberately NOT added to any bank batch — the credit never arrives.
            _record_pathology(batch, "settlement_line", settlement_id, pathology)

        elif pathology == "missing_in_gateway":
            batch.orders.append(order_row)
            # No settlement line at all.
            _record_pathology(batch, "order", order_id, pathology)

        elif pathology == "amount_mismatch":
            batch.orders.append(order_row)
            drift_cents = rng.randint(100, 5000) * rng.choice([-1, 1])
            mismatched_gross = Money(gross.amount + Decimal(drift_cents) / Decimal(100))
            emit_clean_pair(mismatched_gross)
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )
            _record_pathology(batch, "settlement_line", settlement_id, pathology)

        elif pathology == "fee_variance":
            batch.orders.append(order_row)
            fee, tax, _ = _compute_fee_tax(gross)
            skew_bps = rng.randint(50, 400)  # 0.5%-4% extra fee, beyond typical tolerance
            skewed_fee = Money(fee.amount + gross.amount * Decimal(skew_bps) / Decimal(10000))
            net = gross - skewed_fee - tax
            batch.settlements.append(
                {
                    "settlement_id": settlement_id,
                    "payment_id": payment_id,
                    "order_ref": order_id,
                    "line_type": "payment",
                    "gross": str(gross.amount),
                    "fee": str(skewed_fee.amount),
                    "tax": str(tax.amount),
                    "net": str(net.amount),
                    "instrument": instrument,
                    "settled_on": _fmt_date(settled_on),
                    "utr": "",
                }
            )
            batch.ground_truth.append(
                {
                    "entity_type_a": "order",
                    "natural_key_a": order_id,
                    "entity_type_b": "settlement_line",
                    "natural_key_b": settlement_id,
                    "expected_allocated_amount": None,
                    "pathology": pathology,
                }
            )
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )
            _record_pathology(batch, "settlement_line", settlement_id, pathology)

        elif pathology == "duplicate_utr":
            # The actual collision is injected at day-level, after bank-batch aggregation
            # (see _finalise_bank_batches), bounded by DUPLICATE_UTR_MAX_INCIDENTS rather
            # than by order volume — see that function's docstring for why per-order
            # injection here was a real bug: it scaled with record count while the number
            # of distinct calendar days does not, guaranteeing near-total cascading
            # collisions at realistic record counts. This branch is otherwise identical to
            # the clean path; the order/settlement themselves aren't the injection site.
            batch.orders.append(order_row)
            emit_clean_pair(gross)
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )

        elif pathology == "partial_settlement":
            batch.orders.append(order_row)
            first = Money(gross.amount * Decimal("0.6"))
            second = Money(gross.amount - first.amount)
            for suffix, part_gross in (("A", first), ("B", second)):
                part_id = f"{settlement_id}{suffix}"
                fee, tax, net = _compute_fee_tax(part_gross)
                batch.settlements.append(
                    {
                        "settlement_id": part_id,
                        "payment_id": payment_id,
                        "order_ref": order_id,
                        "line_type": "payment",
                        "gross": str(part_gross.amount),
                        "fee": str(fee.amount),
                        "tax": str(tax.amount),
                        "net": str(net.amount),
                        "instrument": instrument,
                        "settled_on": _fmt_date(settled_on),
                        "utr": "",
                    }
                )
                batch.ground_truth.append(
                    {
                        "entity_type_a": "order",
                        "natural_key_a": order_id,
                        "entity_type_b": "settlement_line",
                        "natural_key_b": part_id,
                        "expected_allocated_amount": None,
                        "pathology": pathology,
                    }
                )
                batch.bank_rows.append(
                    {"_pending_batch_date": settled_on, "_settlement_id": part_id}
                )
                _record_pathology(batch, "settlement_line", part_id, pathology)

        elif pathology == "period_boundary_timing":
            batch.orders.append(order_row)
            pushed_settled_on = period_end + timedelta(days=rng.randint(1, 3))
            fee, tax, net = _compute_fee_tax(gross)
            batch.settlements.append(
                {
                    "settlement_id": settlement_id,
                    "payment_id": payment_id,
                    "order_ref": order_id,
                    "line_type": "payment",
                    "gross": str(gross.amount),
                    "fee": str(fee.amount),
                    "tax": str(tax.amount),
                    "net": str(net.amount),
                    "instrument": instrument,
                    "settled_on": _fmt_date(pushed_settled_on),
                    "utr": "",
                }
            )
            batch.ground_truth.append(
                {
                    "entity_type_a": "order",
                    "natural_key_a": order_id,
                    "entity_type_b": "settlement_line",
                    "natural_key_b": settlement_id,
                    "expected_allocated_amount": None,
                    "pathology": pathology,
                }
            )
            # No bank row this period — the credit belongs to the next period's batch.
            _record_pathology(batch, "settlement_line", settlement_id, pathology)

        elif pathology in ("netted_refund_unlinked", "chargeback_debit_unlinked", "unknown_adjustment"):
            line_type = {
                "netted_refund_unlinked": "refund",
                "chargeback_debit_unlinked": "chargeback",
                "unknown_adjustment": "adjustment",
            }[pathology]
            batch.orders.append(order_row)
            emit_clean_pair(gross)  # the order's own honest settlement, unaffected
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )
            stray_id = f"{settlement_id}X"
            stray_amount = Money(Decimal(rng.randint(500, 50000)) / Decimal(100))
            signed_gross = stray_amount if line_type == "adjustment" else -stray_amount
            batch.settlements.append(
                {
                    "settlement_id": stray_id,
                    "payment_id": "",
                    "order_ref": "",
                    "line_type": line_type,
                    "gross": str(signed_gross.amount),
                    "fee": "0.00",
                    "tax": "0.00",
                    "net": str(signed_gross.amount),
                    "instrument": instrument,
                    "settled_on": _fmt_date(settled_on),
                    "utr": "",
                }
            )
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": stray_id}
            )
            _record_pathology(batch, "settlement_line", stray_id, pathology)

        elif pathology == "ambiguous_multi_candidate":
            batch.orders.append(order_row)
            fee, tax, net = _compute_fee_tax(gross)
            batch.settlements.append(
                {
                    "settlement_id": settlement_id,
                    "payment_id": "",  # malformed/missing at the gateway
                    "order_ref": "",
                    "line_type": "payment",
                    "gross": str(gross.amount),
                    "fee": str(fee.amount),
                    "tax": str(tax.amount),
                    "net": str(net.amount),
                    "instrument": instrument,
                    "settled_on": _fmt_date(settled_on),
                    "utr": "",
                }
            )
            batch.ground_truth.append(
                {
                    "entity_type_a": "order",
                    "natural_key_a": order_id,
                    "entity_type_b": "settlement_line",
                    "natural_key_b": settlement_id,
                    "expected_allocated_amount": None,
                    "pathology": pathology,
                }
            )
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )
            _record_pathology(batch, "settlement_line", settlement_id, pathology)

        elif pathology == "orphan_bank_credit":
            # Handled after the main loop (it has no order/settlement counterpart at all).
            batch.orders.append(order_row)
            emit_clean_pair(gross)
            batch.bank_rows.append(
                {"_pending_batch_date": settled_on, "_settlement_id": settlement_id}
            )
            batch._orphan_credit_requests = getattr(batch, "_orphan_credit_requests", 0) + 1  # type: ignore[attr-defined]

        else:  # pragma: no cover - defensive; every catalogue key is handled above
            raise ValueError(f"Unhandled pathology category: {pathology}")

    _finalise_bank_batches(batch, rng, period_start)
    return batch


def _record_pathology(batch: GeneratedBatch, entity_type: str, natural_key: str, pathology: str) -> None:
    batch.pathology_manifest.append(
        {"entity_type": entity_type, "natural_key": natural_key, "pathology": pathology}
    )
    batch.pathology_counts[pathology] = batch.pathology_counts.get(pathology, 0) + 1


def _finalise_bank_batches(batch: GeneratedBatch, rng: Random, period_start: date) -> None:
    """Aggregate pending settlement->bank references into real bank_txn rows, one credit
    per settlement date (the many-to-one scenario PDR §1.1 names as structural reason #1).
    """
    pending = batch.bank_rows
    batch.bank_rows = []

    by_date: dict[date, list[str]] = {}
    settlement_gross_by_id = {s["settlement_id"]: Decimal(s["net"]) for s in batch.settlements}
    for p in pending:
        d = p["_pending_batch_date"]
        by_date.setdefault(d, []).append(p["_settlement_id"])

    orphan_requests = getattr(batch, "_orphan_credit_requests", 0)

    utr_by_date: dict[date, str] = {}
    narration_by_date: dict[date, str] = {}
    sorted_dates = sorted(by_date)
    for d in sorted_dates:
        utr = f"UTR{d.strftime('%Y%m%d')}0001"
        utr_by_date[d] = utr
        narration_by_date[d] = f"NEFT CR {utr} SETTLEMENT"

    settlement_by_id = {s["settlement_id"]: s for s in batch.settlements}
    bank_row_by_date: dict[date, dict] = {}
    for d in sorted_dates:
        sids = by_date[d]
        utr = utr_by_date[d]
        narration = narration_by_date[d]
        total_credit = sum((settlement_gross_by_id[sid] for sid in sids), Decimal("0"))
        for sid in sids:
            row = settlement_by_id[sid]
            if row["utr"] == "":
                row["utr"] = utr
        bank_row_by_date[d] = {
            "value_date": _fmt_date(d),
            "narration": narration,
            "utr_extracted": utr,  # may be corrupted below by the duplicate_utr injection
            "credit": str(Money(total_credit).amount),
            "debit": "0.00",
            "balance": "",
        }
        for sid in sids:
            # Keyed by the day's narration, not the bare UTR code: this natural key must
            # stay resolvable even after a duplicate_utr incident corrupts utr_extracted
            # below — exactly the field a real bank's own extraction could get wrong,
            # while the raw narration text remains accurate (Schema/pathology note).
            batch.ground_truth.append(
                {
                    "entity_type_a": "settlement_line",
                    "natural_key_a": sid,
                    "entity_type_b": "bank_txn",
                    "natural_key_b": narration,
                    "expected_allocated_amount": str(settlement_gross_by_id[sid]),
                    "pathology": None,
                }
            )

    # duplicate_utr pathology: injected at DAY level, bounded by a small fixed cap rather
    # than by order volume. This was a real bug in an earlier version of this generator —
    # injecting it per-settlement-line meant the incident count scaled with record_count
    # while the number of distinct calendar days does not, so at realistic record counts
    # (5,000+ orders over a ~30-day period) nearly every day collided with another and T2
    # correctly-but-uselessly refused almost the entire batch. A handful of whole-day
    # UTR-field corruptions is what "duplicate UTR" actually looks like in a real gateway
    # export. Only `utr_extracted` is corrupted — the raw narration keeps the day's true
    # UTR text, modelling a bad structured-field extraction rather than a rewritten record.
    num_incidents = min(DUPLICATE_UTR_MAX_INCIDENTS, max(0, (len(sorted_dates) - 1) // 8))
    if num_incidents > 0:
        step = max(1, (len(sorted_dates) - 1) // num_incidents)
        for k in range(num_incidents):
            victim_idx = min(len(sorted_dates) - 1, (k + 1) * step)
            victim_date = sorted_dates[victim_idx]
            collide_with_date = sorted_dates[victim_idx - 1]
            bank_row_by_date[victim_date]["utr_extracted"] = utr_by_date[collide_with_date]
            batch.pathology_manifest.append(
                {
                    "entity_type": "bank_txn",
                    "natural_key": narration_by_date[victim_date],
                    "pathology": "duplicate_utr",
                }
            )
            batch.pathology_counts["duplicate_utr"] = (
                batch.pathology_counts.get("duplicate_utr", 0) + 1
            )

    batch.bank_rows = [bank_row_by_date[d] for d in sorted_dates]

    # orphan_bank_credit pathology: standalone unexplained credits, no settlement backing.
    for k in range(orphan_requests):
        d = period_start + timedelta(days=k)
        token = f"ORPHAN{k:05d}"
        narration = f"NEFT CR UNKNOWN {token}"
        amount = Money(Decimal(rng.randint(1000, 200000)) / Decimal(100))
        batch.bank_rows.append(
            {
                "value_date": _fmt_date(d),
                "narration": narration,
                "utr_extracted": "",
                "credit": str(amount.amount),
                "debit": "0.00",
                "balance": "",
            }
        )
        batch.pathology_manifest.append(
            # Natural key is the full narration string, consistent with every other
            # bank_txn pathology entry (see _finalise_bank_batches above) — the bare
            # token alone doesn't match what any consumer actually keys bank rows by.
            {"entity_type": "bank_txn", "natural_key": narration, "pathology": "orphan_bank_credit"}
        )
        batch.pathology_counts["orphan_bank_credit"] = (
            batch.pathology_counts.get("orphan_bank_credit", 0) + 1
        )

    batch.bank_rows.sort(key=lambda r: (r["value_date"], r["narration"]))


def write_outputs(batch: GeneratedBatch, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def write_csv(name: str, headers: list[str], rows: list[dict]) -> None:
        with (out_dir / name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

    write_csv("orders.csv", ORDER_HEADERS, batch.orders)
    write_csv(
        "gateway_settlement.csv",
        SETTLEMENT_HEADERS,
        sorted(batch.settlements, key=lambda r: r["settlement_id"]),
    )
    write_csv("bank_statement.csv", BANK_HEADERS, batch.bank_rows)

    with (out_dir / "ground_truth.jsonl").open("w", encoding="utf-8") as f:
        for row in batch.ground_truth:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    with (out_dir / "pathology_manifest.jsonl").open("w", encoding="utf-8") as f:
        for row in batch.pathology_manifest:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "order_count": len(batch.orders),
                "settlement_line_count": len(batch.settlements),
                "bank_txn_count": len(batch.bank_rows),
                "pathology_counts": dict(sorted(batch.pathology_counts.items())),
            },
            f,
            indent=2,
            sort_keys=True,
        )
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Milaan synthetic reconciliation data generator")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--records", type=int, default=5000)
    parser.add_argument("--period-start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--period-end", type=date.fromisoformat, default=date(2026, 1, 31))
    parser.add_argument("--pathology-rate", type=float, default=0.12)
    parser.add_argument("--compressed", action="store_true", help="use the 6-category variant")
    parser.add_argument("--out", type=Path, default=Path("data/synthetic"))
    args = parser.parse_args()

    from milaan.adapters.synthetic.pathology import (
        COMPRESSED_CATEGORIES,
        DEFAULT_WEIGHTS,
    )

    weights = (
        {k: 1.0 for k in COMPRESSED_CATEGORIES} if args.compressed else DEFAULT_WEIGHTS
    )

    batch = generate(
        seed=args.seed,
        record_count=args.records,
        period_start=args.period_start,
        period_end=args.period_end,
        pathology_weights=weights,
        pathology_rate=args.pathology_rate,
    )
    write_outputs(batch, args.out)
    print(f"Wrote synthetic batch (seed={args.seed}, records={args.records}) to {args.out}")
    print(f"Pathology counts: {batch.pathology_counts}")


if __name__ == "__main__":
    main()
