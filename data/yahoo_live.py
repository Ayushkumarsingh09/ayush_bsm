"""Live underlier / rate / VIX / option-chain fetch via Yahoo Finance.

Uses a cookie + crumb session for option endpoints. Spot/rate/VIX use the
public chart API with query1/query2 failover. Stooq is a last-resort spot
fallback. Quotes are never fabricated — empty/failed fetches raise or return
empty frames with explicit provenance.
"""

from __future__ import annotations

import json
import logging
import math
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Yahoo option symbol keyed by dashboard underlying label.
_OPTION_SYMBOL = {
    "SPX": "^SPX",
    "^SPX": "^SPX",
    "^GSPC": "^SPX",
    "SPY": "SPY",
}


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
    spot_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveOptionChain:
    """One expiry of live option quotes (real bid/ask/last when present)."""
    underlying: str
    yahoo_symbol: str
    spot: float
    expiry: datetime
    asof: datetime
    frame: pd.DataFrame
    source: str
    expiries_available: tuple[datetime, ...] = ()


@dataclass
class _YahooSession:
    opener: urllib.request.OpenerDirector
    crumb: str | None = None
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)


_SESSION: _YahooSession | None = None


def _get_session(timeout: float = 15.0, force: bool = False) -> _YahooSession:
    global _SESSION
    if _SESSION is not None and not force and _SESSION.crumb:
        return _SESSION
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", _UA)]
    # Seed cookies (404 is fine — still sets cookies on some edges).
    for url in ("https://fc.yahoo.com", "https://finance.yahoo.com"):
        try:
            opener.open(url, timeout=timeout)
        except Exception:
            pass
    crumb = None
    try:
        with opener.open(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=timeout,
        ) as resp:
            crumb = resp.read().decode("utf-8").strip()
    except Exception as exc:
        logger.warning("Yahoo crumb fetch failed: %s", exc)
    _SESSION = _YahooSession(opener=opener, crumb=crumb, jar=jar)
    return _SESSION


