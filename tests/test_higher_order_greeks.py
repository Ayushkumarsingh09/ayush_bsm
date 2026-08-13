"""Finite-difference validation of second/third/higher-order Greeks.

Each analytic Greek is compared against a numerical derivative of a
lower-order analytic quantity (itself FD-validated in test_greeks.py),
which catches sign, scaling and convention mistakes.
"""

import pytest

from pricing import bsm, greeks, higher_order_greeks as hog

GRID = [
    dict(S=6137.0, K=6125.0, T=0.08, r=0.0525, q=0.0135, sigma=0.185),
    dict(S=6137.0, K=5800.0, T=0.5, r=0.0525, q=0.0135, sigma=0.22),
    dict(S=6137.0, K=6500.0, T=1.2, r=0.03, q=0.02, sigma=0.15),
    dict(S=100.0, K=100.0, T=1.0, r=0.05, q=0.0, sigma=0.20),
    dict(S=100.0, K=90.0, T=0.2, r=0.02, q=0.01, sigma=0.35),
]


def _scalar(fn, **p):
    return float(fn(bsm.compute_core(**p)))


@pytest.mark.parametrize("p", GRID)
class TestSecondOrder:
    def test_vanna_is_dvega_dspot(self, p):
        h = p["S"] * 1e-5
        fd = (_scalar(greeks.vega, **{**p, "S": p["S"] + h})
              - _scalar(greeks.vega, **{**p, "S": p["S"] - h})) / (2 * h)
        assert _scalar(hog.vanna, **p) == pytest.approx(fd, rel=1e-5, abs=1e-8)

    def test_vanna_is_ddelta_dsigma(self, p):
        h = 1e-6
        fd = (_scalar(greeks.call_delta, **{**p, "sigma": p["sigma"] + h})
              - _scalar(greeks.call_delta, **{**p, "sigma": p["sigma"] - h})
              ) / (2 * h)
        assert _scalar(hog.vanna, **p) == pytest.approx(fd, rel=1e-5, abs=1e-8)

    def test_volga_is_dvega_dsigma(self, p):
        h = 1e-6
        fd = (_scalar(greeks.vega, **{**p, "sigma": p["sigma"] + h})
              - _scalar(greeks.vega, **{**p, "sigma": p["sigma"] - h})) / (2 * h)
        assert _scalar(hog.volga, **p) == pytest.approx(fd, rel=1e-5, abs=1e-7)


@pytest.mark.parametrize("p", GRID)
class TestThirdOrderAndHigher:
    def test_call_charm_is_calendar_delta_decay(self, p):
        # dDelta/dt = -dDelta/dT
        h = min(1e-6, p["T"] * 0.01)
        fd = (_scalar(greeks.call_delta, **{**p, "T": p["T"] - h})
              - _scalar(greeks.call_delta, **{**p, "T": p["T"] + h})) / (2 * h)
        assert _scalar(hog.call_charm, **p) == pytest.approx(fd, rel=1e-4,
                                                             abs=1e-8)

    def test_put_charm_is_calendar_delta_decay(self, p):
        h = min(1e-6, p["T"] * 0.01)
        fd = (_scalar(greeks.put_delta, **{**p, "T": p["T"] - h})
              - _scalar(greeks.put_delta, **{**p, "T": p["T"] + h})) / (2 * h)
        assert _scalar(hog.put_charm, **p) == pytest.approx(fd, rel=1e-4,
                                                            abs=1e-8)

    def test_speed_is_dgamma_dspot(self, p):
        h = p["S"] * 1e-5
        fd = (_scalar(greeks.gamma, **{**p, "S": p["S"] + h})
              - _scalar(greeks.gamma, **{**p, "S": p["S"] - h})) / (2 * h)
        assert _scalar(hog.speed, **p) == pytest.approx(fd, rel=1e-5, abs=1e-12)

    def test_zomma_is_dgamma_dsigma(self, p):
        h = 1e-6
        fd = (_scalar(greeks.gamma, **{**p, "sigma": p["sigma"] + h})
              - _scalar(greeks.gamma, **{**p, "sigma": p["sigma"] - h})) / (2 * h)
        assert _scalar(hog.zomma, **p) == pytest.approx(fd, rel=1e-5, abs=1e-10)

    def test_color_is_calendar_gamma_decay(self, p):
        # dGamma/dt = -dGamma/dT
        h = min(1e-7, p["T"] * 0.001)
        fd = (_scalar(greeks.gamma, **{**p, "T": p["T"] - h})
              - _scalar(greeks.gamma, **{**p, "T": p["T"] + h})) / (2 * h)
        assert _scalar(hog.color, **p) == pytest.approx(fd, rel=1e-4, abs=1e-10)

    def test_ultima_is_dvolga_dsigma(self, p):
        h = 1e-6
        fd = (_scalar(hog.volga, **{**p, "sigma": p["sigma"] + h})
              - _scalar(hog.volga, **{**p, "sigma": p["sigma"] - h})) / (2 * h)
        assert _scalar(hog.ultima, **p) == pytest.approx(fd, rel=1e-4, abs=1e-6)
