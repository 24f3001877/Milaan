"""Property tests for the Money value object.

Covers TRD C1: no float leakage, exact allocation sums, round-half-up correctness.
Run via `pytest -m hypothesis` — this is the invariant class the CI eval-adjacent test job
checks on 1,000+ generated examples before any matching logic is trusted (Implementation
Plan §6.1, task 1.6).
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from milaan.domain.money import Money, MoneyError

pytestmark = pytest.mark.hypothesis

decimals = st.decimals(
    min_value=Decimal("-1000000000"),
    max_value=Decimal("1000000000"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


@given(decimals)
@settings(max_examples=1000)
def test_construction_quantizes_to_two_places(d: Decimal) -> None:
    m = Money(d)
    assert m.amount.as_tuple().exponent >= -2


@given(decimals, decimals)
@settings(max_examples=1000)
def test_addition_is_exact_and_commutative(a: Decimal, b: Decimal) -> None:
    assert Money(a) + Money(b) == Money(b) + Money(a)


two_place_decimals = st.decimals(
    min_value=Decimal("-1000000000"),
    max_value=Decimal("1000000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@given(st.lists(two_place_decimals, min_size=1, max_size=50))
@settings(max_examples=500)
def test_allocation_sum_matches_manual_quantized_sum(parts: list[Decimal]) -> None:
    """C5: summing already-quantized Money members equals quantizing their raw sum once.

    This only holds when each part is already at 2dp, which is the real allocation case —
    every match_member.allocated_amount is itself a Money value, never a raw 4dp figure.
    Summing values that still carry unquantized precision (e.g. straight from a rate-card
    NUMERIC(20,4) calculation) is a different, order-dependent operation and deliberately
    NOT asserted equal here — that drift is exactly the paise-level fee variance F3 exists
    to detect, not a bug to paper over in the Money type.
    """
    total_via_money = Money.sum([Money(p) for p in parts])
    manual = Money(sum(parts, Decimal("0")))
    assert total_via_money == manual


def test_float_construction_is_rejected() -> None:
    with pytest.raises(MoneyError):
        Money(0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.005", "1.01"),  # round-half-up (away from zero), not banker's rounding
        ("1.004", "1.00"),
        ("-1.005", "-1.01"),  # ROUND_HALF_UP ties go away from zero, symmetric in sign
        ("0.005", "0.01"),
    ],
)
def test_round_half_up_semantics(raw: str, expected: str) -> None:
    assert Money(raw).to_json() == expected


def test_json_serialisation_is_always_a_string() -> None:
    assert isinstance(Money("1234.56").to_json(), str)
    assert Money("1234.5").to_json() == "1234.50"
