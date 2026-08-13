"""Refresh sample_data/live_cache.json for GitHub Pages (browser CORS-safe).

Yahoo Finance blocks cross-origin XHR from github.io / Pyodide. The hosted
stlite app therefore loads this JSON cache instead of calling Yahoo directly.
Run locally or via GitHub Actions — never fabricates quotes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.yahoo_live import (  # noqa: E402
    fetch_live_snapshot,
    fetch_option_chain,
    list_option_expiries,
)

KEEP_COLS = [
    "strike",
    "call_bid", "call_ask", "call_last", "call_yahoo_iv",
    "put_bid", "put_ask", "put_last", "put_yahoo_iv",
]


def _snapshot_dict(snap) -> dict:
    return {
        "symbol": snap.symbol,
        "spot": snap.spot,
        "asof": snap.asof.isoformat(sep=" ", timespec="seconds"),
        "risk_free_rate": snap.risk_free_rate,
        "rate_source": snap.rate_source,
        "dividend_yield": snap.dividend_yield,
        "dividend_source": snap.dividend_source,
        "vix": snap.vix,
        "source": snap.source,
        "spot_sources": list(snap.spot_sources),
    }


def _trim_frame(frame: pd.DataFrame, spot: float,
                moneyness_band: float = 0.12) -> pd.DataFrame:
    """Keep liquid-ish strikes near spot to keep the Pages cache small."""
    f = frame.copy()
    f["strike"] = pd.to_numeric(f["strike"], errors="coerce")
    f = f.dropna(subset=["strike"])
    lo, hi = spot * (1 - moneyness_band), spot * (1 + moneyness_band)
    band = f[(f["strike"] >= lo) & (f["strike"] <= hi)]
    if len(band) < 15:
        # Fallback: nearest 40 strikes to spot
        f = f.assign(_d=(f["strike"] - spot).abs()).sort_values("_d").head(40)
        f = f.drop(columns="_d").sort_values("strike")
        return f
    return band.sort_values("strike")


def _chain_dict(chain, moneyness_band: float = 0.12) -> dict:
    frame = _trim_frame(chain.frame, chain.spot, moneyness_band)
    cols = [c for c in KEEP_COLS if c in frame.columns]
    rows = json.loads(frame[cols].to_json(orient="records"))
    return {
        "underlying": chain.underlying,
        "yahoo_symbol": chain.yahoo_symbol,
        "spot": chain.spot,
        "expiry": chain.expiry.isoformat(sep=" ", timespec="seconds"),
        "asof": chain.asof.isoformat(sep=" ", timespec="seconds"),
        "source": chain.source,
        "rows": rows,
    }


def _pick_expiry(underlying: str, dte_days: float = 30.0) -> datetime:
    expiries = list_option_expiries(underlying)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    future = [e for e in expiries if e.date() >= now.date()] or expiries
    target = now + timedelta(days=dte_days)
    return min(future, key=lambda e: abs((e - target).total_seconds()))


def _pick_surface_expiries(underlying: str, n: int = 3) -> list[datetime]:
    expiries = list_option_expiries(underlying)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    future = [e for e in expiries if e.date() >= now.date()] or expiries
    # Prefer weekly-ish spacing: take first n future expiries with gaps
    picked: list[datetime] = []
    for e in future:
        if not picked or (e - picked[-1]).days >= 2:
            picked.append(e)
        if len(picked) >= n:
            break
    return picked or future[:n]


def main() -> int:
    out = ROOT / "sample_data" / "live_cache.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(tzinfo=None)
        .isoformat(sep=" ", timespec="seconds") + "Z",
        "note": (
            "Cached live Yahoo snapshot for GitHub Pages / stlite. "
            "Browsers cannot call Yahoo directly (CORS). Local Streamlit "
            "still fetches live."
        ),
        "snapshots": {},
        "option_chains": {},
        "surface_chains": {},
    }

    for under in ("SPX", "SPY", "ES"):
        try:
            snap = fetch_live_snapshot(under)
            payload["snapshots"][under] = _snapshot_dict(snap)
            print(f"snapshot {under}: spot={snap.spot}")
        except Exception as exc:
            print(f"snapshot {under} FAILED: {exc}")

    for under in ("SPX", "SPY"):
        try:
            exp = _pick_expiry(under, 30)
            chain = fetch_option_chain(under, expiry=exp)
            payload["option_chains"][under] = _chain_dict(chain)
            print(f"chain {under}: n={len(payload['option_chains'][under]['rows'])} "
                  f"exp={chain.expiry.date()}")
        except Exception as exc:
            print(f"chain {under} FAILED: {exc}")

        try:
            slices = []
            for exp in _pick_surface_expiries(under, 3):
                c = fetch_option_chain(under, expiry=exp)
                slices.append(_chain_dict(c, moneyness_band=0.08))
            payload["surface_chains"][under] = slices
            print(f"surface {under}: {len(slices)} expiries")
        except Exception as exc:
            print(f"surface {under} FAILED: {exc}")

    if "SPX" in payload["option_chains"] and "ES" not in payload["option_chains"]:
        proxy = dict(payload["option_chains"]["SPX"])
        proxy["underlying"] = "ES"
        proxy["source"] = str(proxy.get("source", "")) + " · SPX proxy for ES (cached)"
        payload["option_chains"]["ES"] = proxy
        if "SPX" in payload.get("surface_chains", {}):
            payload["surface_chains"]["ES"] = payload["surface_chains"]["SPX"]

    text = json.dumps(payload, indent=2)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
