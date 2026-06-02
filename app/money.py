"""Money is stored as integer cents so sums stay exact; dollars live in Decimal.

Conversions happen only at the DB boundary.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_cents(amount: Decimal | None) -> int | None:
    if amount is None:
        return None
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_cents(cents: int | None) -> Decimal | None:
    if cents is None:
        return None
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def dollars(cents: int | None) -> str:
    d = from_cents(cents)
    return f"${d}" if d is not None else "—"