def _http_json(url: str, timeout: float = 15.0,
               use_session: bool = False) -> dict[str, Any]:
    if use_session:
        session = _get_session(timeout=timeout)
        try:
            with session.opener.open(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                session = _get_session(timeout=timeout, force=True)
                with session.opener.open(url, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chart_last(symbol: str, timeout: float = 15.0) -> tuple[float, datetime, str]:
    """Return (price, asof, host_used). Tries query1 then query2."""
    hosts = (
        "https://query1.finance.yahoo.com/v8/finance/chart/",
        "https://query2.finance.yahoo.com/v8/finance/chart/",
    )
    errors: list[str] = []
    for host in hosts:
        url = host + urllib.parse.quote(symbol) + "?range=5d&interval=1d"
        try:
            payload = _http_json(url, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                urllib.error.HTTPError) as exc:
            errors.append(f"{host}: {exc}")
            continue
        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            errors.append(f"{host}: empty result")
            continue
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            price = meta.get("previousClose")
        if price is None:
            errors.append(f"{host}: no price")
            continue
        ts = meta.get("regularMarketTime")
        asof = (datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
                if ts else datetime.utcnow())
        return float(price), asof, host.split("//")[1].split("/")[0]
    raise RuntimeError(f"Live chart failed for {symbol}: {'; '.join(errors)}")


def _stooq_last(symbol: str, timeout: float = 15.0) -> tuple[float, datetime]:
    """Stooq last for common symbols (spot fallback only)."""
    # Stooq uses lowercase tickers; indices often end with .ind
    mapping = {
        "^GSPC": "^spx",
        "ES=F": "es.f",
        "SPY": "spy.us",
    }
    tick = mapping.get(symbol)
    if tick is None:
        raise RuntimeError(f"No Stooq mapping for {symbol}")
    url = f"https://stooq.com/q/l/?s={urllib.parse.quote(tick)}&f=sd2t2ohlcv&h&e=csv"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"Stooq empty for {symbol}")
    # header: Symbol,Date,Time,Open,High,Low,Close,Volume
    parts = lines[1].split(",")
    if len(parts) < 7:
        raise RuntimeError(f"Stooq parse failed for {symbol}: {lines[1]}")
    close = float(parts[6])
    if not math.isfinite(close) or close <= 0:
        raise RuntimeError(f"Stooq non-positive close for {symbol}")
    asof = datetime.utcnow()
    try:
        asof = datetime.strptime(parts[1] + " " + parts[2], "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return close, asof


def _spot_with_failover(symbol: str, timeout: float = 15.0
                        ) -> tuple[float, datetime, tuple[str, ...]]:
    used: list[str] = []
    try:
        px, asof, host = _chart_last(symbol, timeout=timeout)
        used.append(f"yahoo-chart:{host}")
        return px, asof, tuple(used)
    except RuntimeError as primary:
        logger.warning("Yahoo chart failed for %s: %s", symbol, primary)
        try:
            px, asof = _stooq_last(symbol, timeout=timeout)
            used.append("stooq")
            return px, asof, tuple(used)
        except Exception as secondary:
            raise RuntimeError(
                f"All spot sources failed for {symbol}: yahoo=({primary}); "
                f"stooq=({secondary})"
            ) from secondary


def fetch_live_snapshot(underlying: str = "SPX",
                        timeout: float = 15.0) -> LiveSnapshot:
    """Fetch a live snapshot for SPX (cash), SPY, or ES (futures)."""
    u = underlying.strip().upper()
    if u in {"ES", "ES=F", "FUTURES"}:
        spot_sym = "ES=F"
        div = None
        div_src = None
        label = "ES=F"
    elif u == "SPY":
        spot_sym = "SPY"
        div = 0.013
        div_src = "ETF continuous-yield proxy 1.30% (override in UI)"
        label = "SPY"
    else:
        spot_sym = "^GSPC"
        div = 0.013
        div_src = "SPX continuous-yield proxy 1.30% (override in UI)"
        label = "^GSPC (SPX)"

    spot, asof, spot_sources = _spot_with_failover(spot_sym, timeout=timeout)

    rate = None
    rate_src = None
    try:
        irx, _, src = _chart_last("^IRX", timeout=timeout)
        rate = irx / 100.0
        rate_src = f"^IRX (13-week T-bill) via {src}"
    except RuntimeError as exc:
        logger.warning("IRX fetch failed: %s", exc)
        try:
            tnx, _, src = _chart_last("^TNX", timeout=timeout)
            # 10Y is a coarser proxy — label clearly.
            rate = tnx / 100.0
            rate_src = f"^TNX (10Y) fallback via {src} — prefer IRX when available"
        except RuntimeError as exc2:
            logger.warning("TNX fallback failed: %s", exc2)

    vix = None
    try:
        vix_pts, _, _ = _chart_last("^VIX", timeout=timeout)
        vix = vix_pts / 100.0
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
        source=f"Live multi-source ({'+'.join(spot_sources) or 'yahoo'}) · {label}",
        spot_sources=spot_sources,
    )


def _option_yahoo_symbol(underlying: str) -> str:
    u = underlying.strip().upper()
    if u in {"ES", "ES=F"}:
        # CME ES options are not on Yahoo; SPX cash options are the liquid
        # index-vol surface used as an explicit proxy (labeled in UI).
        return "^SPX"
    return _OPTION_SYMBOL.get(u, "^SPX")


def list_option_expiries(underlying: str = "SPX",
                         timeout: float = 20.0,
                         asof: datetime | None = None) -> list[datetime]:
    """List available option expiries (UTC midnight unix → naive UTC datetime).

    Past-dated expiries relative to ``asof`` (default: UTC now) are dropped.
    """
    sym = _option_yahoo_symbol(underlying)
    session = _get_session(timeout=timeout)
    url = ("https://query2.finance.yahoo.com/v7/finance/options/"
           + urllib.parse.quote(sym))
    if session.crumb:
        url += f"?crumb={urllib.parse.quote(session.crumb)}"
    payload = _http_json(url, timeout=timeout, use_session=True)
    result = (payload.get("optionChain") or {}).get("result") or []
    if not result:
        err = (payload.get("optionChain") or {}).get("error")
        raise RuntimeError(f"No option chain metadata for {sym}: {err}")
    stamps = result[0].get("expirationDates") or []
    expiries = [
        datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
        for ts in stamps
    ]
    cutoff = asof or datetime.now(timezone.utc).replace(tzinfo=None)
    # Keep expiry if calendar date >= asof date (same-day options still valid).
    future = [e for e in expiries if e.date() >= cutoff.date()]
    return future or expiries


def _contract_row(c: dict[str, Any], side: str) -> dict[str, Any]:
    return {
        "strike": c.get("strike"),
        f"{side}_bid": c.get("bid"),
        f"{side}_ask": c.get("ask"),
        f"{side}_last": c.get("lastPrice"),
        f"{side}_volume": c.get("volume"),
        f"{side}_open_interest": c.get("openInterest"),
        f"{side}_yahoo_iv": c.get("impliedVolatility"),
    }


def fetch_option_chain(
    underlying: str = "SPX",
    expiry: datetime | None = None,
    timeout: float = 25.0,
) -> LiveOptionChain:
    """Fetch a real option chain with bid/ask/last (no fabricated quotes).

    For ES underliers, Yahoo has no ES options — ``^SPX`` chain is returned
    and ``source`` is labeled as an explicit SPX proxy surface.
    """
    u = underlying.strip().upper()
    sym = _option_yahoo_symbol(u)
    session = _get_session(timeout=timeout)

    if expiry is None:
        # Default to the nearest *future* listed expiry.
        future = list_option_expiries(u, timeout=timeout)
        if not future:
            raise RuntimeError(f"No listed expiries for {sym}")
        expiry = future[0]

    base = ("https://query2.finance.yahoo.com/v7/finance/options/"
            + urllib.parse.quote(sym))
    params: dict[str, str] = {}
    if session.crumb:
        params["crumb"] = session.crumb
    # Yahoo expects unix seconds at UTC midnight of the expiry date.
    exp_utc = datetime(expiry.year, expiry.month, expiry.day,
                       tzinfo=timezone.utc)
    params["date"] = str(int(exp_utc.timestamp()))
    url = base + ("?" + urllib.parse.urlencode(params))

    try:
        payload = _http_json(url, timeout=timeout, use_session=True)
    except Exception:
        session = _get_session(timeout=timeout, force=True)
        if session.crumb:
            params["crumb"] = session.crumb
        url = base + ("?" + urllib.parse.urlencode(params))
        payload = _http_json(url, timeout=timeout, use_session=True)

    result = (payload.get("optionChain") or {}).get("result") or []
    if not result:
        err = (payload.get("optionChain") or {}).get("error")
        raise RuntimeError(f"Option chain empty for {sym}: {err}")
    block = result[0]
    quote = block.get("quote") or {}
    spot = quote.get("regularMarketPrice") or quote.get("previousClose")
    if spot is None:
        raise RuntimeError(f"Option chain for {sym} missing underlier quote")
    stamps = block.get("expirationDates") or []
    expiries = tuple(
        datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
        for ts in stamps
    )
    options = block.get("options") or []
    if not options:
        raise RuntimeError(
            f"No option contracts returned for {sym} "
            f"(expiry={expiry.date() if expiry else 'nearest'})."
        )
    slice_ = options[0]
    exp_ts = slice_.get("expirationDate")
    exp_dt = (
        datetime.fromtimestamp(int(exp_ts), tz=timezone.utc).replace(tzinfo=None)
        if exp_ts else expiry
    )
    calls = {_contract_row(c, "call")["strike"]: _contract_row(c, "call")
             for c in (slice_.get("calls") or [])
             if c.get("strike") is not None}
    puts = {_contract_row(p, "put")["strike"]: _contract_row(p, "put")
            for p in (slice_.get("puts") or [])
            if p.get("strike") is not None}
    strikes = sorted(set(calls) | set(puts))
    rows = []
    for k in strikes:
        row: dict[str, Any] = {"strike": float(k)}
        row.update(calls.get(k, {}))
        row.update(puts.get(k, {}))
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"Parsed zero strikes for {sym}")

    asof_ts = quote.get("regularMarketTime")
    asof = (datetime.fromtimestamp(asof_ts, tz=timezone.utc).replace(tzinfo=None)
            if asof_ts else datetime.utcnow())

    proxy_note = ""
    if u in {"ES", "ES=F"}:
        proxy_note = " · SPX options used as vol-surface proxy for ES spot"

    return LiveOptionChain(
        underlying=u,
        yahoo_symbol=sym,
        spot=float(spot),
        expiry=exp_dt,
        asof=asof,
        frame=frame,
        source=f"Yahoo options API ({sym}){proxy_note}",
        expiries_available=expiries,
    )


def fetch_multi_expiry_surface(
    underlying: str = "SPX",
    max_expiries: int = 6,
    timeout: float = 25.0,
) -> list[LiveOptionChain]:
    """Fetch several near-dated expiries for a volatility surface."""
    expiries = list_option_expiries(underlying, timeout=timeout)
    if not expiries:
        raise RuntimeError("No option expiries available")
    out: list[LiveOptionChain] = []
    for exp in expiries[: max(1, int(max_expiries))]:
        try:
            out.append(fetch_option_chain(underlying, expiry=exp, timeout=timeout))
        except RuntimeError as exc:
            logger.warning("Skip expiry %s: %s", exp.date(), exc)
    if not out:
        raise RuntimeError("Failed to fetch any option expiry slices")
    return out
