"""Black-Scholes-Merton pricing core for European (SPX-style) options.

Model
-----
For spot ``S``, strike ``K``, time to expiry ``T`` (years), risk-free rate
``r``, continuous dividend yield ``q`` and volatility ``sigma``:

    d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T))
    d2 = d1 - sigma sqrt(T)

    Call = S e^{-qT} N(d1) - K e^{-rT} N(d2)
    Put  = K e^{-rT} N(-d2) - S e^{-qT} N(-d1)

Numerical safety
----------------
Three regimes are handled explicitly with masks (never by letting division
by ``sigma sqrt(T)`` explode):

* ``regular``  : T > 0 and sigma > 0 -- full BSM formulas.
* ``zero_vol`` : T > 0 and sigma ~= 0 -- price equals the discounted
  intrinsic value on the forward, ``e^{-rT} max(+-(F - K), 0)`` with
  ``F = S e^{(r-q)T}``. Greeks are reported as NaN (many are step
  functions / Dirac deltas in this degenerate limit; we refuse to display
  fake finite values).
* ``expired``  : T <= 0 -- price equals intrinsic value ``max(+-(S-K), 0)``
  and all Greeks are NaN (undefined at expiry).

All functions are vectorized over NumPy arrays and use float64 throughout.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from config.settings import EPS_SIGMA, EPS_TIME

ArrayLike = "np.typing.ArrayLike"


@dataclass(frozen=True)
class BSMCore:
    """Precomputed quantities shared by the pricer and all Greek formulas.

    All members are float64 ndarrays of a common broadcast shape. In the
    ``zero_vol`` and ``expired`` regimes, ``d1``/``d2`` and derived CDF/PDF
    values are NaN; downstream code must consult the masks.
    """

    S: np.ndarray
    K: np.ndarray
    T: np.ndarray
    r: np.ndarray
    q: np.ndarray
    sigma: np.ndarray
    sqrt_T: np.ndarray
    d1: np.ndarray
    d2: np.ndarray
    pdf_d1: np.ndarray
    cdf_d1: np.ndarray
    cdf_d2: np.ndarray
    cdf_neg_d1: np.ndarray
    cdf_neg_d2: np.ndarray
    disc_q: np.ndarray  # e^{-qT}
    disc_r: np.ndarray  # e^{-rT}
    forward: np.ndarray  # F = S e^{(r-q)T}
    mask_expired: np.ndarray
    mask_zero_vol: np.ndarray
    mask_regular: np.ndarray


def _as_float_arrays(*values) -> list[np.ndarray]:
    arrays = [np.asarray(v, dtype=np.float64) for v in values]
    return [np.array(a, dtype=np.float64) for a in np.broadcast_arrays(*arrays)]


def compute_core(S, K, T, r, q, sigma) -> BSMCore:
    """Broadcast inputs and precompute d1/d2, CDFs, discounts and masks."""
    S, K, T, r, q, sigma = _as_float_arrays(S, K, T, r, q, sigma)

    mask_expired = T <= EPS_TIME
    mask_zero_vol = (~mask_expired) & (sigma <= EPS_SIGMA)
    mask_regular = ~(mask_expired | mask_zero_vol)

    sqrt_T = np.sqrt(np.where(T > 0.0, T, 0.0))
    disc_q = np.exp(-q * np.maximum(T, 0.0))
    disc_r = np.exp(-r * np.maximum(T, 0.0))
    forward = S * np.exp((r - q) * np.maximum(T, 0.0))

    d1 = np.full_like(S, np.nan)
    d2 = np.full_like(S, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = sigma * sqrt_T
        num = np.log(S / K) + (r - q + 0.5 * sigma**2) * T
        d1_all = num / denom
        d2_all = d1_all - denom
    d1[mask_regular] = d1_all[mask_regular]
    d2[mask_regular] = d2_all[mask_regular]

    pdf_d1 = norm.pdf(d1)
    cdf_d1 = norm.cdf(d1)
    cdf_d2 = norm.cdf(d2)
    cdf_neg_d1 = norm.cdf(-d1)
    cdf_neg_d2 = norm.cdf(-d2)

    return BSMCore(
        S=S, K=K, T=T, r=r, q=q, sigma=sigma, sqrt_T=sqrt_T,
        d1=d1, d2=d2, pdf_d1=pdf_d1,
        cdf_d1=cdf_d1, cdf_d2=cdf_d2,
        cdf_neg_d1=cdf_neg_d1, cdf_neg_d2=cdf_neg_d2,
        disc_q=disc_q, disc_r=disc_r, forward=forward,
        mask_expired=mask_expired, mask_zero_vol=mask_zero_vol,
        mask_regular=mask_regular,
    )


def call_price(core: BSMCore) -> np.ndarray:
    """BSM theoretical call value with edge-case regimes handled."""
    price = np.full_like(core.S, np.nan)

    m = core.mask_regular
    price[m] = (
        core.S[m] * core.disc_q[m] * core.cdf_d1[m]
        - core.K[m] * core.disc_r[m] * core.cdf_d2[m]
    )

    m = core.mask_zero_vol
    price[m] = core.disc_r[m] * np.maximum(core.forward[m] - core.K[m], 0.0)

    m = core.mask_expired
    price[m] = np.maximum(core.S[m] - core.K[m], 0.0)
    return price


def put_price(core: BSMCore) -> np.ndarray:
    """BSM theoretical put value with edge-case regimes handled."""
    price = np.full_like(core.S, np.nan)

    m = core.mask_regular
    price[m] = (
        core.K[m] * core.disc_r[m] * core.cdf_neg_d2[m]
        - core.S[m] * core.disc_q[m] * core.cdf_neg_d1[m]
    )

    m = core.mask_zero_vol
    price[m] = core.disc_r[m] * np.maximum(core.K[m] - core.forward[m], 0.0)

    m = core.mask_expired
    price[m] = np.maximum(core.K[m] - core.S[m], 0.0)
    return price


def parity_error(core: BSMCore, call: np.ndarray, put: np.ndarray) -> np.ndarray:
    """Put-call parity residual: (C - P) - (S e^{-qT} - K e^{-rT}).

    Should be zero to machine precision for live (non-expired) options.
    """
    lhs = call - put
    rhs = core.S * core.disc_q - core.K * core.disc_r
    err = lhs - rhs
    # Parity in this discounted form does not hold at expiry (T=0 reduces to
    # intrinsic parity C - P = S - K which is the same formula, so keep it).
    return err


# ---------------------------------------------------------------------------
# Convenience scalar API (used by tests and the implied-vol solver)
# ---------------------------------------------------------------------------

def bsm_price(S: float, K: float, T: float, r: float, q: float,
              sigma: float, is_call: bool) -> float:
    """Scalar BSM price. Thin wrapper over the vectorized engine."""
    core = compute_core(S, K, T, r, q, sigma)
    price = call_price(core) if is_call else put_price(core)
    return float(price)
