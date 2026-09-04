"""Unit tests for T4 fee verification (Implementation Plan §6.2, task 2.4; PDR F3)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from milaan.domain.entities import RateCardBand, SettlementLineEntity
from milaan.domain.fee_verification import (
    compute_expected_fee_tax,
    find_applicable_band,
    verify_fees,
)
from milaan.domain.money import Money

D = date(2026, 1, 10)

# Mirrors the synthetic generator's own formula exactly (2% MDR, 18% tax on the fee) so
# these tests validate real-world-shaped numbers, not made-up ones.
STANDARD_BAND = RateCardBand(
    version="v1", instrument="upi", min_amount=Money("0.00"), max_amount=Money("9999999.00"),
    percent_bps=200, flat_fee=Money("0.00"), tax_percent_bps=1800,
    effective_from=date(2026, 1, 1), effective_to=None,
)


def make_line(
    gross: str, fee: str, tax: str, net: str, instrument: str | None = "upi",
    line_type: str = "payment", settled_on: date = D,
) -> SettlementLineEntity:
    return SettlementLineEntity(
        id=uuid.uuid4(), settlement_id="S1", payment_id="p1", order_ref="O1",
        line_type=line_type, gross=Money(gross), net=Money(net), utr="UTR1",
        settled_on=settled_on, fee=Money(fee), tax=Money(tax), instrument=instrument,
    )


def test_compute_expected_fee_tax_matches_generator_formula() -> None:
    fee, tax = compute_expected_fee_tax(STANDARD_BAND, Money("1000.00"))
    assert fee == Money("20.00")   # 2% of 1000
    assert tax == Money("3.60")    # 18% of 20.00


def test_find_applicable_band_respects_instrument_amount_and_date() -> None:
    assert find_applicable_band([STANDARD_BAND], "upi", Money("500.00"), D) is STANDARD_BAND
    assert find_applicable_band([STANDARD_BAND], "card_debit", Money("500.00"), D) is None
    assert find_applicable_band([STANDARD_BAND], "upi", Money("500.00"), date(2025, 12, 1)) is None


def test_verify_fees_flags_clean_line_as_within_tolerance() -> None:
    # gross=1000, expected fee=20.00, expected tax=3.60 (matches exactly)
    line = make_line(gross="1000.00", fee="20.00", tax="3.60", net="976.40")
    records = verify_fees([line], [STANDARD_BAND], "v1")
    assert len(records) == 1
    r = records[0]
    assert r.within_tolerance is True
    assert r.expected_fee == Money("20.00")
    assert r.delta == Money("0.00")


def test_verify_fees_flags_overcharge_beyond_tolerance() -> None:
    # Reported fee is skewed +5.00 above the expected 20.00 — same shape as the generator's
    # fee_variance pathology (a skew well beyond any rounding tolerance).
    line = make_line(gross="1000.00", fee="25.00", tax="3.60", net="971.40")
    records = verify_fees([line], [STANDARD_BAND], "v1")
    r = records[0]
    assert r.within_tolerance is False
    assert r.delta == Money("5.00")


def test_verify_fees_allows_paise_level_rounding_within_tolerance() -> None:
    # 1 paise off — within the default 0.02 tolerance.
    line = make_line(gross="1000.00", fee="20.01", tax="3.60", net="976.39")
    records = verify_fees([line], [STANDARD_BAND], "v1")
    assert records[0].within_tolerance is True


def test_verify_fees_skips_non_payment_lines() -> None:
    refund = make_line(gross="-50.00", fee="0.00", tax="0.00", net="-50.00", line_type="refund")
    records = verify_fees([refund], [STANDARD_BAND], "v1")
    assert records == []


def test_verify_fees_flags_unverifiable_line_with_no_matching_band() -> None:
    line = make_line(gross="1000.00", fee="20.00", tax="3.60", net="976.40", instrument="emi")
    records = verify_fees([line], [STANDARD_BAND], "v1")  # STANDARD_BAND is 'upi' only
    r = records[0]
    assert r.within_tolerance is False
    assert r.instrument_resolved is None


def test_verify_fees_flags_missing_instrument_as_unverifiable() -> None:
    line = make_line(gross="1000.00", fee="20.00", tax="3.60", net="976.40", instrument=None)
    records = verify_fees([line], [STANDARD_BAND], "v1")
    assert records[0].instrument_resolved is None
    assert records[0].within_tolerance is False


def test_verify_fees_all_amounts_are_money_not_float() -> None:
    line = make_line(gross="1000.00", fee="20.00", tax="3.60", net="976.40")
    r = verify_fees([line], [STANDARD_BAND], "v1")[0]
    for field_value in (r.expected_fee, r.expected_tax, r.reported_fee, r.reported_tax, r.delta):
        assert isinstance(field_value, Money)


def test_flat_fee_component_applies() -> None:
    band = RateCardBand(
        version="v1", instrument="netbanking", min_amount=Money("0.00"), max_amount=Money("999999.00"),
        percent_bps=100, flat_fee=Money("5.00"), tax_percent_bps=1800,
        effective_from=date(2026, 1, 1), effective_to=None,
    )
    fee, tax = compute_expected_fee_tax(band, Money("1000.00"))
    assert fee == Money("15.00")  # 1% of 1000 + flat 5.00
    assert tax == Money("2.70")   # 18% of 15.00
