"""Normalize the proprietary ``BSM inputs.xlsx`` workbook layout.

That workbook is *not* a flat market-inputs table. It looks like:

```text
                Underlying spot     7500
                Risk free Rate      0.0365

Contract | Current date and time | Expiry date and time | Strike | Implied Volatility
ES Sep26 | 2026-08-12 16:00:00   | 2026-09-10 18:00:00  | 8200   | 0.13
...
```

This module detects that layout and returns a tidy per-strike DataFrame
with columns the rest of the pipeline understands.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


NORMALIZED_COLUMNS = [
    "spot",
    "risk_free_rate",
    "dividend_yield",
    "asof",
    "expiry",
    "strike",
    "volatility",
    "contract",
]


def _cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return "".join(c if c.isalnum() else "_" for c in _cell_str(value).lower())


def _find_labeled_number(raw: pd.DataFrame, *needles: str) -> float | None:
    """Find a numeric value in the cell to the right of a label matching needles."""
    for i in range(len(raw)):
        for j in range(raw.shape[1]):
            label = _norm(raw.iat[i, j])
            if not label:
                continue
            if all(n in label for n in needles):
                for k in range(j + 1, raw.shape[1]):
                    val = raw.iat[i, k]
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        continue
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        continue
    return None


def _find_header_row(raw: pd.DataFrame) -> int | None:
    for i in range(len(raw)):
        norms = [_norm(raw.iat[i, j]) for j in range(raw.shape[1])]
        has_strike = any(n == "strike" or n.endswith("_strike") for n in norms)
        has_vol = any(
            "implied" in n and "vol" in n or n in {"iv", "volatility", "vol", "sigma"}
            for n in norms
        )
        has_expiry = any("expiry" in n or "expiration" in n for n in norms)
        if has_strike and has_vol and has_expiry:
            return i
    return None


def _col_index(headers: list[str], *candidates: str) -> int | None:
    norms = [_norm(h) for h in headers]
    for cand in candidates:
        cn = _norm(cand)
        for i, n in enumerate(norms):
            if n == cn or cn in n:
                return i
    return None


def try_normalize_bsm_chain_workbook(
    raw: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]] | None:
    """Return (normalized DataFrame, notes) if ``raw`` matches the workbook layout.

    ``raw`` must be read with ``header=None`` (positional columns).
    Returns ``None`` when the sheet is a normal flat table.
    """
    if raw is None or raw.empty or raw.shape[1] < 4:
        return None

    spot = _find_labeled_number(raw, "underlying", "spot")
    if spot is None:
        spot = _find_labeled_number(raw, "spot")
    rate = _find_labeled_number(raw, "risk", "free", "rate")
    if rate is None:
        rate = _find_labeled_number(raw, "risk", "free")
    header_i = _find_header_row(raw)
    if spot is None or rate is None or header_i is None:
        return None

    headers = [_cell_str(raw.iat[header_i, j]) or f"col{j}"
               for j in range(raw.shape[1])]
    i_strike = _col_index(headers, "strike")
    i_vol = _col_index(headers, "implied volatility", "implied_vol",
                       "volatility", "iv", "sigma")
    i_expiry = _col_index(headers, "expiry date and time", "expiry",
                          "expiration", "expiry date")
    i_asof = _col_index(headers, "current date and time", "current date",
                        "asof", "valuation date", "trade date")
    i_contract = _col_index(headers, "contract")
    if i_strike is None or i_vol is None or i_expiry is None:
        return None

    rows: list[dict[str, Any]] = []
    for i in range(header_i + 1, len(raw)):
        strike_raw = raw.iat[i, i_strike]
        vol_raw = raw.iat[i, i_vol]
        expiry_raw = raw.iat[i, i_expiry]
        if (pd.isna(strike_raw) or pd.isna(vol_raw) or pd.isna(expiry_raw)):
            continue
        try:
            strike = float(strike_raw)
            vol = float(vol_raw)
        except (TypeError, ValueError):
            continue
        if strike <= 0 or vol < 0:
            continue
        asof = raw.iat[i, i_asof] if i_asof is not None else pd.NaT
        contract = (raw.iat[i, i_contract]
                    if i_contract is not None else None)
        rows.append({
            "spot": spot,
            "risk_free_rate": rate,
            "dividend_yield": pd.NA,  # missing → provider assumes q=0
            "asof": asof,
            "expiry": expiry_raw,
            "strike": strike,
            "volatility": vol,
            "contract": contract,
        })

    if len(rows) < 2:
        return None

    out = pd.DataFrame(rows, columns=NORMALIZED_COLUMNS)
    # Sort ascending by strike for a conventional chain view.
    out = out.sort_values("strike").reset_index(drop=True)
    notes = [
        f"Detected BSM chain workbook layout: spot={spot:g}, "
        f"r={rate:g}, {len(out)} strike/IV rows.",
        "Per-strike implied volatilities from the file will be used "
        "(not a single flat sigma).",
        "Valuation time (T) uses the file's Current date and time column "
        "when present.",
        "No dividend yield in workbook: q = 0% will be assumed.",
    ]
    return out, notes
