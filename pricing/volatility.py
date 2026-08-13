"""Volatility provider abstraction.

The pricing engine consumes a per-strike volatility array, never a single
hardcoded number, so smile/skew and full surfaces sigma(K, T) can be
plugged in without touching the BSM mathematics.

Implementations:

* :class:`ConstantVolatility` -- flat sigma across strikes.
* :class:`PerStrikeVolatility` -- explicit sigma per strike (smile).
* :class:`MarketVolatilitySurface` -- bilinear sigma(K, T) from observed
  market points (no fabricated quotes — extrapolates flat at edges).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class VolatilityProvider(ABC):
    """Maps strikes (and time to expiry) to volatilities."""

    @abstractmethod
    def sigma(self, strikes: np.ndarray, T: float) -> np.ndarray:
        """Return per-strike volatility array (decimal, e.g. 0.185)."""


class ConstantVolatility(VolatilityProvider):
    def __init__(self, sigma_value: float) -> None:
        self._sigma = float(sigma_value)

    def sigma(self, strikes: np.ndarray, T: float) -> np.ndarray:
        return np.full_like(np.asarray(strikes, dtype=np.float64), self._sigma)


class PerStrikeVolatility(VolatilityProvider):
    """Interpolates volatility across strikes from known (strike, vol) pairs."""

    def __init__(self, strikes: np.ndarray, sigmas: np.ndarray) -> None:
        order = np.argsort(np.asarray(strikes, dtype=np.float64))
        self._strikes = np.asarray(strikes, dtype=np.float64)[order]
        self._sigmas = np.asarray(sigmas, dtype=np.float64)[order]

    def sigma(self, strikes: np.ndarray, T: float) -> np.ndarray:
        return np.interp(
            np.asarray(strikes, dtype=np.float64), self._strikes, self._sigmas
        )


class MarketVolatilitySurface(VolatilityProvider):
    """Bilinear sigma(K, T) on a scattered market grid.

    Points are unique (K, T) observations. For a query tenor T, we
    interpolate in strike on the nearest tenors and then blend in T.
    Outside the observed strike/tenor range, values clamp to the edge
    (flat extrapolation) — never invent new market quotes.
    """

    def __init__(self, strikes: np.ndarray, tenors: np.ndarray,
                 sigmas: np.ndarray) -> None:
        k = np.asarray(strikes, dtype=np.float64)
        t = np.asarray(tenors, dtype=np.float64)
        s = np.asarray(sigmas, dtype=np.float64)
        mask = np.isfinite(k) & np.isfinite(t) & np.isfinite(s) & (s > 0) & (t > 0)
        k, t, s = k[mask], t[mask], s[mask]
        if len(k) < 3:
            raise ValueError("Need at least 3 surface points")
        self._k = k
        self._t = t
        self._s = s
        self._tenors = np.unique(np.round(t, 10))
        if len(self._tenors) == 0:
            raise ValueError("No positive tenors in surface")

    def _smile_at_tenor(self, tenor: float) -> tuple[np.ndarray, np.ndarray]:
        # Collect points whose T is closest to tenor (exact bucket if any).
        diffs = np.abs(self._t - tenor)
        # Prefer exact matches within 1e-6 years (~30s).
        exact = diffs < 1e-6
        if exact.any():
            return self._k[exact], self._s[exact]
        nearest = self._tenors[np.argmin(np.abs(self._tenors - tenor))]
        mask = np.abs(self._t - nearest) < 1e-6
        if not mask.any():
            # Fallback: all points at globally nearest observed T values
            mask = np.abs(self._t - self._t[np.argmin(diffs)]) < 1e-4
        return self._k[mask], self._s[mask]

    def _interp_smile(self, strikes: np.ndarray, k: np.ndarray,
                      s: np.ndarray) -> np.ndarray:
        if len(k) == 0:
            return np.full(len(strikes), np.nan)
        order = np.argsort(k)
        return np.interp(strikes, k[order], s[order])

    def sigma(self, strikes: np.ndarray, T: float) -> np.ndarray:
        strikes = np.asarray(strikes, dtype=np.float64)
        T = float(T)
        if T <= 0:
            # Degenerate — return ATM-ish mean of surface
            return np.full(len(strikes), float(np.nanmean(self._s)))

        tenors = self._tenors
        if len(tenors) == 1 or T <= tenors.min() or T >= tenors.max():
            # Clamp to nearest tenor smile
            k, s = self._smile_at_tenor(T)
            return self._interp_smile(strikes, k, s)

        # Bracket T between two observed tenors
        hi_idx = int(np.searchsorted(tenors, T, side="left"))
        hi_idx = min(max(hi_idx, 1), len(tenors) - 1)
        t0, t1 = float(tenors[hi_idx - 1]), float(tenors[hi_idx])
        if t1 <= t0:
            k, s = self._smile_at_tenor(T)
            return self._interp_smile(strikes, k, s)
        w = (T - t0) / (t1 - t0)
        k0, s0 = self._smile_at_tenor(t0)
        k1, s1 = self._smile_at_tenor(t1)
        v0 = self._interp_smile(strikes, k0, s0)
        v1 = self._interp_smile(strikes, k1, s1)
        return (1.0 - w) * v0 + w * v1
