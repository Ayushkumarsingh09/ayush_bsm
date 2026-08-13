"""Market option quotes and mispricing vs BSM theoretical premiums.

Never fabricates bids/asks. When only IVs are supplied, market mid is absent
and mispricing columns are NaN — the BSM premium from market IV is still the
market-implied European fair value under the chosen model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pricing.implied_vol import ImpliedVolError, implied_volatility


def mid_from_bid_ask(bid: float | None, ask: float | None,
                     last: float | None = None) -> float | None:
    """Best available market premium proxy: mid if both sides, else last."""
    b = None if bid is None or (isinstance(bid, float) and np.isnan(bid)) else float(bid)
    a = None if ask is None or (isinstance(ask, float) and np.isnan(ask)) else float(ask)
    if b is not None and a is not None and b >= 0 and a >= 0 and a >= b:
        return 0.5 * (b + a)
    if last is not None and not (isinstance(last, float) and np.isnan(last)):
        if float(last) > 0:
            return float(last)
    return None


@dataclass(frozen=True)
class MispricingSummary:
    n_with_market: int
    call_mae: float | None
    put_mae: float | None
    call_mape_pct: float | None
    put_mape_pct: float | None
    median_call_diff: float | None
    median_put_diff: float | None


def attach_market_columns(chain: pd.DataFrame,
                          market: pd.DataFrame | None) -> pd.DataFrame:
    """Left-join optional market quote columns onto the analytics chain.

    ``market`` expected columns (any subset): strike, call_bid, call_ask,
    call_last, call_mid, put_bid, put_ask, put_last, put_mid, market_iv.
    """
    out = chain.copy()
    empty = [
        "call_bid", "call_ask", "call_last", "call_market_mid",
        "put_bid", "put_ask", "put_last", "put_market_mid",
        "call_mispricing", "put_mispricing",
        "call_mispricing_pct", "put_mispricing_pct",
        "call_market_iv", "put_market_iv",
    ]
    for col in empty:
        if col not in out.columns:
            out[col] = np.nan

    if market is None or market.empty or "strike" not in market.columns:
        return out

    m = market.copy()
    m["strike"] = pd.to_numeric(m["strike"], errors="coerce")
    m = m.dropna(subset=["strike"])

    def _series(name_candidates: list[str]) -> pd.Series | None:
        for name in name_candidates:
            if name in m.columns:
                return pd.to_numeric(m[name], errors="coerce")
        return None

    call_bid = _series(["call_bid", "bid_call"])
    call_ask = _series(["call_ask", "ask_call"])
    call_last = _series(["call_last", "last_call", "call_price_mkt"])
    call_mid_col = _series(["call_mid", "call_market_mid"])
    put_bid = _series(["put_bid", "bid_put"])
    put_ask = _series(["put_ask", "ask_put"])
    put_last = _series(["put_last", "last_put", "put_price_mkt"])
    put_mid_col = _series(["put_mid", "put_market_mid"])

    rows = []
    for i, strike in enumerate(m["strike"].to_numpy()):
        cb = None if call_bid is None else call_bid.iloc[i]
        ca = None if call_ask is None else call_ask.iloc[i]
        cl = None if call_last is None else call_last.iloc[i]
        cm = None if call_mid_col is None else call_mid_col.iloc[i]
        if cm is None or (isinstance(cm, float) and np.isnan(cm)):
            cm = mid_from_bid_ask(cb, ca, cl)
        pb = None if put_bid is None else put_bid.iloc[i]
        pa = None if put_ask is None else put_ask.iloc[i]
        pl = None if put_last is None else put_last.iloc[i]
        pm = None if put_mid_col is None else put_mid_col.iloc[i]
        if pm is None or (isinstance(pm, float) and np.isnan(pm)):
            pm = mid_from_bid_ask(pb, pa, pl)
        rows.append({
            "strike": float(strike),
            "call_bid": cb, "call_ask": ca, "call_last": cl,
            "call_market_mid": cm,
            "put_bid": pb, "put_ask": pa, "put_last": pl,
            "put_market_mid": pm,
        })
    mq = pd.DataFrame(rows)
    out = out.drop(columns=[c for c in mq.columns if c != "strike" and c in out.columns],
                   errors="ignore")
    out = out.merge(mq, on="strike", how="left")

    out["call_mispricing"] = out["call_bsm_premium"] - out["call_market_mid"]
    out["put_mispricing"] = out["put_bsm_premium"] - out["put_market_mid"]
    with np.errstate(divide="ignore", invalid="ignore"):
        out["call_mispricing_pct"] = np.where(
            out["call_market_mid"].abs() > 1e-12,
            100.0 * out["call_mispricing"] / out["call_market_mid"],
            np.nan,
        )
        out["put_mispricing_pct"] = np.where(
            out["put_market_mid"].abs() > 1e-12,
            100.0 * out["put_mispricing"] / out["put_market_mid"],
            np.nan,
        )
    return out


def enrich_market_ivs(chain: pd.DataFrame, spot: float, T: float, r: float,
                      q: float) -> pd.DataFrame:
    """Solve market IV from market mids when present."""
    out = chain.copy()
    if "call_market_iv" not in out.columns:
        out["call_market_iv"] = np.nan
    if "put_market_iv" not in out.columns:
        out["put_market_iv"] = np.nan

    for i, row in out.iterrows():
        k = float(row["strike"])
        cm = row.get("call_market_mid")
        pm = row.get("put_market_mid")
        if pd.notna(cm) and float(cm) > 0 and T > 0:
            try:
                out.at[i, "call_market_iv"] = implied_volatility(
                    float(cm), spot, k, T, r, q, True)
            except ImpliedVolError:
                pass
        if pd.notna(pm) and float(pm) > 0 and T > 0:
            try:
                out.at[i, "put_market_iv"] = implied_volatility(
                    float(pm), spot, k, T, r, q, False)
            except ImpliedVolError:
                pass
    return out


def summarize_mispricing(chain: pd.DataFrame) -> MispricingSummary:
    def _stats(diff: pd.Series, mid: pd.Series) -> tuple[float | None, float | None, float | None]:
        mask = mid.notna() & diff.notna()
        if not mask.any():
            return None, None, None
        d = diff[mask]
        m = mid[mask].abs()
        mae = float(d.abs().mean())
        mape = float((d.abs() / m.replace(0, np.nan)).mean() * 100) if m.sum() else None
        med = float(d.median())
        return mae, mape, med

    c_mae, c_mape, c_med = _stats(chain.get("call_mispricing", pd.Series(dtype=float)),
                                  chain.get("call_market_mid", pd.Series(dtype=float)))
    p_mae, p_mape, p_med = _stats(chain.get("put_mispricing", pd.Series(dtype=float)),
                                  chain.get("put_market_mid", pd.Series(dtype=float)))
    n = int(chain.get("call_market_mid", pd.Series(dtype=float)).notna().sum()
            + chain.get("put_market_mid", pd.Series(dtype=float)).notna().sum())
    return MispricingSummary(
        n_with_market=n, call_mae=c_mae, put_mae=p_mae,
        call_mape_pct=c_mape, put_mape_pct=p_mape,
        median_call_diff=c_med, median_put_diff=p_med,
    )
