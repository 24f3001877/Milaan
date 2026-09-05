"""The committed demo rate card used by the eval harness.

Matches the synthetic generator's own formula exactly (2% MDR, 18% tax on the fee, flat
across instruments) — see adapters/synthetic/generate.py's MDR_RATE/TAX_RATE. This is a
placeholder standing in for the real `rate_card` DB table (Schema §5.4), which task 2.17's
API surface will read from once it exists. Versioned the same way the DB table is: a
`version` string, never mutated in place.
"""

from __future__ import annotations

from datetime import date

from milaan.domain.entities import RateCardBand
from milaan.domain.money import Money

RATE_CARD_VERSION = "demo-v1"

_INSTRUMENTS = ["upi", "card_debit", "card_credit", "netbanking", "wallet", "emi"]


def default_rate_card(effective_from: date) -> list[RateCardBand]:
    return [
        RateCardBand(
            version=RATE_CARD_VERSION,
            instrument=inst,
            min_amount=Money("0.00"),
            max_amount=Money("99999999.00"),
            percent_bps=200,
            flat_fee=Money("0.00"),
            tax_percent_bps=1800,
            effective_from=effective_from,
            effective_to=None,
        )
        for inst in _INSTRUMENTS
    ]
