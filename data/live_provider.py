"""Live market-data provider using Yahoo chart API for underliers/rates/VIX.

Option chains are not fabricated. When Yahoo option endpoints are
unavailable (HTTP 401), this provider still supplies live spot, a T-bill
rate proxy, VIX as ATM-vol proxy, and an SPX dividend-yield proxy so the
dashboard can price a theoretical chain that is *inclined* to live markets.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from data.base import (DataProvider, DataProviderError,
                       DataProviderNotConfiguredError, MarketInputs)
from data.yahoo_live import LiveSnapshot, fetch_live_snapshot


class LiveMarketDataProvider(DataProvider):
    name = "Live API"

    def __init__(self, underlying: str = "SPX",
                 expiry: datetime | None = None,
                 dte_days: float = 30.0) -> None:
        self._underlying = underlying
        self._expiry = expiry
        self._dte_days = dte_days
        self.snapshot: LiveSnapshot | None = None
        self.interpretations: list[str] = []

    def is_configured(self) -> bool:
        return True

    def get_market_inputs(self) -> MarketInputs:
        self.interpretations = []
        try:
            snap = fetch_live_snapshot(self._underlying)
        except RuntimeError as exc:
            raise DataProviderError(
                f"Live data fetch failed: {exc}. Check network access to "
                "Yahoo Finance, or use Manual / CSV."
            ) from exc
        self.snapshot = snap

        if snap.risk_free_rate is None:
            raise DataProviderError(
                "Live spot was fetched but the risk-free rate (^IRX) was "
                "unavailable. Retry or enter rate manually."
            )
        if snap.vix is None:
            raise DataProviderError(
                "Live spot/rate fetched but VIX was unavailable for the "
                "ATM volatility proxy. Retry or enter volatility manually."
            )

        expiry = self._expiry
        if expiry is None:
            expiry = snap.asof + timedelta(days=self._dte_days)
            self.interpretations.append(
                f"No expiry selected: using asof + {self._dte_days:g} calendar days."
            )

        u = self._underlying.strip().upper()
        dividend_assumed = snap.dividend_yield is None
        q = 0.0 if dividend_assumed else float(snap.dividend_yield)

        self.interpretations.append(
            f"Live spot {snap.spot:.2f} from {snap.symbol} asof "
            f"{snap.asof.isoformat(sep=' ', timespec='minutes')} UTC-epoch."
        )
        self.interpretations.append(
            f"Risk-free rate {snap.risk_free_rate*100:.3f}% from {snap.rate_source}."
        )
        self.interpretations.append(
            f"ATM vol proxy from VIX: {snap.vix*100:.2f}% "
            "(constant across strikes until a smile file/feed is supplied)."
        )
        if dividend_assumed:
            self.interpretations.append(
                "Futures underlying: dividend yield omitted (Black-76 uses q = r)."
            )
        elif snap.dividend_source:
            self.interpretations.append(snap.dividend_source)

        return MarketInputs(
            spot=snap.spot,
            volatility=float(snap.vix),
            risk_free_rate=float(snap.risk_free_rate),
            dividend_yield=q,
            expiry=expiry,
            source=snap.source,
            dividend_assumed=dividend_assumed,
            asof=snap.asof,
        )


# Keep the old "not configured" sentinel available for tests that monkeypatch.
class UnconfiguredLiveProvider(DataProvider):
    name = "Live API (unconfigured)"

    def is_configured(self) -> bool:
        return False

    def get_market_inputs(self) -> MarketInputs:
        raise DataProviderNotConfiguredError(
            "Live data source: Not configured."
        )
