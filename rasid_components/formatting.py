"""Formatting for values that come off the API as strings.

Shared because the same figure shows up in two unrelated places — the account
card's balance and a GoBox solution's price per run — and both have to render
it the same way.
"""

from decimal import Decimal, InvalidOperation


def format_tokens(raw):
    """Format a GoToken figure for display, or ``None`` if it is not a number.

    Parsed as :class:`~decimal.Decimal`, never ``float``. The API sends these
    as STRINGS on purpose: a balance the user watches tick down must not pick
    up binary floating-point error on the way through JSON, and routing it
    through ``float`` here would reintroduce exactly what the string exists to
    prevent. ``"2404153.0000"`` is the real shape.

    Returns ``None`` rather than ``"0"`` for anything unparseable. Callers rely
    on that distinction: a missing figure is not a zero one, and showing a
    confident zero for an absent value is the bug this function was written to
    end (the account card told every user they had 0 tokens for exactly that
    reason).
    """
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # Whole numbers lose the ".00"; fractions keep two places.
    if value == value.to_integral_value():
        value = value.quantize(Decimal(1))
    else:
        value = value.quantize(Decimal("0.01"))
    return f"{value:,}"
