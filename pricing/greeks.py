"""First-order Greeks (plus Gamma) for the BSM model, in raw units.

Raw-unit conventions (display scaling happens in ``pricing.conventions``):

* Delta  : dV/dS -- per 1 index point of spot.
* Gamma  : d2V/dS2 -- change of delta per 1 index point (second order,
  computed here because it shares all inputs with the first-order set).
* Vega   : dV/dsigma -- per 1.00 of volatility (i.e. per 100 vol points).
* Theta  : dV/dt -- per *year* of calendar time (negative of dV/dT).
* Rho    : dV/dr -- per 1.00 of rate (i.e. per 100 percentage points).

Greeks are NaN in the ``expired`` and ``zero_vol`` regimes: at expiry most
Greeks are singular or undefined, and in the zero-volatility limit delta and
gamma degenerate to step/Dirac functions. We deliberately report NaN rather
than fake finite values (rendered as "N/A" in the UI).
"""

from __future__ import annotations

import numpy as np

from pricing.bsm import BSMCore


def _nan_like(core: BSMCore) -> np.ndarray:
    return np.full_like(core.S, np.nan)


def call_delta(core: BSMCore) -> np.ndarray:
    """Delta_call = e^{-qT} N(d1)."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = core.disc_q[m] * core.cdf_d1[m]
    return out


def put_delta(core: BSMCore) -> np.ndarray:
    """Delta_put = e^{-qT} (N(d1) - 1)."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = core.disc_q[m] * (core.cdf_d1[m] - 1.0)
    return out


def gamma(core: BSMCore) -> np.ndarray:
    """Gamma = e^{-qT} phi(d1) / (S sigma sqrt(T)). Identical for calls/puts."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = core.disc_q[m] * core.pdf_d1[m] / (
        core.S[m] * core.sigma[m] * core.sqrt_T[m]
    )
    return out


def vega(core: BSMCore) -> np.ndarray:
    """Vega = S e^{-qT} phi(d1) sqrt(T). Identical for calls/puts.

    Raw units: per 1.00 change in sigma. Display convention typically
    scales by 0.01 (per +1 volatility percentage point).
    """
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = core.S[m] * core.disc_q[m] * core.pdf_d1[m] * core.sqrt_T[m]
    return out


def call_theta(core: BSMCore) -> np.ndarray:
    """Annual call theta (dV/dt, calendar time).

    Theta_call = -S e^{-qT} phi(d1) sigma / (2 sqrt(T))
                 + q S e^{-qT} N(d1)
                 - r K e^{-rT} N(d2)
    """
    out = _nan_like(core)
    m = core.mask_regular
    decay = -core.S[m] * core.disc_q[m] * core.pdf_d1[m] * core.sigma[m] / (
        2.0 * core.sqrt_T[m]
    )
    out[m] = (
        decay
        + core.q[m] * core.S[m] * core.disc_q[m] * core.cdf_d1[m]
        - core.r[m] * core.K[m] * core.disc_r[m] * core.cdf_d2[m]
    )
    return out


def put_theta(core: BSMCore) -> np.ndarray:
    """Annual put theta (dV/dt, calendar time).

    Theta_put = -S e^{-qT} phi(d1) sigma / (2 sqrt(T))
                - q S e^{-qT} N(-d1)
                + r K e^{-rT} N(-d2)
    """
    out = _nan_like(core)
    m = core.mask_regular
    decay = -core.S[m] * core.disc_q[m] * core.pdf_d1[m] * core.sigma[m] / (
        2.0 * core.sqrt_T[m]
    )
    out[m] = (
        decay
        - core.q[m] * core.S[m] * core.disc_q[m] * core.cdf_neg_d1[m]
        + core.r[m] * core.K[m] * core.disc_r[m] * core.cdf_neg_d2[m]
    )
    return out


def call_rho(core: BSMCore) -> np.ndarray:
    """Rho_call = K T e^{-rT} N(d2). Raw: per 1.00 change in r."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = core.K[m] * core.T[m] * core.disc_r[m] * core.cdf_d2[m]
    return out


def put_rho(core: BSMCore) -> np.ndarray:
    """Rho_put = -K T e^{-rT} N(-d2). Raw: per 1.00 change in r."""
    out = _nan_like(core)
    m = core.mask_regular
    out[m] = -core.K[m] * core.T[m] * core.disc_r[m] * core.cdf_neg_d2[m]
    return out
