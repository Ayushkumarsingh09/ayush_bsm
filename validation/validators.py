"""Human-readable input validation.

Validation results separate hard *errors* (calculation refused) from soft
*warnings* (calculation proceeds, user is informed). Every message is
actionable plain English, never a bare exception name.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import settings


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _is_bad_number(x) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return True
    return math.isnan(v) or math.isinf(v)


def validate_market_inputs(spot: float, sigma: float, r: float, q: float,
                           T: float) -> ValidationResult:
    """Validate core pricing inputs. All rates/vols are decimals (0.185)."""
    res = ValidationResult()

    if _is_bad_number(spot):
        res.errors.append("Spot price must be a valid number.")
    elif spot <= 0:
        res.errors.append("Spot price must be greater than 0.")

    if _is_bad_number(sigma):
        res.errors.append("Volatility must be a valid number.")
    elif sigma < 0:
        res.errors.append(
            "Volatility must be greater than or equal to 0%. "
            "Please enter a valid volatility."
        )
    elif sigma > settings.MAX_SIGMA:
        res.errors.append(
            f"Volatility {sigma * 100:.1f}% exceeds the supported maximum of "
            f"{settings.MAX_SIGMA * 100:.0f}%. Check whether the value was "
            "entered as a decimal instead of a percentage."
        )
    elif sigma == 0:
        res.warnings.append(
            "Volatility is 0%: option values equal discounted intrinsic value "
            "and Greeks are undefined (shown as N/A)."
        )
    elif sigma > settings.SIGMA_SOFT_MAX:
        res.warnings.append(
            f"Volatility {sigma * 100:.1f}% is unusually high for SPX. "
            "Double-check the input."
        )

    if _is_bad_number(r):
        res.errors.append("Risk-free rate must be a valid number.")
    elif not (settings.RATE_HARD_MIN <= r <= settings.RATE_HARD_MAX):
        res.errors.append(
            f"Risk-free rate {r * 100:.2f}% is outside the supported range "
            f"({settings.RATE_HARD_MIN * 100:.0f}% to "
            f"{settings.RATE_HARD_MAX * 100:.0f}%)."
        )
    elif not (settings.RATE_SOFT_MIN <= r <= settings.RATE_SOFT_MAX):
        res.warnings.append(
            f"Risk-free rate {r * 100:.2f}% is unusual. Double-check the input."
        )

    if _is_bad_number(q):
        res.errors.append("Dividend yield must be a valid number.")
    elif not (settings.DIV_HARD_MIN <= q <= settings.DIV_HARD_MAX):
        res.errors.append(
            f"Dividend yield {q * 100:.2f}% is outside the supported range "
            f"({settings.DIV_HARD_MIN * 100:.0f}% to "
            f"{settings.DIV_HARD_MAX * 100:.0f}%)."
        )
    elif q > settings.DIV_SOFT_MAX:
        res.warnings.append(
            f"Dividend yield {q * 100:.2f}% is unusually high for SPX. "
            "Double-check the input."
        )

    if _is_bad_number(T):
        res.errors.append("Time to expiry could not be computed. "
                          "Check the expiry date and time.")
    elif T <= 0:
        res.warnings.append(
            "The selected expiry is in the past or at the current instant. "
            "Options are priced at intrinsic value and Greeks are shown as N/A."
        )

    return res


def validate_chain_config(strike_interval: float,
                          strikes_each_side: int,
                          atm_strike: float | None = None) -> ValidationResult:
    """Validate strike-grid settings.

    When ``atm_strike`` is provided, also verifies that the lowest grid
    strike is strictly positive (required by ``ln(S/K)`` in BSM).
    """
    res = ValidationResult()
    if _is_bad_number(strike_interval) or strike_interval <= 0:
        res.errors.append("Strike interval must be a positive number.")
    if strikes_each_side < 0:
        res.errors.append("Strikes each side must be 0 or more.")
    elif strikes_each_side > 500:
        res.errors.append("Strikes each side is capped at 500 for performance.")

    if (atm_strike is not None
            and not _is_bad_number(atm_strike)
            and not _is_bad_number(strike_interval)
            and strike_interval > 0
            and strikes_each_side >= 0):
        if atm_strike <= 0:
            res.errors.append("ATM strike must be greater than 0.")
        else:
            lowest = atm_strike - strikes_each_side * strike_interval
            if lowest <= 0:
                res.errors.append(
                    f"Strike grid would include non-positive strikes "
                    f"(lowest K = {lowest:g}). Increase ATM, reduce "
                    f"strikes-each-side, or reduce the strike interval."
                )
    return res
