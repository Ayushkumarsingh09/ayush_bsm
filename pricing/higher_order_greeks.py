"""Second-, third- and higher-order BSM Greeks, in raw units.

Sign and time conventions (documented and enforced by finite-difference
tests in ``tests/test_higher_order_greeks.py``):

* Time-derivative Greeks (Charm, Color) are stated with respect to
  *calendar time t passing* (i.e. -d/dT where T is time-to-expiry),
  matching the Theta convention. Raw units are per year.
* Volatility derivatives are per 1.00 of sigma (per 100 vol points).

Formulas (all with continuous dividend yield q):

    Vanna  = d2V/(dS dsigma)      = -e^{-qT} phi(d1) d2 / sigma
    Volga  = d2V/dsigma2          = Vega d1 d2 / sigma        (a.k.a. Vomma)
    Charm  = dDelta/dt:
        call: q e^{-qT} N(d1)  - e^{-qT} phi(d1) A
        put : -q e^{-qT} N(-d1) - e^{-qT} phi(d1) A
        with A = [2(r-q)T - d2 sigma sqrt(T)] / (2 T sigma sqrt(T))
    Speed  = d3V/dS3              = -(Gamma / S) (d1/(sigma sqrt(T)) + 1)
    Zomma  = dGamma/dsigma        = Gamma (d1 d2 - 1) / sigma
    Color  = dGamma/dt            = +e^{-qT} phi(d1) / (2 S T sigma sqrt(T)) *
                                     [2qT + 1 + d1 (2(r-q)T - d2 sigma sqrt(T))
                                      / (sigma sqrt(T))]
    Ultima = d3V/dsigma3          = -(Vega / sigma^2) *
                                     [d1 d2 (1 - d1 d2) + d1^2 + d2^2]

Vanna, Volga, Speed, Zomma, Color and Ultima are identical for calls and
puts; Charm differs by the dividend term.
"""

from __future__ import annotations

import numpy as np

from pricing.bsm import BSMCore
from pricing.greeks import gamma as gamma_fn
from pricing.greeks import vega as vega_fn


def _nan_like(core: BSMCore) -> np.ndarray:
    return np.full_like(core.S, np.nan)


def vanna(core: BSMCore) -> np.ndarray:
    """Vanna = d2V/(dS dsigma) = -e^{-qT} phi(d1) d2 / sigma."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = -core.disc_q[m] * core.pdf_d1[m] * core.d2[m] / core.sigma[m]
    return out


def volga(core: BSMCore) -> np.ndarray:
    """Volga (Vomma) = d2V/dsigma2 = Vega d1 d2 / sigma."""
    out = _nan_like(core)
    m = core.mask_regular
    v = vega_fn(core)
    out[m] = v[m] * core.d1[m] * core.d2[m] / core.sigma[m]
    return out


def _charm_common(core: BSMCore, m: np.ndarray) -> np.ndarray:
    """Shared term A = [2(r-q)T - d2 sigma sqrt(T)] / (2 T sigma sqrt(T))."""
    num = 2.0 * (core.r[m] - core.q[m]) * core.T[m] - (
        core.d2[m] * core.sigma[m] * core.sqrt_T[m]
    )
    den = 2.0 * core.T[m] * core.sigma[m] * core.sqrt_T[m]
    return num / den


def call_charm(core: BSMCore) -> np.ndarray:
    """Charm_call = dDelta_call/dt (calendar-time delta decay, per year)."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = (
        core.q[m] * core.disc_q[m] * core.cdf_d1[m]
        - core.disc_q[m] * core.pdf_d1[m] * _charm_common(core, m)
    )
    return out


def put_charm(core: BSMCore) -> np.ndarray:
    """Charm_put = dDelta_put/dt (calendar-time delta decay, per year)."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = (
        -core.q[m] * core.disc_q[m] * core.cdf_neg_d1[m]
        - core.disc_q[m] * core.pdf_d1[m] * _charm_common(core, m)
    )
    return out


def speed(core: BSMCore) -> np.ndarray:
    """Speed = d3V/dS3 = -(Gamma / S) (d1 / (sigma sqrt(T)) + 1)."""
    out = _nan_like(core)
    m = core.mask_regular
    g = gamma_fn(core)
    out[m] = -(g[m] / core.S[m]) * (
        core.d1[m] / (core.sigma[m] * core.sqrt_T[m]) + 1.0
    )
    return out


def zomma(core: BSMCore) -> np.ndarray:
    """Zomma = dGamma/dsigma = Gamma (d1 d2 - 1) / sigma."""
    out = _nan_like(core)
    m = core.mask_regular
    g = gamma_fn(core)
    out[m] = g[m] * (core.d1[m] * core.d2[m] - 1.0) / core.sigma[m]
    return out


def color(core: BSMCore) -> np.ndarray:
    """Color = dGamma/dt (calendar-time gamma decay, per year).

    Note: many references state this formula with a leading minus sign,
    which corresponds to dGamma/dT (T = time to expiry). Our documented
    convention is calendar time (dt = -dT), matching Theta and Charm, so
    the sign is positive here. Verified by finite differences in tests.
    """
    out = _nan_like(core)
    m = core.mask_regular
    sig_sqrt_t = core.sigma[m] * core.sqrt_T[m]
    bracket = (
        2.0 * core.q[m] * core.T[m]
        + 1.0
        + core.d1[m]
        * (2.0 * (core.r[m] - core.q[m]) * core.T[m] - core.d2[m] * sig_sqrt_t)
        / sig_sqrt_t
    )
    out[m] = (
        core.disc_q[m]
        * core.pdf_d1[m]
        / (2.0 * core.S[m] * core.T[m] * sig_sqrt_t)
        * bracket
    )
    return out


def ultima(core: BSMCore) -> np.ndarray:
    """Ultima = d3V/dsigma3 = -(Vega/sigma^2)[d1 d2 (1 - d1 d2) + d1^2 + d2^2]."""
    out = _nan_like(core)
    m = core.mask_regular
    v = vega_fn(core)
    d1d2 = core.d1[m] * core.d2[m]
    out[m] = -(v[m] / core.sigma[m] ** 2) * (
        d1d2 * (1.0 - d1d2) + core.d1[m] ** 2 + core.d2[m] ** 2
    )
    return out
