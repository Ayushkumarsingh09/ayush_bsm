"""Data-provider abstraction.

The pricing engine never talks to a broker/API directly; it consumes a
:class:`MarketInputs` produced by a :class:`DataProvider`. New sources
(broker APIs, exchange feeds, institutional data) plug in by subclassing
``DataProvider`` -- the BSM mathematics never changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class DataProviderError(Exception):
    """Raised when a provider cannot supply valid market inputs."""


class DataProviderNotConfiguredError(DataProviderError):
    """Raised when a provider (e.g. live feed) has no configured source."""


@dataclass(frozen=True)
class MarketInputs:
    """Validated market inputs for one pricing run.

    Rates and volatility are decimals (0.185 = 18.5%). ``dividend_assumed``
    flags that q defaulted to 0 because the source provided none; the UI
    must surface that assumption. ``asof`` is an optional valuation
    timestamp from the data file (used for T instead of wall-clock now).
    """

    spot: float
    volatility: float
    risk_free_rate: float
    dividend_yield: float
    expiry: datetime
    source: str
    dividend_assumed: bool = False
    asof: datetime | None = None


class DataProvider(ABC):
    """Supplies market inputs to the pricing layer."""

    name: str = "abstract"

    @abstractmethod
    def get_market_inputs(self) -> MarketInputs:
        """Return validated market inputs or raise ``DataProviderError``."""

    def is_configured(self) -> bool:
        return True
