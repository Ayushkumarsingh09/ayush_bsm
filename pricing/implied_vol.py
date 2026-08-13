"""Implied volatility inversion (kept strictly separate from pricing).

Given a market option price, solve for the BSM volatility that reproduces
it. Uses SciPy's Brent root-finder on a bracketing interval, which is
robust for the monotone vega > 0 problem, with clear error reporting when
the price violates no-arbitrage bounds.

This module is not yet wired into the dashboard UI (no market option
prices are ingested in the current version); it exists so the future
market-vs-model comparison can be added without touching the pricer.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

from config.settings import EPS_TIME
from pricing.bsm import bsm_price

SIGMA_LO = 1e-9
SIGMA_HI = 5.0  # 500% vol upper bracket


class ImpliedVolError(ValueError):
    """Raised when an implied volatility cannot be computed."""


def _no_arb_bounds(S: float, K: float, T: float, r: float, q: float,
                   is_call: bool) -> tuple[float, float]:
    disc_q = np.exp(-q * T)
    disc_r = np.exp(-r * T)
    if is_call:
        lower = max(S * disc_q - K * disc_r, 0.0)
        upper = S * disc_q
    else:
        lower = max(K * disc_r - S * disc_q, 0.0)
        upper = K * disc_r
    return lower, upper


def implied_volatility(price: float, S: float, K: float, T: float,
                       r: float, q: float, is_call: bool,
                       tol: float = 1e-10) -> float:
    """Solve BSM sigma such that model price equals ``price``.

    Raises:
        ImpliedVolError: if the option is expired, the price violates
            no-arbitrage bounds, or no volatility in [~0, 500%] matches.
    """
    if T <= EPS_TIME:
        raise ImpliedVolError("Option is expired; implied volatility is undefined.")
    lower, upper = _no_arb_bounds(S, K, T, r, q, is_call)
    if not (lower - 1e-12 <= price <= upper + 1e-12):
        raise ImpliedVolError(
            f"Price {price:.6f} violates no-arbitrage bounds "
            f"[{lower:.6f}, {upper:.6f}] for this option."
        )

    def objective(sigma: float) -> float:
        return bsm_price(S, K, T, r, q, sigma, is_call) - price

    f_lo = objective(SIGMA_LO)
    f_hi = objective(SIGMA_HI)
    if f_lo > 0.0 and abs(f_lo) < 1e-12:
        return 0.0
    if f_lo * f_hi > 0.0:
        raise ImpliedVolError(
            "No volatility in (0%, 500%] reproduces this price; the price is "
            "too close to an arbitrage bound or outside the model's range."
        )
    return float(brentq(objective, SIGMA_LO, SIGMA_HI, xtol=tol, rtol=8.9e-16))
