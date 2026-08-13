"""Pricing tests: reference values, parity, limits, independent re-derivation."""

import numpy as np
import pytest
from scipy.stats import norm

from pricing import bsm

# Classic textbook case: S=100, K=100, T=1, r=5%, q=0, sigma=20%.
REF = dict(S=100.0, K=100.0, T=1.0, r=0.05, q=0.0, sigma=0.20)
REF_D1 = 0.35
REF_D2 = 0.15
REF_CALL = 10.450583572185565
REF_PUT = 5.573526022256971


def _core(**kw):
    p = {**REF, **kw}
    return bsm.compute_core(p["S"], p["K"], p["T"], p["r"], p["q"], p["sigma"])


def _textbook_call(S, K, T, r, q, sigma):
    """Independent straight-line implementation (no masks) for cross-check."""
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def _textbook_put(S, K, T, r, q, sigma):
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


class TestReferenceValues:
    def test_d1_d2(self):
        core = _core()
        assert core.d1 == pytest.approx(REF_D1, abs=1e-12)
        assert core.d2 == pytest.approx(REF_D2, abs=1e-12)

    def test_call_price(self):
        assert bsm.call_price(_core()) == pytest.approx(REF_CALL, abs=1e-9)

    def test_put_price(self):
        assert bsm.put_price(_core()) == pytest.approx(REF_PUT, abs=1e-9)


class TestAgainstIndependentImplementation:
    """Engine must match a mask-free textbook implementation on a broad grid."""

    @pytest.mark.parametrize("S", [50.0, 100.0, 6137.0])
    @pytest.mark.parametrize("moneyness", [0.7, 0.95, 1.0, 1.05, 1.4])
    @pytest.mark.parametrize("T", [0.01, 0.25, 1.0, 3.0])
    @pytest.mark.parametrize("r,q", [(0.05, 0.0), (0.0525, 0.0135), (0.0, 0.03)])
    @pytest.mark.parametrize("sigma", [0.08, 0.185, 0.6])
    def test_grid(self, S, moneyness, T, r, q, sigma):
        K = S / moneyness
        core = bsm.compute_core(S, K, T, r, q, sigma)
        assert bsm.call_price(core) == pytest.approx(
            _textbook_call(S, K, T, r, q, sigma), rel=1e-12, abs=1e-12)
        assert bsm.put_price(core) == pytest.approx(
            _textbook_put(S, K, T, r, q, sigma), rel=1e-12, abs=1e-12)


class TestPutCallParity:
    @pytest.mark.parametrize("q", [0.0, 0.0135, 0.04])
    @pytest.mark.parametrize("T", [0.001, 0.1, 1.0, 2.5])
    def test_parity(self, q, T):
        strikes = np.arange(5400.0, 6900.0, 25.0)
        core = bsm.compute_core(6137.0, strikes, T, 0.0525, q, 0.185)
        call = bsm.call_price(core)
        put = bsm.put_price(core)
        err = bsm.parity_error(core, call, put)
        assert np.all(np.abs(err) < 1e-9)


class TestLimits:
    def test_deep_itm_call_approaches_discounted_forward_intrinsic(self):
        core = bsm.compute_core(6137.0, 1000.0, 0.5, 0.05, 0.01, 0.185)
        expected = 6137.0 * np.exp(-0.01 * 0.5) - 1000.0 * np.exp(-0.05 * 0.5)
        assert bsm.call_price(core) == pytest.approx(expected, rel=1e-10)

    def test_deep_otm_call_approaches_zero(self):
        core = bsm.compute_core(6137.0, 20000.0, 0.25, 0.05, 0.01, 0.185)
        assert 0.0 <= float(bsm.call_price(core)) < 1e-8

    def test_deep_itm_put(self):
        core = bsm.compute_core(6137.0, 20000.0, 0.25, 0.05, 0.01, 0.185)
        expected = 20000.0 * np.exp(-0.05 * 0.25) - 6137.0 * np.exp(-0.01 * 0.25)
        assert bsm.put_price(core) == pytest.approx(expected, rel=1e-10)

    def test_call_decreasing_put_increasing_in_strike(self):
        strikes = np.arange(5000.0, 7500.0, 25.0)
        core = bsm.compute_core(6137.0, strikes, 0.3, 0.05, 0.013, 0.185)
        call = bsm.call_price(core)
        put = bsm.put_price(core)
        assert np.all(np.diff(call) < 0)
        assert np.all(np.diff(put) > 0)


class TestExpiryAndZeroVol:
    def test_expired_intrinsic(self):
        core = bsm.compute_core(6137.0, np.array([6000.0, 6300.0]), 0.0,
                                0.05, 0.01, 0.185)
        assert np.allclose(bsm.call_price(core), [137.0, 0.0])
        assert np.allclose(bsm.put_price(core), [0.0, 163.0])

    def test_zero_vol_prices_discounted_forward_intrinsic(self):
        S, T, r, q = 6137.0, 0.5, 0.05, 0.01
        F = S * np.exp((r - q) * T)
        strikes = np.array([5500.0, 7000.0])
        core = bsm.compute_core(S, strikes, T, r, q, 0.0)
        call = bsm.call_price(core)
        put = bsm.put_price(core)
        assert call[0] == pytest.approx(np.exp(-r * T) * (F - 5500.0), rel=1e-12)
        assert call[1] == 0.0
        assert put[0] == 0.0
        assert put[1] == pytest.approx(np.exp(-r * T) * (7000.0 - F), rel=1e-12)
