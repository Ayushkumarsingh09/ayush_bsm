"""Option-chain presentation: display-unit scaling and professional styling.

Raw-unit analytics from ``analytics.chain`` are converted to trader-facing
display units here (per-day theta, per-vol-point vega, ...) using the
central convention configuration, then arranged in the classic
``CALL side | STRIKE | PUT side`` layout with the ATM row highlighted.

Premium columns are labeled **BSM Premium** (theoretical model value).
This application does not ingest market bid/ask/mid/last; never confuse
BSM Premium with a traded market premium.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.chain import ChainMeta
from pricing.conventions import GreekConventions
from pricing.registry import GREEK_REGISTRY

# Greek keys per toggleable column group, in display order (call side).
GROUP_GREEKS: dict[str, list[str]] = {
    "First Order": ["delta", "vega", "theta", "rho"],
    "Second Order": ["gamma", "vanna", "volga"],
    "Third Order+": ["charm", "speed", "zomma", "color", "ultima"],
}

# Canonical display label for theoretical premium (Case A: pure BSM).
BSM_PREMIUM_LABEL = "BSM Premium"


def scaled_greek(df: pd.DataFrame, key: str, side: str,
                 conventions: GreekConventions) -> pd.Series:
    """Return a Greek column in display units for 'call' or 'put' side."""
    spec = next(g for g in GREEK_REGISTRY if g.key == key)
    col = f"{side}_{key}" if spec.per_side else key
    return df[col] * conventions.factor_for(spec.display_transform)


def build_display_frame(df: pd.DataFrame, meta: ChainMeta,
                        conventions: GreekConventions,
                        groups: list[str]) -> pd.DataFrame:
    """Assemble the CALL | STRIKE | PUT display table in display units.

    Layout (mirrored about the strike):

        CALL: Status, Greeks..., BSM Premium | Strike | BSM Premium, Greeks...(reversed), Status
    """
    call_ordered: list[tuple[tuple[str, str], pd.Series]] = []
    put_ordered: list[tuple[tuple[str, str], pd.Series]] = []

    call_ordered.append((("CALL", "Status"), df["call_status"]))

    greek_labels: list[tuple[str, pd.Series, pd.Series]] = []
    for group in ["First Order", "Second Order", "Third Order+"]:
        if group not in groups:
            continue
        for key in GROUP_GREEKS[group]:
            spec = next(g for g in GREEK_REGISTRY if g.key == key)
            greek_labels.append((
                spec.label,
                scaled_greek(df, key, "call", conventions),
                scaled_greek(df, key, "put", conventions),
            ))

    for label, call_s, _ in greek_labels:
        call_ordered.append((("CALL", label), call_s))

    # Premium sits next to the strike on both sides. Optional market mid /
    # mispricing appear immediately outside the theoretical premium.
    if "call_market_mid" in df.columns and df["call_market_mid"].notna().any():
        call_ordered.append((("CALL", "Market Mid"), df["call_market_mid"]))
        call_ordered.append((("CALL", "Mispricing"), df["call_mispricing"]))
    call_ordered.append((("CALL", BSM_PREMIUM_LABEL), df["call_bsm_premium"]))
    put_ordered.append((("PUT", BSM_PREMIUM_LABEL), df["put_bsm_premium"]))
    if "put_market_mid" in df.columns and df["put_market_mid"].notna().any():
        put_ordered.append((("PUT", "Mispricing"), df["put_mispricing"]))
        put_ordered.append((("PUT", "Market Mid"), df["put_market_mid"]))

    # Put Greeks reversed so the table mirrors about strike.
    for label, _, put_s in reversed(greek_labels):
        put_ordered.append((("PUT", label), put_s))

    put_ordered.append((("PUT", "Status"), df["put_status"]))

    center = {
        ("STRIKE", "Strike"): df["strike"],
        ("STRIKE", "Moneyness S/K"): df["moneyness"],
    }

    out = pd.DataFrame({
        **dict(call_ordered),
        **center,
        **dict(put_ordered),
    })
    out.columns = pd.MultiIndex.from_tuples(out.columns)
    out.index = df.index
    return out


def style_chain(display_df: pd.DataFrame, df_raw: pd.DataFrame,
                decimals: int) -> "pd.io.formats.style.Styler":
    """Apply ATM highlighting, ITM shading and NaN-as-N/A formatting."""
    atm_mask = df_raw["is_atm"].to_numpy()

    def row_style(row: pd.Series) -> list[str]:
        i = display_df.index.get_loc(row.name)
        if atm_mask[i]:
            return ["background-color: rgba(255, 196, 0, 0.22); "
                    "font-weight: 600"] * len(row)
        styles = []
        for col in display_df.columns:
            side = col[0]
            if side == "CALL" and df_raw["call_status"].iloc[i] == "ITM":
                styles.append("background-color: rgba(38, 166, 91, 0.10)")
            elif side == "PUT" and df_raw["put_status"].iloc[i] == "ITM":
                styles.append("background-color: rgba(38, 166, 91, 0.10)")
            else:
                styles.append("")
        return styles

    numeric_cols = [c for c in display_df.columns
                    if display_df[c].dtype.kind == "f"]
    fmt = {c: (lambda v, d=decimals:
               "N/A" if pd.isna(v) else f"{v:,.{d}f}") for c in numeric_cols}
    fmt[("STRIKE", "Strike")] = lambda v: f"{v:,.0f}"

    styler = display_df.style.format(fmt).apply(row_style, axis=1)
    styler = styler.set_properties(
        subset=[("STRIKE", "Strike")],
        **{"font-weight": "700", "text-align": "center"},
    )
    return styler


def filter_chain(df: pd.DataFrame, strike_min: float, strike_max: float,
                 status_filter: str) -> pd.DataFrame:
    """Filter raw chain rows by strike range and moneyness status."""
    out = df[(df["strike"] >= strike_min) & (df["strike"] <= strike_max)]
    if status_filter == "ITM calls":
        out = out[out["call_status"].isin(["ITM", "ATM"])]
    elif status_filter == "ITM puts":
        out = out[out["put_status"].isin(["ITM", "ATM"])]
    elif status_filter == "Near ATM (\u00b110 strikes)":
        atm_idx = np.flatnonzero(out["is_atm"].to_numpy())
        if len(atm_idx):
            i = atm_idx[0]
            out = out.iloc[max(0, i - 10): i + 11]
    return out
