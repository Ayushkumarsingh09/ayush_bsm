"""Numerical-stability and expiry edge cases: nothing may explode or lie."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from analytics.chain import ChainConfig, build_option_chain
from pricing import bsm, greeks, higher_order_greeks as hog
from pricing.volatility import ConstantVolatility

ALL_GREEK_FNS = [
    greeks.call_delta, greeks.put_delta, greeks.gamma, greeks.vega,
    greeks.call_theta, greeks.put_theta, greeks.call_rho, greeks.put_rho,
    hog.vanna, hog.volga, hog.call_charm, hog.put_charm,
    hog.speed, hog.zomma, hog.color, hog.ultima,
]


class TestExpiry:
    def test_expired_prices_are_intrinsic_and_greeks_nan(self):
        core = bsm.compute_core(6137.0, np.array([6000.0, 6137.0, 6300.0]),
                                0.0, 0.0525, 0.0135, 0.185)
        assert np.allclose(bsm.call_price(core), [137.0, 0.0, 0.0])
        assert np.allclose(bsm.put_price(core), [0.0, 0.0, 163.0])
        for fn in ALL_GREEK_FNS:
            assert np.all(np.isnan(fn(core))), fn.__name__

    def test_negative_T_treated_as_expired(self):
        core = bsm.compute_core(6137.0, 6000.0, -0.1, 0.0525, 0.0135, 0.185)
        assert float(bsm.call_price(core)) == pytest.approx(137.0)
        assert np.isnan(float(greeks.gamma(core)))


class TestZeroVol:
    def test_zero_vol_greeks_are_nan_not_fake(self):
        core = bsm.compute_core(6137.0, 6000.0, 0.5, 0.0525, 0.0135, 0.0)
        for fn in ALL_GREEK_FNS:
            assert np.all(np.isnan(fn(core))), fn.__name__


class TestNumericalStability:
    @pytest.mark.parametrize("T", [1e-9, 1e-6, 1e-4, 1e-2])
    def test_tiny_T_finite_prices(self, T):
        strikes = np.arange(5400.0, 6900.0, 25.0)
        core = bsm.compute_core(6137.0, strikes, T, 0.0525, 0.0135, 0.185)
        call, put = bsm.call_price(core), bsm.put_price(core)
        assert np.all(np.isfinite(call)) and np.all(call >= 0)
        assert np.all(np.isfinite(put)) and np.all(put >= 0)

    @pytest.mark.parametrize("sigma", [1e-9, 1e-6, 1e-3])
    def test_tiny_sigma_finite(self, sigma):
        core = bsm.compute_core(6137.0, 6137.0, 0.5, 0.0525, 0.0135, sigma)
        assert np.isfinite(float(bsm.call_price(core)))

    def test_extreme_strikes_no_nan_prices(self):
        strikes = np.array([1.0, 100.0, 6137.0, 1e5, 1e7])
        core = bsm.compute_core(6137.0, strikes, 0.5, 0.0525, 0.0135, 0.185)
        call, put = bsm.call_price(core), bsm.put_price(core)
        assert np.all(np.isfinite(call)) and np.all(np.isfinite(put))
        # Extreme ITM/OTM greeks may underflow but must never be +-inf.
        for fn in ALL_GREEK_FNS:
            values = fn(core)
            assert not np.any(np.isinf(values)), fn.__name__

    def test_huge_vol(self):
        core = bsm.compute_core(6137.0, 6137.0, 1.0, 0.0525, 0.0135, 4.9)
        call = float(bsm.call_price(core))
        assert np.isfinite(call)
        assert call <= 6137.0  # bounded by discounted spot

    def test_prices_never_negative_across_stress_grid(self):
        S = 6137.0
        strikes = np.arange(1000.0, 12000.0, 100.0)
        for T in [1e-8, 0.001, 0.1, 1.0, 5.0]:
            for sigma in [0.0, 0.01, 0.185, 1.0, 4.0]:
                core = bsm.compute_core(S, strikes, T, 0.0525, 0.0135, sigma)
                assert np.all(bsm.call_price(core) >= -1e-12)
                assert np.all(bsm.put_price(core) >= -1e-12)


class TestChainAtExpiry:
    def test_chain_builds_for_expired_option_without_crash(self):
        df, meta = build_option_chain(
            spot=6137.0, T=0.0, r=0.0525, q=0.0135,
            vol_provider=ConstantVolatility(0.185),
            cfg=ChainConfig(), expiry=datetime.now() - timedelta(days=1),
        )
        assert meta.is_expired
        assert len(df) == 57
        assert np.all(df["call_bsm_premium"] >= 0)
        assert df["call_delta"].isna().all()
