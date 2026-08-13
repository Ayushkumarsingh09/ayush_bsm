"""Option-chain construction: strike grid, ATM detection, full analytics.

Produces a tidy DataFrame with BSM theoretical premiums and every registered
Greek in *raw mathematical units*; display scaling is applied later by the
presentation layer via ``pricing.conventions``.

Column naming
-------------
Theoretical premiums are stored as ``call_bsm_premium`` / ``put_bsm_premium``
so they are never confused with market quotes (bid/ask/mid/last).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from config.settings import (DEFAULT_STRIKE_INTERVAL,
                             DEFAULT_STRIKES_EACH_SIDE, EPS_TIME)
from pricing import bsm, greeks, higher_order_greeks as hog
from pricing.volatility import VolatilityProvider
from utils.dates import days_to_expiry


@dataclass(frozen=True)
class ChainConfig:
    """Strike-grid configuration (defaults: 25-point interval, 28+1+28)."""

    strike_interval: float = DEFAULT_STRIKE_INTERVAL
    strikes_each_side: int = DEFAULT_STRIKES_EACH_SIDE
    atm_method: str = "nearest"       # "nearest" or "explicit"
    explicit_atm: float | None = None


@dataclass(frozen=True)
class ChainMeta:
    """Summary values displayed in the dashboard header/KPI cards."""

    spot: float
    atm_strike: float
    spot_minus_atm: float
    T: float
    dte_days: float
    expiry: datetime
    n_strikes: int
    is_expired: bool
    model_label: str = "BSM (equity index)"
    q_effective: float | None = None


class StrikeGridError(ValueError):
    """Raised when the configured strike grid leaves the BSM domain (K > 0)."""


def detect_atm_strike(spot: float, cfg: ChainConfig) -> float:
    """ATM strike = grid strike minimizing |K - spot| (or explicit value).

    On an exact midpoint between two grid strikes, the **lower** strike is
    chosen. This is deterministic and avoids Python banker's ``round()``.
    """
    if cfg.atm_method == "explicit":
        if cfg.explicit_atm is None:
            raise ValueError("ATM method is 'explicit' but no ATM strike given.")
        return float(cfg.explicit_atm)
    interval = float(cfg.strike_interval)
    n = spot / interval
    lower = math.floor(n) * interval
    upper = math.ceil(n) * interval
    if abs(spot - lower) <= abs(spot - upper):
        return float(lower)
    return float(upper)


def generate_strikes(atm: float, cfg: ChainConfig) -> np.ndarray:
    """K_i = ATM + i * interval, i in [-n, ..., 0, ..., +n].

    All strikes must be strictly positive (required by ``ln(S/K)``).
    """
    i = np.arange(-cfg.strikes_each_side, cfg.strikes_each_side + 1)
    strikes = atm + i * cfg.strike_interval
    if np.any(strikes <= 0.0):
        lowest = float(strikes[0])
        raise StrikeGridError(
            f"Strike grid includes non-positive strikes (lowest K = {lowest:g}). "
            "Increase the ATM strike, reduce strikes-each-side, or reduce the "
            "strike interval so every K > 0."
        )
    return strikes


def _moneyness_status(spot: float, strikes: np.ndarray, atm: float,
                      is_call: bool) -> list[str]:
    """ITM/ATM/OTM classification. Call ITM iff S > K; put ITM iff S < K.

    The ATM grid strike is labeled ATM for both sides.
    """
    out = []
    for k in strikes:
        if k == atm:
            out.append("ATM")
        elif (spot > k) if is_call else (spot < k):
            out.append("ITM")
        else:
            out.append("OTM")
    return out


def build_option_chain(spot: float, T: float, r: float, q: float,
                       vol_provider: VolatilityProvider,
                       cfg: ChainConfig, expiry: datetime,
                       strikes: np.ndarray | None = None,
                       asof: datetime | None = None,
                       model_label: str = "BSM (equity index)",
                       ) -> tuple[pd.DataFrame, ChainMeta]:
    """Compute the full chain. Returns (raw-unit DataFrame, meta summary).

    The DataFrame includes for every strike: moneyness (S/K), call/put
    status, theoretical premiums, parity error, and all registered Greeks.

    If ``strikes`` is provided (e.g. from a BSM chain workbook), those exact
    strikes are priced with ``vol_provider`` (typically per-strike IVs) and
    the synthetic grid from ``cfg`` is not used. ATM is the listed strike
    closest to spot (lower strike on a tie).
    """
    if strikes is not None:
        strikes = np.asarray(strikes, dtype=np.float64)
        if strikes.size == 0:
            raise StrikeGridError("Strike list is empty.")
        if np.any(strikes <= 0.0):
            raise StrikeGridError(
                f"Strike list includes non-positive strikes "
                f"(lowest K = {float(np.min(strikes)):g})."
            )
        # Keep file order if already sorted; otherwise sort ascending.
        order = np.argsort(strikes, kind="mergesort")
        strikes = strikes[order]
        atm = float(strikes[np.argmin(np.abs(strikes - spot))])
        sigma = np.asarray(vol_provider.sigma(strikes, T), dtype=np.float64)
    else:
        atm = detect_atm_strike(spot, cfg)
        strikes = generate_strikes(atm, cfg)
        sigma = vol_provider.sigma(strikes, T)

    core = bsm.compute_core(spot, strikes, T, r, q, sigma)
    call = bsm.call_price(core)
    put = bsm.put_price(core)

    df = pd.DataFrame({
        "strike": strikes,
        "sigma": sigma,
        "moneyness": spot / strikes,
        "call_status": _moneyness_status(spot, strikes, atm, is_call=True),
        "put_status": _moneyness_status(spot, strikes, atm, is_call=False),
        # Theoretical BSM premiums — not market quotes.
        "call_bsm_premium": call,
        "put_bsm_premium": put,
        "parity_error": bsm.parity_error(core, call, put),
        # First order
        "call_delta": greeks.call_delta(core),
        "put_delta": greeks.put_delta(core),
        "vega": greeks.vega(core),
        "call_theta": greeks.call_theta(core),
        "put_theta": greeks.put_theta(core),
        "call_rho": greeks.call_rho(core),
        "put_rho": greeks.put_rho(core),
        # Second order
        "gamma": greeks.gamma(core),
        "vanna": hog.vanna(core),
        "volga": hog.volga(core),
        # Third order and higher
        "call_charm": hog.call_charm(core),
        "put_charm": hog.put_charm(core),
        "speed": hog.speed(core),
        "zomma": hog.zomma(core),
        "color": hog.color(core),
        "ultima": hog.ultima(core),
        "is_atm": strikes == atm,
    })

    meta = ChainMeta(
        spot=spot,
        atm_strike=atm,
        spot_minus_atm=spot - atm,
        T=T,
        # Calendar DTE from the actual expiry timestamp -- independent of the
        # day-count convention used for T. Use file asof when provided.
        dte_days=max(days_to_expiry(expiry, now=asof), 0.0),
        expiry=expiry,
        n_strikes=len(strikes),
        is_expired=T <= EPS_TIME,
        model_label=model_label,
        q_effective=float(q),
    )
    return df, meta
