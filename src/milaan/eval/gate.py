"""Eval regression gate (Implementation Plan §6.2, task 2.9; Appflow §4.2, CI job 3).

`python -m milaan.eval.gate metrics.json` reads the metrics a prior `eval.run` produced and
checks them against committed thresholds — the headline accuracy claim as a build-enforced
invariant, not a screenshot in a README (Appflow §4.2).

Two modes:
  - Full gate (default): reads config/eval_thresholds.json, the final thresholds that apply
    once the LLM triage layer exists on top of the deterministic cascade.
  - Deterministic-only (--mode deterministic_only, or explicit --auto-match-rate-min /
    --false-match-rate-max): the looser GATE 1 checkpoint from Implementation Plan §6.2,
    Day 7 — appropriate right now, since the LLM layer isn't built yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_THRESHOLDS_PATH = Path("config/eval_thresholds.json")

# GATE 1 (Implementation Plan §6.2): "Deterministic-only auto-match rate >= 75% on the
# 5,000-record batch, false-match rate <= 0.5%, and the baseline comparison prints."
GATE_1_THRESHOLDS = {
    "auto_match_rate_min": 0.75,
    "false_match_rate_max": 0.005,
}


def load_thresholds(mode: str, thresholds_path: Path) -> dict:
    if mode == "deterministic_only":
        return dict(GATE_1_THRESHOLDS)
    with thresholds_path.open() as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def check_gate(metrics: dict, thresholds: dict) -> list[str]:
    """Returns a list of failure messages; empty means the gate passed."""
    failures: list[str] = []
    m = metrics["milaan"]

    if "auto_match_rate_min" in thresholds:
        actual = m["auto_match_rate"]
        threshold = thresholds["auto_match_rate_min"]
        if actual < threshold:
            failures.append(f"auto_match_rate {actual:.2%} < required {threshold:.2%}")

    if "false_match_rate_max" in thresholds:
        actual = m["false_match_rate"]
        threshold = thresholds["false_match_rate_max"]
        if actual > threshold:
            failures.append(f"false_match_rate {actual:.4%} > allowed {threshold:.4%}")

    if "unexplained_value_pct_max" in thresholds:
        actual = metrics["unexplained_value_pct"]
        threshold = thresholds["unexplained_value_pct_max"]
        if actual > threshold:
            failures.append(f"unexplained_value_pct {actual:.2%} > allowed {threshold:.2%}")

    if "p95_runtime_seconds_max" in thresholds:
        actual = metrics["throughput"]["elapsed_seconds"]
        threshold = thresholds["p95_runtime_seconds_max"]
        if actual > threshold:
            failures.append(f"runtime {actual:.1f}s > allowed {threshold:.1f}s")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Milaan eval regression gate")
    parser.add_argument("metrics_path", type=Path)
    parser.add_argument(
        "--mode", choices=["full", "deterministic_only"], default="full",
        help="'full' reads config/eval_thresholds.json; 'deterministic_only' applies GATE 1",
    )
    parser.add_argument("--thresholds-path", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    parser.add_argument("--auto-match-rate-min", type=float, default=None)
    parser.add_argument("--false-match-rate-max", type=float, default=None)
    args = parser.parse_args()

    with args.metrics_path.open() as f:
        metrics = json.load(f)

    thresholds = load_thresholds(args.mode, args.thresholds_path)
    if args.auto_match_rate_min is not None:
        thresholds["auto_match_rate_min"] = args.auto_match_rate_min
    if args.false_match_rate_max is not None:
        thresholds["false_match_rate_max"] = args.false_match_rate_max

    failures = check_gate(metrics, thresholds)

    print(f"Gate mode: {args.mode}")
    print(f"Thresholds: {thresholds}")
    print(
        f"Actual: auto_match_rate={metrics['milaan']['auto_match_rate']:.2%}, "
        f"false_match_rate={metrics['milaan']['false_match_rate']:.4%}, "
        f"unexplained_value_pct={metrics['unexplained_value_pct']:.2%}"
    )

    if failures:
        print("\nGATE FAILED:")
        for f_ in failures:
            print(f"  - {f_}")
        sys.exit(1)

    print("\nGATE PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    main()
