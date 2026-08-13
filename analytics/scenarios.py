"""Scenario / shock analysis on top of the BSM chain.

Applies spot, volatility, rate and calendar-day shocks and reports the
change in ATM call/put BSM premiums and first-order Greeks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from analytics.chain import ChainConfig, build_option_chain
from pricing.volatility import ConstantVolatility, PerStrikeVolatility, VolatilityProvider


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    d_spot_pct: float = 0.0      # e.g. -0.01 = -1% spot
    d_vol_pts: float = 0.0       # e.g. +0.01 = +1 vol point
    d_rate_pts: float = 0.0      # e.g. +0.0025 = +25 bp
    d_days: float = 0.0          # calendar days passing (T falls)


def _provider_from_chain(base: pd.DataFrame) -> VolatilityProvider:
    return PerStrikeVolatility(
        base["strike"].to_numpy(dtype=float),
        base["sigma"].to_numpy(dtype=float),
    )


def run_scenarios(base: pd.DataFrame, spot: float, T: float, r: float, q: float,
                  expiry, asof, specs: list[ScenarioSpec],
                  ) -> pd.DataFrame:
    """Return a tidy scenario table (one row per scenario)."""
    cfg = ChainConfig()
    # Anchor ATM from base chain
    atm_rows = base.loc[base["is_atm"]]
    if atm_rows.empty:
        atm_strike = float(base.iloc[(base["strike"] - spot).abs().argmin()]["strike"])
    else:
        atm_strike = float(atm_rows.iloc[0]["strike"])

    base_atm = base.loc[base["strike"] == atm_strike].iloc[0]
    rows = []
    for spec in specs:
        spot_s = spot * (1.0 + spec.d_spot_pct)
        r_s = r + spec.d_rate_pts
        T_s = max(T - spec.d_days / 365.0, 1e-12)
        # Shock vols uniformly in vol-point space.
        strikes = base["strike"].to_numpy(dtype=float)
        sigmas = np.maximum(base["sigma"].to_numpy(dtype=float) + spec.d_vol_pts, 1e-8)
        vol = PerStrikeVolatility(strikes, sigmas)
        chain, _ = build_option_chain(
            spot_s, T_s, r_s, q, vol, cfg, expiry,
            strikes=strikes, asof=asof,
        )
        atm = chain.loc[chain["strike"] == atm_strike]
        if atm.empty:
            atm = chain.iloc[(chain["strike"] - spot_s).abs().argmin()]
        else:
            atm = atm.iloc[0]
        rows.append({
            "scenario": spec.name,
            "spot": spot_s,
            "T": T_s,
            "r": r_s,
            "atm_strike": atm_strike,
            "call_bsm_premium": float(atm["call_bsm_premium"]),
            "put_bsm_premium": float(atm["put_bsm_premium"]),
            "call_delta": float(atm["call_delta"]) if pd.notna(atm["call_delta"]) else np.nan,
            "put_delta": float(atm["put_delta"]) if pd.notna(atm["put_delta"]) else np.nan,
            "gamma": float(atm["gamma"]) if pd.notna(atm["gamma"]) else np.nan,
            "vega_1pct": float(atm["vega"]) * 0.01 if pd.notna(atm["vega"]) else np.nan,
            "d_call": float(atm["call_bsm_premium"] - base_atm["call_bsm_premium"]),
            "d_put": float(atm["put_bsm_premium"] - base_atm["put_bsm_premium"]),
        })
    return pd.DataFrame(rows)


DEFAULT_SCENARIOS = [
    ScenarioSpec("Base", 0, 0, 0, 0),
    ScenarioSpec("Spot -1%", -0.01, 0, 0, 0),
    ScenarioSpec("Spot +1%", 0.01, 0, 0, 0),
    ScenarioSpec("Spot -2%", -0.02, 0, 0, 0),
    ScenarioSpec("Spot +2%", 0.02, 0, 0, 0),
    ScenarioSpec("Vol +1pt", 0, 0.01, 0, 0),
    ScenarioSpec("Vol -1pt", 0, -0.01, 0, 0),
    ScenarioSpec("Vol +2pt", 0, 0.02, 0, 0),
    ScenarioSpec("Rate +25bp", 0, 0, 0.0025, 0),
    ScenarioSpec("Rate -25bp", 0, 0, -0.0025, 0),
    ScenarioSpec("1 day decay", 0, 0, 0, 1),
    ScenarioSpec("Spot -1% & Vol +1pt", -0.01, 0.01, 0, 0),
    ScenarioSpec("Spot +1% & Vol +1pt", 0.01, 0.01, 0, 0),
]
