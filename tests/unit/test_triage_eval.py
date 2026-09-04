"""Tests for the golden-set triage accuracy harness (Implementation Plan §6.2, task 2.15)."""

from __future__ import annotations

from pathlib import Path

from milaan.eval.golden_set import GOLDEN_SET
from milaan.eval.triage_eval import populate_placeholder_cache, run_triage_eval


def test_golden_set_has_forty_items_covering_all_twelve_categories() -> None:
    assert len(GOLDEN_SET) == 40
    categories = {item.category for item in GOLDEN_SET}
    assert len(categories) == 12


def test_run_triage_eval_produces_a_real_accuracy_signal(tmp_path: Path) -> None:
    populate_placeholder_cache(tmp_path)
    result = run_triage_eval(tmp_path)
    assert result.total == 40
    # Not trivially 0% or 100% — the placeholder responder is deliberately independent
    # of the golden set's hand labels, so this is a genuine (if limited) measurement.
    assert 0 < result.accuracy < 1
    assert len(result.mismatches) == result.total - result.correct


def test_per_category_accuracy_covers_every_category(tmp_path: Path) -> None:
    populate_placeholder_cache(tmp_path)
    result = run_triage_eval(tmp_path)
    assert set(result.per_category_accuracy.keys()) == {item.category for item in GOLDEN_SET}
