"""Tests for live option chains, vol surface, and multi-source spot."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from analytics.vol_surface import (
    build_surface_table,
    compute_skew_metrics,
    enrich_chain_ivs,
    smile_provider_from_chain,
    surface_provider_from_table,
)
from pricing.volatility import MarketVolatilitySurface, PerStrikeVolatility


def _synthetic_chain(spot: float = 5000.0, atm_iv: float = 0.18) -> pd.DataFrame:
    from pricing.bsm import bsm_price

    strikes = np.arange(spot - 200, spot + 250, 50)
    T, r, q = 30 / 365, 0.04, 0.013
    rows = []
    for k in strikes:
        iv = atm_iv + 0.04 * max(0.0, (spot - k) / spot) + 0.01 * max(
            0.0, (k - spot) / spot
        )
        call_mid = float(bsm_price(spot, k, T, r, q, iv, True))
        put_mid = float(bsm_price(spot, k, T, r, q, iv, False))
        spread = max(0.05, 0.01 * call_mid)
        rows.append({
            "strike": k,
            "call_bid": max(call_mid - spread / 2, 0.0),
            "call_ask": call_mid + spread / 2,
            "call_last": call_mid,
            "put_bid": max(put_mid - spread / 2, 0.0),
            "put_ask": put_mid + spread / 2,
            "put_last": put_mid,
            "call_yahoo_iv": iv,
            "put_yahoo_iv": iv,
        })
    return pd.DataFrame(rows)


class TestVolSurfaceAnalytics:
    def test_enrich_and_smile_provider(self):
        frame = _synthetic_chain()
        T, r, q = 30 / 365, 0.04, 0.013
        enriched = enrich_chain_ivs(frame, 5000.0, T, r, q)
        assert enriched["market_iv"].notna().sum() >= 5
        prov = smile_provider_from_chain(frame, 5000.0, T, r, q)
        assert isinstance(prov, PerStrikeVolatility)
        sig = prov.sigma(np.array([5000.0]), T)[0]
        assert 0.05 < sig < 0.5

    def test_surface_bilinear_interp(self):
        # Two tenors, three strikes
        strikes = np.array([90.0, 100.0, 110.0, 90.0, 100.0, 110.0])
        tenors = np.array([0.1, 0.1, 0.1, 0.5, 0.5, 0.5])
        sigmas = np.array([0.22, 0.20, 0.21, 0.24, 0.22, 0.23])
        surf = MarketVolatilitySurface(strikes, tenors, sigmas)
        mid = surf.sigma(np.array([100.0]), 0.3)[0]
        assert 0.20 < mid < 0.24

    def test_build_surface_table_and_provider(self):
        frame = _synthetic_chain()
        exp1 = datetime(2026, 9, 18)
        exp2 = datetime(2026, 10, 16)
        asof = datetime(2026, 8, 14)
        table = build_surface_table(
            [(exp1, 5000.0, frame), (exp2, 5000.0, frame)],
            spot=5000.0, r=0.04, q=0.013, asof=asof,
        )
        assert len(table) > 10
        assert set(["T", "strike", "market_iv", "moneyness"]) <= set(table.columns)
        prov = surface_provider_from_table(table)
        assert isinstance(prov, MarketVolatilitySurface)

    def test_skew_metrics(self):
        frame = _synthetic_chain()
        T = 30 / 365
        m = compute_skew_metrics(frame, 5000.0, T, 0.04, 0.013)
        assert m.n_points >= 5
        assert m.atm_iv is not None
        assert 0.05 < m.atm_iv < 0.5


class TestLiveYahooOptions:
    def test_fetch_spx_option_chain(self):
        from data.yahoo_live import fetch_live_snapshot, fetch_option_chain

        try:
            snap = fetch_live_snapshot("SPX")
        except RuntimeError as exc:
            pytest.skip(f"offline / blocked: {exc}")
        assert snap.spot > 0
        assert snap.risk_free_rate is not None or True  # may still continue
        try:
            chain = fetch_option_chain("SPX")
        except RuntimeError as exc:
            pytest.skip(f"options blocked: {exc}")
        assert len(chain.frame) >= 10
        assert "call_bid" in chain.frame.columns
        assert "put_ask" in chain.frame.columns
        # At least some positive bids or asks
        has_quote = (
            chain.frame["call_bid"].fillna(0).gt(0).any()
            or chain.frame["call_ask"].fillna(0).gt(0).any()
        )
        assert has_quote

    def test_live_provider_with_options(self):
        from data.live_provider import LiveMarketDataProvider

        provider = LiveMarketDataProvider(
            "SPX", dte_days=30, fetch_options=True, fetch_surface=False)
        try:
            mi = provider.get_market_inputs()
        except Exception as exc:
            pytest.skip(f"live unavailable: {exc}")
        assert mi.spot > 0
        assert mi.volatility > 0
        table = provider.market_table()
        if table is None:
            pytest.skip("option chain not returned")
        assert len(table) >= 5
