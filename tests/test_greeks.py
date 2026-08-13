"""First-order Greek tests: reference values + finite-difference validation.

The finite-difference tests are the mathematical heart of the suite: they
verify signs, scaling and conventions of every analytic Greek against
numerical derivatives of the price function itself.
"""

import numpy as np
import pytest

from pricing import bsm, greeks

REF = dict(S=100.0, K=100.0, T=1.0, r=0.05, q=0.0, sigma=0.20)

# Independently verified values for the classic case (raw units).
REF_CALL_DELTA = 0.6368306511756191
REF_PUT_DELTA = REF_CALL_DELTA - 1.0
REF_GAMMA = 0.018762017345846895
REF_VEGA = 37.52403469169379          # per 1.00 of sigma
REF_CALL_THETA = -6.414027546438197   # per year
REF_PUT_THETA = -1.657880423934626    # per year
REF_CALL_RHO = 53.232481545376345     # per 1.00 of r
REF_PUT_RHO = -41.89046090469506      # per 1.00 of r


def _core(**kw):
    p = {**REF, **kw}
    return bsm.compute_core(p["S"], p["K"], p["T"], p["r"], p["q"], p["sigma"])


def _price(is_call, **kw):
    p = {**REF, **kw}
    return bsm.bsm_price(p["S"], p["K"], p["T"], p["r"], p["q"], p["sigma"],
                         is_call)


class TestReferenceGreeks:
    def test_deltas(self):
        core = _core()
        assert greeks.call_delta(core) == pytest.approx(REF_CALL_DELTA, abs=1e-9)
        assert greeks.put_delta(core) == pytest.approx(REF_PUT_DELTA, abs=1e-9)

    def test_gamma(self):
        assert greeks.gamma(_core()) == pytest.approx(REF_GAMMA, abs=1e-12)

    def test_vega(self):
        assert greeks.vega(_core()) == pytest.approx(REF_VEGA, abs=1e-9)

    def test_thetas(self):
        core = _core()
        assert greeks.call_theta(core) == pytest.approx(REF_CALL_THETA, abs=1e-9)
        assert greeks.put_theta(core) == pytest.approx(REF_PUT_THETA, abs=1e-9)

    def test_rhos(self):
        core = _core()
        assert greeks.call_rho(core) == pytest.approx(REF_CALL_RHO, abs=1e-9)
        assert greeks.put_rho(core) == pytest.approx(REF_PUT_RHO, abs=1e-9)


GRID = [
    dict(S=6137.0, K=6125.0, T=0.08, r=0.0525, q=0.0135, sigma=0.185),
    dict(S=6137.0, K=5700.0, T=0.5, r=0.0525, q=0.0135, sigma=0.22),
    dict(S=6137.0, K=6600.0, T=1.2, r=0.03, q=0.02, sigma=0.15),
    dict(S=100.0, K=100.0, T=1.0, r=0.05, q=0.0, sigma=0.20),
    dict(S=100.0, K=140.0, T=0.05, r=0.01, q=0.0, sigma=0.45),
]


@pytest.mark.parametrize("p", GRID)
@pytest.mark.parametrize("is_call", [True, False])
class TestFiniteDifference:
    def test_delta(self, p, is_call):
        h = p["S"] * 1e-5
        fd = (_price(is_call, **{**p, "S": p["S"] + h})
              - _price(is_call, **{**p, "S": p["S"] - h})) / (2 * h)
        core = bsm.compute_core(**p)
        analytic = greeks.call_delta(core) if is_call else greeks.put_delta(core)
        assert float(analytic) == pytest.approx(fd, rel=1e-6, abs=1e-8)

    def test_gamma(self, p, is_call):
        h = p["S"] * 1e-4
        fd = (_price(is_call, **{**p, "S": p["S"] + h})
              - 2 * _price(is_call, **p)
              + _price(is_call, **{**p, "S": p["S"] - h})) / h**2
        analytic = float(greeks.gamma(bsm.compute_core(**p)))
        assert analytic == pytest.approx(fd, rel=1e-4, abs=1e-10)

    def test_vega(self, p, is_call):
        h = 1e-6
        fd = (_price(is_call, **{**p, "sigma": p["sigma"] + h})
              - _price(is_call, **{**p, "sigma": p["sigma"] - h})) / (2 * h)
        analytic = float(greeks.vega(bsm.compute_core(**p)))
        assert analytic == pytest.approx(fd, rel=1e-5, abs=1e-8)

    def test_theta_is_calendar_time_derivative(self, p, is_call):
        # dV/dt = -dV/dT: as one unit of calendar time passes, T falls by one.
        h = min(1e-6, p["T"] * 0.01)
        fd = (_price(is_call, **{**p, "T": p["T"] - h})
              - _price(is_call, **{**p, "T": p["T"] + h})) / (2 * h)
        core = bsm.compute_core(**p)
        analytic = greeks.call_theta(core) if is_call else greeks.put_theta(core)
        assert float(analytic) == pytest.approx(fd, rel=1e-4, abs=1e-8)

    def test_rho(self, p, is_call):
        h = 1e-6
        fd = (_price(is_call, **{**p, "r": p["r"] + h})
              - _price(is_call, **{**p, "r": p["r"] - h})) / (2 * h)
        core = bsm.compute_core(**p)
        analytic = greeks.call_rho(core) if is_call else greeks.put_rho(core)
        assert float(analytic) == pytest.approx(fd, rel=1e-5, abs=1e-8)


class TestSanity:
    def test_call_delta_bounds_and_monotonicity(self):
        strikes = np.arange(5000.0, 7500.0, 25.0)
        core = bsm.compute_core(6137.0, strikes, 0.3, 0.0525, 0.0135, 0.185)
        d = greeks.call_delta(core)
        disc_q = np.exp(-0.0135 * 0.3)
        assert np.all(d > 0) and np.all(d < disc_q)
        assert np.all(np.diff(d) < 0)  # delta falls as strike rises

    def test_gamma_vega_positive_and_peak_near_atm(self):
        strikes = np.arange(5000.0, 7500.0, 25.0)
        core = bsm.compute_core(6137.0, strikes, 0.3, 0.0525, 0.0135, 0.185)
        g = greeks.gamma(core)
        v = greeks.vega(core)
        assert np.all(g > 0) and np.all(v > 0)
        peak_strike = strikes[np.argmax(g)]
        assert abs(peak_strike - 6137.0) < 200.0
