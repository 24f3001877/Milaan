"""Money value object.

TRD §2.3 C1 (highest severity): no `float` anywhere in the money path. All amounts are
`Decimal`, quantized to two decimal places with ROUND_HALF_UP, and serialise to JSON as a
string — never a JSON number, because JSON has no exact decimal type.

This module has zero I/O and zero framework imports, per the domain-layer contract enforced
by import-linter in CI (pyproject.toml [[tool.importlinter.contracts]]).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

TWO_PLACES = Decimal("0.01")


class MoneyError(ValueError):
    """Raised when a Money value is constructed from an untrusted or lossy source."""


class Money:
    """An exact, immutable monetary amount quantized to two decimal places.

    Deliberately refuses to be constructed from `float`: a float literal like 0.1 cannot
    represent paise exactly, and silently accepting one would reintroduce the exact bug
    class C1 exists to eliminate. Construct from `Decimal`, `int`, or `str` only.
    """

    __slots__ = ("_amount",)

    def __init__(self, amount: Decimal | int | str) -> None:
        if isinstance(amount, float):  # pragma: no cover - defensive, mypy already blocks this
            raise MoneyError("Money cannot be constructed from float; use Decimal, int, or str.")
        try:
            dec = Decimal(amount) if not isinstance(amount, Decimal) else amount
        except Exception as exc:  # noqa: BLE001 - re-raise as domain error
            raise MoneyError(f"Cannot parse {amount!r} as Money") from exc
        if not dec.is_finite():
            raise MoneyError(f"Money must be finite, got {dec}")
        self._amount = dec.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @property
    def amount(self) -> Decimal:
        return self._amount

    def __add__(self, other: Money) -> Money:
        return Money(self._amount + other._amount)

    def __sub__(self, other: Money) -> Money:
        return Money(self._amount - other._amount)

    def __neg__(self) -> Money:
        return Money(-self._amount)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self._amount == other._amount

    def __lt__(self, other: Money) -> bool:
        return self._amount < other._amount

    def __le__(self, other: Money) -> bool:
        return self._amount <= other._amount

    def __hash__(self) -> int:
        return hash(self._amount)

    def __repr__(self) -> str:
        return f"Money('{self._amount}')"

    def to_json(self) -> str:
        """Serialise as a JSON string, e.g. '1234.5600' -> '1234.56'. Never a bare number."""
        return str(self._amount)

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal("0"))

    @classmethod
    def sum(cls, values: list[Money]) -> Money:
        total = Decimal("0")
        for v in values:
            total += v._amount
        return cls(total)


def money_json_encoder(value: Any) -> str:
    """Pydantic/FastAPI json encoder hook: Money -> JSON string, never a number."""
    if isinstance(value, Money):
        return value.to_json()
    raise TypeError(f"Cannot encode {type(value)} as Money")
