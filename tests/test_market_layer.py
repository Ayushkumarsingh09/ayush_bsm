"""Market inclination layer: Black-76, live snapshot, mispricing, scenarios."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from analytics.chain import ChainConfig, build_option_chain
from analytics.scenarios import DEFAULT_SCENARIOS, run_scenarios
from data.market_quotes import (attach_market_columns, mid_from_bid_ask,
                                summarize_mispricing)
from data.yahoo_live import fetch_live_snapshot
from pricing.bsm import bsm_price
from pricing.models import PricingModel, resolve_model
from pricing.volatility import ConstantVolatility, PerStrikeVolatility


class TestPricingModels:
    def test_es_contract_selects_black76(self):
        choice = resolve_model("ES Sep26", r=0.0365, q=0.0, dividend_assumed=True)
        assert choice.model is PricingModel.BLACK76
        assert choice.q_effective == pytest.approx(0.0365)

    def test_spx_uses_bsm_with_q(self):
        choice = resolve_model("SPX", r=0.05, q=0.0135)
        assert choice.model is PricingModel.BSM
        assert choice.q_effective == pytest.approx(0.0135)

    def test_black76_equals_bsm_with_q_equals_r(self):
        S, K, T, r, sig = 7500.0, 7500.0, 0.08, 0.0365, 0.1289
        c76 = bsm_price(S, K, T, r, r, sig, True)  # q = r
        c0 = bsm_price(S, K, T, r, 0.0, sig, True)
        assert c76 != pytest.approx(c0, abs=1e-6)
        # Futures ATM call/put nearly equal when F=K under Black-76.
        p76 = bsm_price(S, K, T, r, r, sig, False)
        assert abs(c76 - p76) < 1.0


class TestMarketQuotes:
    def test_mid_from_bid_ask(self):
        assert mid_from_bid_ask(10.0, 12.0) == pytest.approx(11.0)
        assert mid_from_bid_ask(None, None, last=9.5) == pytest.approx(9.5)
        assert mid_from_bid_ask(None, None, last=None) is None

    def test_mispricing_attach(self):
        strikes = np.array([100.0, 105.0])
        core_df, _ = build_option_chain(
            100.0, 0.25, 0.05, 0.01, ConstantVolatility(0.2),
            ChainConfig(strikes_each_side=0, strike_interval=5,
                        atm_method="explicit", explicit_atm=100.0),
            datetime.now() + timedelta(days=90),
            strikes=strikes,
        )
        market = pd.DataFrame({
            "strike": [100.0, 105.0],
            "call_bid": [8.0, 5.0],
            "call_ask": [8.4, 5.4],
            "put_bid": [6.0, 8.0],
            "put_ask": [6.4, 8.5],
        })
        out = attach_market_columns(core_df, market)
        assert out.loc[out["strike"] == 100.0, "call_market_mid"].iloc[0] == pytest.approx(8.2)
        summary = summarize_mispricing(out)
        assert summary.n_with_market >= 2
        assert summary.call_mae is not None


class TestScenarios:
    def test_scenarios_run(self):
        expiry = datetime(2026, 9, 10, 18, 0)
        asof = datetime(2026, 8, 12, 16, 0)
        strikes = np.arange(7400.0, 7600.0, 25.0)
        vols = np.full_like(strikes, 0.13)
        df, _ = build_option_chain(
            7500.0, 0.08, 0.0365, 0.0365,
            PerStrikeVolatility(strikes, vols), ChainConfig(),
            expiry, strikes=strikes, asof=asof,
        )
        scen = run_scenarios(df, 7500.0, 0.08, 0.0365, 0.0365, expiry, asof,
                             DEFAULT_SCENARIOS[:4])
        assert len(scen) == 4
        assert scen.loc[scen["scenario"] == "Base", "d_call"].iloc[0] == pytest.approx(0.0)


class TestLiveYahoo:
    def test_fetch_spx_snapshot(self):
        try:
            snap = fetch_live_snapshot("SPX")
        except RuntimeError as exc:
            pytest.skip(f"Live Yahoo unavailable: {exc}")
        assert snap.spot > 1000
        assert snap.risk_free_rate is None or 0 < snap.risk_free_rate < 0.2
        assert snap.vix is None or 0 < snap.vix < 2
