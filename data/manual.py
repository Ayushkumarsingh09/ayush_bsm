"""Manual (form-entered) market data provider."""

from __future__ import annotations

from datetime import datetime

from data.base import DataProvider, MarketInputs


class ManualDataProvider(DataProvider):
    """Wraps values entered by the user in the input panel."""

    name = "Manual"

    def __init__(self, spot: float, volatility: float, risk_free_rate: float,
                 dividend_yield: float | None, expiry: datetime) -> None:
        self._spot = float(spot)
        self._volatility = float(volatility)
        self._rate = float(risk_free_rate)
        self._dividend_assumed = dividend_yield is None
        self._dividend = 0.0 if dividend_yield is None else float(dividend_yield)
        self._expiry = expiry

    def get_market_inputs(self) -> MarketInputs:
        return MarketInputs(
            spot=self._spot,
            volatility=self._volatility,
            risk_free_rate=self._rate,
            dividend_yield=self._dividend,
            expiry=self._expiry,
            source=self.name,
            dividend_assumed=self._dividend_assumed,
        )
