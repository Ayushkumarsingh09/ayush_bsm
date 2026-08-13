"""Live market-data provider: multi-source spot + Yahoo option chains.

Spot/rate/VIX come from chart APIs with Stooq failover. Option bid/ask are
fetched via Yahoo crumb session for ^SPX / SPY. Quotes are never fabricated.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from data.base import (DataProvider, DataProviderError,
                       DataProviderNotConfiguredError, MarketInputs)
from data.yahoo_live import (LiveOptionChain, LiveSnapshot,
                             fetch_live_snapshot, fetch_multi_expiry_surface,
                             fetch_option_chain, list_option_expiries)


class LiveMarketDataProvider(DataProvider):
    name = "Live API"

    def __init__(
        self,
        underlying: str = "SPX",
        expiry: datetime | None = None,
        dte_days: float = 30.0,
        fetch_options: bool = True,
        fetch_surface: bool = True,
        max_surface_expiries: int = 6,
    ) -> None:
        self._underlying = underlying
        self._expiry = expiry
        self._dte_days = dte_days
        self._fetch_options = fetch_options
        self._fetch_surface = fetch_surface
        self._max_surface_expiries = max_surface_expiries
        self.snapshot: LiveSnapshot | None = None
        self.option_chain: LiveOptionChain | None = None
        self.surface_slices: list[LiveOptionChain] = []
        self.interpretations: list[str] = []

    def is_configured(self) -> bool:
        return True

    def get_market_inputs(self) -> MarketInputs:
        self.interpretations = []
        self.option_chain = None
        self.surface_slices = []
        try:
            snap = fetch_live_snapshot(self._underlying)
        except Exception as exc:
            # Last resort: try cache even if yahoo_live raised a non-RuntimeError
            # (Pyodide JsException from CORS).
            try:
                from data.yahoo_live import _snapshot_from_cache
                snap = _snapshot_from_cache(self._underlying)
                self.interpretations.append(
                    f"Direct live fetch failed ({type(exc).__name__}); "
                    "using Pages live-cache."
                )
            except Exception:
                raise DataProviderError(
                    f"Live data fetch failed: {exc}. On GitHub Pages, Yahoo is "
                    "blocked by browser CORS — ensure sample_data/live_cache.json "
                    "is loaded (hard-refresh). Or run locally: streamlit run app.py"
                ) from exc
        self.snapshot = snap

        if snap.risk_free_rate is None:
            raise DataProviderError(
                "Live spot was fetched but no risk-free rate (^IRX/^TNX) was "
                "available. Retry or enter rate manually."
            )

        expiry = self._expiry
        market_iv_atm = None

        if self._fetch_options:
            try:
                # Prefer listed expiry nearest to target DTE when none set.
                if expiry is None:
                    expiries = list_option_expiries(self._underlying)
                    target = snap.asof + timedelta(days=self._dte_days)
                    expiry = min(expiries, key=lambda e: abs((e - target).total_seconds()))
                    self.interpretations.append(
                        f"Selected listed expiry {expiry.date()} nearest to "
                        f"target DTE {self._dte_days:g}."
                    )
                chain = fetch_option_chain(self._underlying, expiry=expiry)
                self.option_chain = chain
                # Prefer option underlier quote when consistent
                if abs(chain.spot - snap.spot) / max(snap.spot, 1e-9) < 0.05:
                    pass  # keep snapshot spot; chain spot is same index
                expiry = chain.expiry
                self.interpretations.append(
                    f"Live option chain: {len(chain.frame)} strikes from "
                    f"{chain.source} (expiry {chain.expiry.date()})."
                )
            except RuntimeError as exc:
                self.interpretations.append(
                    f"Live option chain unavailable ({exc}). "
                    "Falling back to spot/VIX theoretical pricing."
                )

        if self._fetch_surface and self.option_chain is not None:
            try:
                self.surface_slices = fetch_multi_expiry_surface(
                    self._underlying,
                    max_expiries=self._max_surface_expiries,
                )
                self.interpretations.append(
                    f"Volatility surface: {len(self.surface_slices)} expiries "
                    "loaded from live option quotes."
                )
            except RuntimeError as exc:
                self.interpretations.append(
                    f"Multi-expiry surface skipped: {exc}"
                )

        if expiry is None:
            expiry = snap.asof + timedelta(days=self._dte_days)
            self.interpretations.append(
                f"No expiry selected: using asof + {self._dte_days:g} calendar days."
            )

        # ATM vol: prefer live chain mid IV, else VIX.
        vol = snap.vix
        if self.option_chain is not None and not self.option_chain.frame.empty:
            from analytics.vol_surface import enrich_chain_ivs
            from utils.dates import DayCount, time_to_expiry
            T = time_to_expiry(expiry, now=snap.asof, convention=DayCount.ACT_365)
            q = 0.0 if snap.dividend_yield is None else float(snap.dividend_yield)
            enriched = enrich_chain_ivs(
                self.option_chain.frame, snap.spot, max(T, 1e-6),
                float(snap.risk_free_rate), q,
            )
            if enriched["market_iv"].notna().any():
                idx = (enriched["strike"] - snap.spot).abs().idxmin()
                market_iv_atm = float(enriched.loc[idx, "market_iv"])
                vol = market_iv_atm
                self.interpretations.append(
                    f"ATM vol from live option mids: {vol*100:.2f}% "
                    "(replaces VIX flat proxy for chain pricing)."
                )

        if vol is None:
            raise DataProviderError(
                "No ATM volatility available (VIX and option IVs both "
                "unavailable). Retry or enter volatility manually."
            )

        u = self._underlying.strip().upper()
        dividend_assumed = snap.dividend_yield is None
        q = 0.0 if dividend_assumed else float(snap.dividend_yield)

        self.interpretations.append(
            f"Live spot {snap.spot:.2f} from {snap.symbol} via "
            f"{'+'.join(snap.spot_sources) or snap.source} asof "
            f"{snap.asof.isoformat(sep=' ', timespec='minutes')}."
        )
        self.interpretations.append(
            f"Risk-free rate {snap.risk_free_rate*100:.3f}% from {snap.rate_source}."
        )
        if snap.vix is not None and market_iv_atm is None:
            self.interpretations.append(
                f"ATM vol proxy from VIX: {snap.vix*100:.2f}%."
            )
        if dividend_assumed:
            self.interpretations.append(
                "Futures underlying: dividend yield omitted (Black-76 uses q = r)."
            )
        elif snap.dividend_source:
            self.interpretations.append(snap.dividend_source)

        return MarketInputs(
            spot=snap.spot,
            volatility=float(vol),
            risk_free_rate=float(snap.risk_free_rate),
            dividend_yield=q,
            expiry=expiry,
            source=snap.source,
            dividend_assumed=dividend_assumed,
            asof=snap.asof,
        )

    def market_table(self) -> pd.DataFrame | None:
        if self.option_chain is None:
            return None
        return self.option_chain.frame.copy()

    def smile_strikes_sigmas(self, r: float, q: float
                             ) -> tuple[list[float], list[float]] | None:
        if self.option_chain is None or self.snapshot is None:
            return None
        from analytics.vol_surface import smile_provider_from_chain
        from utils.dates import DayCount, time_to_expiry
        T = time_to_expiry(
            self.option_chain.expiry, now=self.snapshot.asof,
            convention=DayCount.ACT_365)
        prov = smile_provider_from_chain(
            self.option_chain.frame, self.snapshot.spot, max(T, 1e-6), r, q)
        if prov is None:
            return None
        return list(prov._strikes), list(prov._sigmas)


class UnconfiguredLiveProvider(DataProvider):
    name = "Live API (unconfigured)"

    def is_configured(self) -> bool:
        return False

    def get_market_inputs(self) -> MarketInputs:
        raise DataProviderNotConfiguredError(
            "Live data source: Not configured."
        )
