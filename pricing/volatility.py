"""Volatility provider abstraction.

The pricing engine consumes a per-strike volatility array, never a single
hardcoded number, so smile/skew and full surfaces sigma(K, T) can be
plugged in later without touching the BSM mathematics.

Current implementations:

* :class:`ConstantVolatility` -- flat sigma across strikes (initial version).
* :class:`PerStrikeVolatility` -- explicit sigma per strike (smile-ready).
* :class:`MarketVolatilitySurface` -- placeholder for a future market
  surface; deliberately raises until a real data source is configured
  (no fabricated data).
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
    """Future sigma(K, T) surface. Not configured in this version."""

    def sigma(self, strikes: np.ndarray, T: float) -> np.ndarray:
        raise NotImplementedError(
            "Market volatility surface is not configured. "
            "Use ConstantVolatility or PerStrikeVolatility."
        )
