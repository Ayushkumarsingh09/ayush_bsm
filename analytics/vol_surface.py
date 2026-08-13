"""Volatility surface construction and smile / skew analytics.

Builds sigma(K, T) from live or file market quotes. Never invents quotes —
missing IVs stay missing; interpolation only fills between observed points.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pricing.implied_vol import ImpliedVolError, implied_volatility
from pricing.volatility import MarketVolatilitySurface, PerStrikeVolatility
from utils.dates import DayCount, time_to_expiry


@dataclass(frozen=True)
class SkewMetrics:
    atm_iv: float | None
    put_25d_iv: float | None
    call_25d_iv: float | None
    risk_reversal_25d: float | None  # call_iv - put_iv
    butterfly_25d: float | None      # 0.5*(call+put) - atm
    skew_slope: float | None         # dσ/d(K/S) near ATM
    n_points: int
    expiry: object | None = None
    T: float | None = None


def _safe_iv(price: float, spot: float, strike: float, T: float,
             r: float, q: float, is_call: bool) -> float | None:
    if price is None or not np.isfinite(price) or price <= 0:
        return None
    if T <= 0 or spot <= 0 or strike <= 0:
        return None
    try:
        return float(implied_volatility(
            price, spot, strike, T, r, q, is_call=is_call))
    except (ImpliedVolError, ValueError):
        return None


def mid_iv_row(row: pd.Series, spot: float, T: float, r: float, q: float
               ) -> tuple[float | None, float | None, float | None]:
    """Return (call_iv, put_iv, blended_iv) from mids; Yahoo IV as fallback."""
    def _mid(bid, ask, last):
        b = row.get(bid)
        a = row.get(ask)
        last_v = row.get(last)
        try:
            b_f = float(b) if b is not None and np.isfinite(float(b)) else None
            a_f = float(a) if a is not None and np.isfinite(float(a)) else None
        except (TypeError, ValueError):
            b_f = a_f = None
        if b_f is not None and a_f is not None and a_f >= b_f >= 0:
            return 0.5 * (b_f + a_f)
        try:
            lv = float(last_v) if last_v is not None else None
            if lv is not None and np.isfinite(lv) and lv > 0:
                return lv
        except (TypeError, ValueError):
            pass
        return None

    k = float(row["strike"])
    cm = _mid("call_bid", "call_ask", "call_last")
    pm = _mid("put_bid", "put_ask", "put_last")
    civ = _safe_iv(cm, spot, k, T, r, q, True) if cm is not None else None
    piv = _safe_iv(pm, spot, k, T, r, q, False) if pm is not None else None

    # Yahoo-reported IV fallback (still market-derived, not fabricated).
    if civ is None:
        y = row.get("call_yahoo_iv")
        try:
            yf = float(y) if y is not None else None
            if yf is not None and np.isfinite(yf) and 1e-4 < yf < 5.0:
                civ = yf
        except (TypeError, ValueError):
            pass
    if piv is None:
        y = row.get("put_yahoo_iv")
        try:
            yf = float(y) if y is not None else None
            if yf is not None and np.isfinite(yf) and 1e-4 < yf < 5.0:
                piv = yf
        except (TypeError, ValueError):
            pass

    blend = None
    if civ is not None and piv is not None:
        blend = 0.5 * (civ + piv)
    elif civ is not None:
        blend = civ
    elif piv is not None:
        blend = piv
    return civ, piv, blend


def enrich_chain_ivs(frame: pd.DataFrame, spot: float, T: float,
                     r: float, q: float) -> pd.DataFrame:
    """Add call_iv / put_iv / market_iv columns from live quote rows."""
    out = frame.copy()
    call_ivs, put_ivs, blend = [], [], []
    for _, row in out.iterrows():
        c, p, b = mid_iv_row(row, spot, T, r, q)
        call_ivs.append(c)
        put_ivs.append(p)
        blend.append(b)
    out["call_iv"] = call_ivs
    out["put_iv"] = put_ivs
    out["market_iv"] = blend
    return out


def smile_provider_from_chain(frame: pd.DataFrame, spot: float, T: float,
                              r: float, q: float) -> PerStrikeVolatility | None:
    """Build a per-strike smile from a single-expiry quote frame."""
    enriched = enrich_chain_ivs(frame, spot, T, r, q)
    ok = enriched.dropna(subset=["strike", "market_iv"])
    ok = ok[(ok["market_iv"] > 1e-4) & (ok["market_iv"] < 5.0)]
    if len(ok) < 3:
        return None
    return PerStrikeVolatility(
        ok["strike"].to_numpy(dtype=float),
        ok["market_iv"].to_numpy(dtype=float),
    )


def build_surface_table(
    slices: list[tuple[datetime, float, pd.DataFrame]],
    spot: float,
    r: float,
    q: float,
    asof: datetime | None = None,
    day_count: DayCount = DayCount.ACT_365,
) -> pd.DataFrame:
    """Flatten multi-expiry chains into long-form surface points.

    Each ``slices`` item is ``(expiry, spot_for_slice, quote_frame)``.
    """
    rows = []
    for expiry, slice_spot, frame in slices:
        T = time_to_expiry(expiry, now=asof, convention=day_count)
        if T <= 0:
            continue
        s = float(slice_spot) if slice_spot else float(spot)
        enriched = enrich_chain_ivs(frame, s, T, r, q)
        for _, row in enriched.iterrows():
            iv = row.get("market_iv")
            if iv is None or not np.isfinite(iv):
                continue
            k = float(row["strike"])
            rows.append({
                "expiry": expiry,
                "T": T,
                "strike": k,
                "moneyness": k / s,
                "log_moneyness": float(np.log(k / s)),
                "market_iv": float(iv),
                "call_iv": row.get("call_iv"),
                "put_iv": row.get("put_iv"),
                "spot": s,
            })
    return pd.DataFrame(rows)


def surface_provider_from_table(surface: pd.DataFrame) -> MarketVolatilitySurface:
    if surface is None or surface.empty:
        raise ValueError("Empty surface table")
    return MarketVolatilitySurface(
        strikes=surface["strike"].to_numpy(dtype=float),
        tenors=surface["T"].to_numpy(dtype=float),
        sigmas=surface["market_iv"].to_numpy(dtype=float),
    )


def _nearest_delta_iv(frame: pd.DataFrame, spot: float, T: float, r: float,
                      q: float, target_delta: float, side: str
                      ) -> float | None:
    """Pick IV at strike whose BSM delta is closest to target (signed)."""
    from pricing.bsm import compute_core
    from pricing.greeks import call_delta, put_delta

    enriched = enrich_chain_ivs(frame, spot, T, r, q)
    best = None
    best_err = 1e9
    for _, row in enriched.iterrows():
        iv = row.get("call_iv") if side == "call" else row.get("put_iv")
        if iv is None or not np.isfinite(iv) or iv <= 0:
            iv = row.get("market_iv")
        if iv is None or not np.isfinite(iv) or iv <= 0:
            continue
        k = float(row["strike"])
        try:
            core = compute_core(spot, k, T, r, q, float(iv))
            d = float(call_delta(core)[0] if side == "call"
                      else put_delta(core)[0])
        except Exception:
            continue
        if not np.isfinite(d):
            continue
        err = abs(d - target_delta)
        if err < best_err:
            best_err = err
            best = float(iv)
    return best


def compute_skew_metrics(frame: pd.DataFrame, spot: float, T: float,
                         r: float, q: float,
                         expiry=None) -> SkewMetrics:
    enriched = enrich_chain_ivs(frame, spot, T, r, q)
    ok = enriched.dropna(subset=["market_iv"])
    n = len(ok)
    if n == 0:
        return SkewMetrics(None, None, None, None, None, None, 0, expiry, T)

    # ATM = closest strike to spot
    idx = (ok["strike"] - spot).abs().idxmin()
    atm_iv = float(ok.loc[idx, "market_iv"])

    put_25 = _nearest_delta_iv(frame, spot, T, r, q, -0.25, "put")
    call_25 = _nearest_delta_iv(frame, spot, T, r, q, 0.25, "call")
    rr = None
    fly = None
    if put_25 is not None and call_25 is not None:
        rr = call_25 - put_25
        fly = 0.5 * (call_25 + put_25) - atm_iv

    # Local slope dσ / d(K/S) near ATM (±5% moneyness)
    band = ok[(ok["strike"] / spot >= 0.95) & (ok["strike"] / spot <= 1.05)]
    slope = None
    if len(band) >= 3:
        x = (band["strike"] / spot).to_numpy(dtype=float)
        y = band["market_iv"].to_numpy(dtype=float)
        coef = np.polyfit(x, y, 1)
        slope = float(coef[0])

    return SkewMetrics(
        atm_iv=atm_iv,
        put_25d_iv=put_25,
        call_25d_iv=call_25,
        risk_reversal_25d=rr,
        butterfly_25d=fly,
        skew_slope=slope,
        n_points=n,
        expiry=expiry,
        T=T,
    )
