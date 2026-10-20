"""Money as integer cents.

Floats can't represent most decimal amounts exactly (0.1 + 0.2 != 0.3), and those
errors pile up over a ledger. Every amount in SplitLedger is an int number of
cents, and text is converted to cents only through the strict parser below.
"""

from __future__ import annotations

import re

# One billion dollars. Far above any household expense, and comfortably inside
# SQLite's 64-bit INTEGER range, so a typo like 10000000000000 is caught early.
MAX_CENTS = 100_000_000_000

_AMOUNT = re.compile(r"(?P<whole>[0-9]+)(?:\.(?P<frac>[0-9]{1,2}))?")


class MoneyError(ValueError):
    """Text could not be read as an amount of money."""


def parse_amount(text: str) -> int:
    """Convert text such as "12.34" to cents (1234).

    Accepted: digits with an optional decimal point and one or two decimal
    places ("12", "12.5", "12.34"). Rejected rather than guessed: signs,
    currency symbols, thousands separators, exponents, surrounding spaces, and
    more than two decimal places ("12.345" is ambiguous, so the user decides).
    """
    if not isinstance(text, str):
        raise MoneyError(f"amount must be text, got {type(text).__name__}")
    match = _AMOUNT.fullmatch(text)
    if match is None:
        raise MoneyError(
            f"{text!r} is not an amount; use digits with up to two decimal places, like 12.34"
        )
    frac = (match["frac"] or "").ljust(2, "0")  # "5" means 50 cents, not 5
    cents = int(match["whole"]) * 100 + int(frac or "0")
    check_cents(cents)
    return cents


def check_cents(cents: int, *, allow_negative: bool = False) -> int:
    """Validate an amount already in cents and return it."""
    # bool is a subclass of int in Python, so True would otherwise count as 1 cent.
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise MoneyError(f"amount in cents must be an int, got {type(cents).__name__}")
    if cents < 0 and not allow_negative:
        raise MoneyError(f"amount must not be negative, got {cents} cents")
    if abs(cents) > MAX_CENTS:
        raise MoneyError(f"amount {format_cents(cents)} is above the limit of {format_cents(MAX_CENTS)}")
    return cents


def format_cents(cents: int) -> str:
    """Format cents as text: 1234 -> "12.34", -5 -> "-0.05"."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"
