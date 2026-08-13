"""Live underlier / rate / VIX fetch via Yahoo chart API (no fabricated data).

Option *chains* on Yahoo currently require authenticated endpoints and are
not used here. Spot (SPX/ES), IRX/TNX rates and VIX are fetched from the
public chart API with an explicit User-Agent.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass(frozen=True)
class LiveSnapshot:
    symbol: str
    spot: float
    asof: datetime
    risk_free_rate: float | None
    rate_source: str | None
    dividend_yield: float | None
    dividend_source: str | None
    vix: float | None
    source: str


def _chart_last(symbol: str, timeout: float = 15.0) -> tuple[float, datetime]:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol)
           + "?range=5d&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Live fetch failed for {symbol}: {exc}") from exc
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"No chart result for {symbol}")
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        price = meta.get("previousClose")
    if price is None:
        raise RuntimeError(f"No price in chart meta for {symbol}")
    ts = meta.get("regularMarketTime")
    asof = (datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
            if ts else datetime.utcnow())
    return float(price), asof


def fetch_live_snapshot(underlying: str = "SPX",
                        timeout: float = 15.0) -> LiveSnapshot:
    """Fetch a live snapshot for SPX (cash) or ES (futures).

    Rates: prefer ^IRX (13-week T-bill, percent) → decimal; fallback ^TNX/100
    is *not* used as r (10Y is too long) — only IRX. Dividend: SPX proxy 1.3%
    when cash index; futures use q=None (Black-76 applies q=r later).
    """
    u = underlying.strip().upper()
    if u in {"ES", "ES=F", "FUTURES"}:
        spot_sym = "ES=F"
        div = None
        div_src = None
        label = "ES=F"
    else:
        spot_sym = "^GSPC"
        div = 0.013  # long-run SPX continuous-yield proxy; flagged in UI
        div_src = "SPX continuous-yield proxy 1.30% (override in UI)"
        label = "^GSPC (SPX)"

    spot, asof = _chart_last(spot_sym, timeout=timeout)

    rate = None
    rate_src = None
    try:
        irx, _ = _chart_last("^IRX", timeout=timeout)
        # IRX quoted in percent (e.g. 3.705 -> 3.705%)
        rate = irx / 100.0
        rate_src = "^IRX (13-week T-bill)"
    except RuntimeError as exc:
        logger.warning("IRX fetch failed: %s", exc)

    vix = None
    try:
        vix, _ = _chart_last("^VIX", timeout=timeout)
        vix = vix / 100.0  # VIX points -> decimal vol proxy
    except RuntimeError as exc:
        logger.warning("VIX fetch failed: %s", exc)

    return LiveSnapshot(
        symbol=label,
        spot=spot,
        asof=asof,
        risk_free_rate=rate,
        rate_source=rate_src,
        dividend_yield=div,
        dividend_source=div_src,
        vix=vix,
        source=f"Yahoo chart API ({label})",
    )
