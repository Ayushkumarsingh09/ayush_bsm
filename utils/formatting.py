"""Number formatting helpers. Rounding happens only at display time."""

from __future__ import annotations

import math


def fmt_number(value: float | None, decimals: int = 4) -> str:
    """Format a float for display; NaN/None render as 'N/A'."""
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(v):
        return "N/A"
    if math.isinf(v):
        return "\u221e" if v > 0 else "-\u221e"
    return f"{v:,.{decimals}f}"


def fmt_pct(value: float | None, decimals: int = 2) -> str:
    """Format a decimal fraction as a percentage string (0.185 -> '18.50%')."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{float(value) * 100.0:.{decimals}f}%"


def parse_pct_input(value: float) -> float:
    """Convert a user-facing percentage number to a decimal (18.5 -> 0.185)."""
    return float(value) / 100.0
