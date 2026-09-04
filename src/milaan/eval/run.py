"""Eval harness (Implementation Plan §6.2, tasks 2.7-2.9).

`python -m milaan.eval.run` — one command that reads a seeded synthetic batch, runs it
through the real deterministic pipeline (T1-T4 + exception classification) AND the naive
baseline, scores both against the generator's authored ground truth, and emits
`metrics.json` (machine-readable, what `eval/gate.py` checks) and `metrics.md`
(human-readable, what a reviewer reads — PDR persona P4).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from milaan.domain.exception_classifier import classify_exceptions
from milaan.domain.fee_verification import verify_fees
from milaan.domain.matching.baseline import naive_match
from milaan.domain.matching.cascade import run_t1_t2_t3
from milaan.domain.money import Money
from milaan.domain.scoring import score
from milaan.eval.ground_truth import load_ground_truth_links, load_pathology_manifest
from milaan.eval.load_batch import load_batch_from_directory
from milaan.eval.rate_card import RATE_CARD_VERSION, default_rate_card


def run_eval(data_dir: Path, period_start: date, period_end: date) -> dict:
    t0 = time.monotonic()

    batch = load_batch_from_directory(data_dir)
    orders, lines, banks = batch.orders, batch.settlement_lines, batch.bank_txns

    cascade = run_t1_t2_t3(orders, lines, banks)
    bands = default_rate_card(period_start)
    fee_records = verify_fees(lines, bands, RATE_CARD_VERSION)
    exceptions = classify_exceptions(orders, lines, banks, cascade, fee_records, period_start, period_end)

    baseline_groups = naive_match(orders, lines, banks)

    elapsed = time.monotonic() - t0
    throughput = len(orders) / elapsed if elapsed > 0 else float("inf")

    true_links = load_ground_truth_links(data_dir / "ground_truth.jsonl", orders, lines, banks)

    order_payment_id = {o.id: o.payment_id for o in orders}
    settlement_payment_id = {l.id: l.payment_id for l in lines}

    total_settlement_value = Money.sum([l.gross for l in lines]) if lines else Money.zero()
    matched_ids = {
        m.entity_id for g in cascade.groups for m in g.members if m.entity_type == "settlement_line"
    }
    matched_settlement_value = (
        Money.sum([l.gross for l in lines if l.id in matched_ids]) if matched_ids else Money.zero()
    )

    milaan_score = score(
        predicted_groups=cascade.groups, true_links=true_links,
        total_settlement_lines=len(lines), total_settlement_value=total_settlement_value,
        matched_settlement_value=matched_settlement_value,
        exception_count=len(exceptions), total_records=len(orders),
        order_payment_id=order_payment_id, settlement_payment_id=settlement_payment_id,
    )

    baseline_matched_ids = {
        m.entity_id for g in baseline_groups for m in g.members if m.entity_type == "settlement_line"
    }
    baseline_matched_value = (
        Money.sum([l.gross for l in lines if l.id in baseline_matched_ids])
        if baseline_matched_ids else Money.zero()
    )
    baseline_score = score(
        predicted_groups=baseline_groups, true_links=true_links,
        total_settlement_lines=len(lines), total_settlement_value=total_settlement_value,
        matched_settlement_value=baseline_matched_value,
        exception_count=0, total_records=len(orders),
        order_payment_id=order_payment_id, settlement_payment_id=settlement_payment_id,
    )

    pathology_table = _build_pathology_table(data_dir, orders, lines, banks, exceptions)

    flagged_fee_variances = [r for r in fee_records if not r.within_tolerance]
    fee_variance_total = (
        Money.sum([r.delta for r in flagged_fee_variances]) if flagged_fee_variances else Money.zero()
    )

    exceptions_by_category: dict[str, int] = {}
    for e in exceptions:
        exceptions_by_category[e.category] = exceptions_by_category.get(e.category, 0) + 1

    metrics = {
        "run_info": {
            "data_dir": str(data_dir),
            "order_count": len(orders),
            "settlement_line_count": len(lines),
            "bank_txn_count": len(banks),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "rate_card_version": RATE_CARD_VERSION,
        },
        "milaan": _score_to_dict(milaan_score),
        "baseline": _score_to_dict(baseline_score),
        "throughput": {
            "elapsed_seconds": round(elapsed, 4),
            "records_per_second": round(throughput, 1),
        },
        "unexplained_value_pct": round(1 - milaan_score.value_explained_pct, 6),
        "exceptions_by_category": dict(sorted(exceptions_by_category.items())),
        "exception_count": len(exceptions),
        "fee_variance": {
            "flagged_count": len(flagged_fee_variances),
            "total_amount_at_risk": fee_variance_total.to_json(),
        },
        "pathology_table": pathology_table,
        "validation_errors": batch.validation_errors,
    }
    return metrics


def _score_to_dict(s) -> dict:  # noqa: ANN001
    return {
        "auto_match_rate": round(s.auto_match_rate, 6),
        "value_explained_pct": round(s.value_explained_pct, 6),
        "false_match_rate": round(s.false_match_rate, 6),
        "human_touches_per_100": round(s.human_touches_per_100, 3),
        "matched_settlement_lines": s.matched_settlement_lines,
        "total_settlement_lines": s.total_settlement_lines,
        "total_links_predicted": s.total_links_predicted,
        "true_positive_links": s.true_positive_links,
        "false_positive_links": s.false_positive_links,
        "false_negative_links": s.false_negative_links,
    }


def _build_pathology_table(data_dir: Path, orders, lines, banks, exceptions) -> list[dict]:  # noqa: ANN001
    manifest_path = data_dir / "pathology_manifest.jsonl"
    if not manifest_path.exists():
        return []
    manifest = load_pathology_manifest(manifest_path)

    order_id_by_eid = {o.id: o.order_id for o in orders}
    settlement_id_by_eid = {s.id: s.settlement_id for s in lines}
    narration_by_eid = {b.id: b.narration for b in banks}

    def natural_key(entity_type: str, entity_id) -> str | None:  # noqa: ANN001
        if entity_type == "order":
            return order_id_by_eid.get(entity_id)
        if entity_type == "settlement_line":
            return settlement_id_by_eid.get(entity_id)
        return narration_by_eid.get(entity_id)

    detected_keys: set[tuple[str, str, str]] = set()
    for e in exceptions:
        nk = natural_key(e.entity_type, e.entity_id)
        if nk is not None:
            detected_keys.add((e.category, e.entity_type, nk))

    injected_by_category: dict[str, int] = {}
    detected_by_category: dict[str, int] = {}
    for entry in manifest:
        cat = entry["pathology"]
        injected_by_category[cat] = injected_by_category.get(cat, 0) + 1
        if (cat, entry["entity_type"], entry["natural_key"]) in detected_keys:
            detected_by_category[cat] = detected_by_category.get(cat, 0) + 1

    table = []
    for cat in sorted(injected_by_category):
        injected = injected_by_category[cat]
        detected = detected_by_category.get(cat, 0)
        table.append(
            {"pathology": cat, "injected": injected, "detected": detected, "missed": injected - detected}
        )
    return table


def render_metrics_md(metrics: dict) -> str:
    m, b = metrics["milaan"], metrics["baseline"]
    out = [
        "# Milaan — Evaluation Metrics",
        "",
        f"Batch: {metrics['run_info']['order_count']} orders, "
        f"{metrics['run_info']['settlement_line_count']} settlement lines, "
        f"{metrics['run_info']['bank_txn_count']} bank credits "
        f"(period {metrics['run_info']['period_start']} to {metrics['run_info']['period_end']}).",
        "",
        "## Headline: Milaan vs. naive exact-ID baseline",
        "",
        "| Metric | Milaan | Naive baseline |",
        "|---|---|---|",
        f"| Auto-match rate | {m['auto_match_rate']:.2%} | {b['auto_match_rate']:.2%} |",
        f"| Value explained | {m['value_explained_pct']:.2%} | {b['value_explained_pct']:.2%} |",
        f"| False-match rate | {m['false_match_rate']:.4%} | {b['false_match_rate']:.4%} |",
        f"| Human touches / 100 records | {m['human_touches_per_100']:.1f} | n/a |",
        "",
        f"**Throughput:** {metrics['throughput']['records_per_second']:,.0f} records/sec "
        f"({metrics['throughput']['elapsed_seconds']}s for "
        f"{metrics['run_info']['order_count']} records).",
        "",
        f"**Unexplained value:** {metrics['unexplained_value_pct']:.2%}",
        "",
        "## Fee variance",
        "",
        f"{metrics['fee_variance']['flagged_count']} lines flagged beyond tolerance, "
        f"₹{metrics['fee_variance']['total_amount_at_risk']} total amount at risk.",
        "",
        "## Exceptions by category",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    for cat, count in metrics["exceptions_by_category"].items():
        out.append(f"| {cat} | {count} |")

    out += [
        "",
        "## Pathology table (synthetic runs only)",
        "",
        "| Pathology | Injected | Detected | Missed |",
        "|---|---|---|---|",
    ]
    for row in metrics["pathology_table"]:
        out.append(f"| {row['pathology']} | {row['injected']} | {row['detected']} | {row['missed']} |")

    out += [
        "",
        "## Reproduction",
        "",
        "```bash",
        "make seed SEED=42 RECORDS=5000",
        "make eval",
        "```",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Milaan eval harness")
    parser.add_argument("--data-dir", type=Path, default=Path("data/synthetic"))
    parser.add_argument("--period-start", type=date.fromisoformat, default=date(2026, 1, 1))
    parser.add_argument("--period-end", type=date.fromisoformat, default=date(2026, 1, 31))
    parser.add_argument("--out-json", type=Path, default=Path("metrics.json"))
    parser.add_argument("--out-md", type=Path, default=Path("metrics.md"))
    args = parser.parse_args()

    metrics = run_eval(args.data_dir, args.period_start, args.period_end)

    args.out_json.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    args.out_md.write_text(render_metrics_md(metrics))

    m = metrics["milaan"]
    print(f"Milaan auto-match rate: {m['auto_match_rate']:.2%}")
    print(f"Milaan false-match rate: {m['false_match_rate']:.4%}")
    print(f"Baseline auto-match rate: {metrics['baseline']['auto_match_rate']:.2%}")
    print(f"Throughput: {metrics['throughput']['records_per_second']:,.0f} records/sec")
    print(f"Wrote {args.out_json} and {args.out_md}")


if __name__ == "__main__":
    main()
