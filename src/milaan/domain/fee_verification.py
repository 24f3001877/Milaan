"""T4 — fee verification against a versioned rate card (Implementation Plan §6.2, task 2.4;
PDR F3).

Recomputes the expected fee and tax for every payment-type settlement line from the
applicable rate-card band and compares it to what the gateway actually reported. This is
deliberately independent of match status (T1-T3): a line's own fee correctness is a
property of the line and the rate card in force, not of whether it happens to tie to a
bank credit yet — matching the orchestrator's `... -> MATCH_T3 -> VERIFY_FEES -> ...`
ordering (TRD §2.2), where fee verification runs on the settlement lines as a batch, not on
match groups.

Only `line_type == 'payment'` lines are evaluated — refunds, chargebacks, and adjustments
carry no MDR/tax in this model (Schema §5.4), so verifying them would be meaningless.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from milaan.domain.entities import RateCardBand, SettlementLineEntity
from milaan.domain.money import Money

DEFAULT_TOLERANCE = Money("0.02")  # absolute paise-level rounding slack on combined fee+tax


@dataclass(frozen=True, slots=True)
class FeeVarianceRecord:
    settlement_line_id: uuid.UUID
    expected_fee: Money
    expected_tax: Money
    reported_fee: Money
    reported_tax: Money
    delta: Money  # (reported_fee + reported_tax) - (expected_fee + expected_tax)
    rate_card_version: str
    within_tolerance: bool
    instrument_resolved: str | None


def find_applicable_band(
    bands: list[RateCardBand], instrument: str | None, gross: Money, on_date: date
) -> RateCardBand | None:
    if instrument is None:
        return None
    candidates = [
        b
        for b in bands
        if b.instrument == instrument
        and b.min_amount.amount <= gross.amount <= b.max_amount.amount
        and b.effective_from <= on_date
        and (b.effective_to is None or on_date <= b.effective_to)
    ]
    if not candidates:
        return None
    # Deterministic tie-break for the (normally-shouldn't-happen) case of overlapping
    # bands: prefer the most recently effective one, then the narrowest amount range.
    candidates.sort(
        key=lambda b: (b.effective_from, b.min_amount.amount - b.max_amount.amount), reverse=True
    )
    return candidates[0]


def compute_expected_fee_tax(band: RateCardBand, gross: Money) -> tuple[Money, Money]:
    fee = Money(gross.amount * Decimal(band.percent_bps) / Decimal(10000) + band.flat_fee.amount)
    tax = Money(fee.amount * Decimal(band.tax_percent_bps) / Decimal(10000))
    return fee, tax


def verify_fees(
    lines: list[SettlementLineEntity],
    bands: list[RateCardBand],
    rate_card_version: str,
    tolerance: Money = DEFAULT_TOLERANCE,
) -> list[FeeVarianceRecord]:
    """Batch entry point — verifies every payment-type line using its own gross/fee/tax/
    instrument fields directly."""
    records: list[FeeVarianceRecord] = []
    for line in sorted(
        lines, key=lambda settlement_line: settlement_line.settlement_id
    ):  # deterministic order (C2)
        if line.line_type != "payment":
            continue

        band = find_applicable_band(bands, line.instrument, line.gross, line.settled_on)
        if band is None:
            records.append(
                FeeVarianceRecord(
                    settlement_line_id=line.id,
                    expected_fee=Money.zero(),
                    expected_tax=Money.zero(),
                    reported_fee=line.fee,
                    reported_tax=line.tax,
                    delta=Money.zero(),
                    rate_card_version=rate_card_version,
                    within_tolerance=False,
                    instrument_resolved=None,
                )
            )
            continue

        expected_fee, expected_tax = compute_expected_fee_tax(band, line.gross)
        delta = Money(
            (line.fee.amount + line.tax.amount) - (expected_fee.amount + expected_tax.amount)
        )
        within_tolerance = abs(delta.amount) <= tolerance.amount
        records.append(
            FeeVarianceRecord(
                settlement_line_id=line.id,
                expected_fee=expected_fee,
                expected_tax=expected_tax,
                reported_fee=line.fee,
                reported_tax=line.tax,
                delta=delta,
                rate_card_version=rate_card_version,
                within_tolerance=within_tolerance,
                instrument_resolved=band.instrument,
            )
        )
    return records
