"""Self-explanatory CSV / Excel export of the option chain.

Exports include the full input set, timestamp and Greek conventions so the
file can be understood on its own. Greek columns are exported in *display
units* with unit-suffixed names (e.g. ``call_theta_per_day``).
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from analytics.chain import ChainMeta
from pricing.conventions import GreekConventions
from pricing.registry import GREEK_REGISTRY
from visualization.option_chain import scaled_greek

_UNIT_SUFFIX = {
    "none": "",
    "per_pct": "_per_1pct",
    "per_day": "_per_day",
    "per_pct2": "_per_1pct_sq",
    "per_pct3": "_per_1pct_cu",
}


def build_export_frame(df: pd.DataFrame,
                       conventions: GreekConventions) -> pd.DataFrame:
    out = pd.DataFrame({
        "strike": df["strike"],
        "moneyness_s_over_k": df["moneyness"],
        "sigma": df["sigma"],
        "call_status": df["call_status"],
        "put_status": df["put_status"],
        # Explicit theoretical premium names — not market bid/ask/mid/last.
        "call_bsm_premium": df["call_bsm_premium"],
        "put_bsm_premium": df["put_bsm_premium"],
        "parity_error": df["parity_error"],
        "is_atm": df["is_atm"],
    })
    for spec in GREEK_REGISTRY:
        suffix = _UNIT_SUFFIX[spec.display_transform]
        if spec.per_side:
            out[f"call_{spec.key}{suffix}"] = scaled_greek(
                df, spec.key, "call", conventions)
            out[f"put_{spec.key}{suffix}"] = scaled_greek(
                df, spec.key, "put", conventions)
        else:
            out[f"{spec.key}{suffix}"] = scaled_greek(
                df, spec.key, "call", conventions)
    return out


def _metadata_rows(meta: ChainMeta, inputs: dict,
                   conventions: GreekConventions) -> list[tuple[str, str]]:
    return [
        ("model", "Black-Scholes-Merton (European, continuous dividend yield)"),
        ("model_version", "bsm-dashboard-1.1"),
        ("underlying", "SPX (theoretical BSM premiums, not market quotes)"),
        ("premium_definition", "call_bsm_premium / put_bsm_premium = BSM "
                               "theoretical fair value in index points"),
        ("spx_multiplier_note", "Listed SPX options have a $100 multiplier; "
                                "values here are in index points (not dollars)"),
        ("exported_at", datetime.now().isoformat(timespec="seconds")),
        ("spot", f"{meta.spot}"),
        ("volatility", f"{inputs['sigma']:.6f} ({inputs['sigma'] * 100:.4f}%)"),
        ("risk_free_rate", f"{inputs['r']:.6f} ({inputs['r'] * 100:.4f}%)"),
        ("dividend_yield", f"{inputs['q']:.6f} ({inputs['q'] * 100:.4f}%)"),
        ("expiry", meta.expiry.isoformat(timespec="minutes")),
        ("expiry_timezone_note", "Expiry timestamps are interpreted in the "
                                 "clock used at calculation time; SPX "
                                 "settlement is America/New_York"),
        ("time_to_expiry_years", f"{meta.T:.10f}"),
        ("day_count", inputs.get("day_count", "ACT/365")),
        ("dte_days", f"{meta.dte_days:.4f}"),
        ("atm_strike", f"{meta.atm_strike}"),
        ("n_strikes", f"{meta.n_strikes}"),
        ("data_source", inputs.get("source", "Manual")),
        ("theta_convention", f"per calendar day (annual / {conventions.days_per_year:.0f})"),
        ("vega_convention", "per +1 volatility percentage point (raw x 0.01)"),
        ("rho_convention", "per +1 rate percentage point (raw x 0.01)"),
        ("charm_color_convention", "calendar-time decay per day"),
        ("disclaimer", "BSM Premium columns are theoretical model outputs and "
                       "are not market bid/ask/mid/last or executable prices."),
    ]


def to_csv_bytes(df: pd.DataFrame, meta: ChainMeta, inputs: dict,
                 conventions: GreekConventions) -> bytes:
    """CSV with a '#'-commented metadata header followed by the chain."""
    export = build_export_frame(df, conventions)
    buf = io.StringIO()
    for key, value in _metadata_rows(meta, inputs, conventions):
        buf.write(f"# {key}: {value}\n")
    export.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def to_excel_bytes(df: pd.DataFrame, meta: ChainMeta, inputs: dict,
                   conventions: GreekConventions) -> bytes:
    """Excel workbook: 'Option Chain' sheet + 'Inputs & Conventions' sheet."""
    export = build_export_frame(df, conventions)
    meta_df = pd.DataFrame(_metadata_rows(meta, inputs, conventions),
                           columns=["Field", "Value"])
    buf = io.BytesIO()
    try:
        engine = "xlsxwriter"
        import xlsxwriter  # noqa: F401
    except ImportError:
        try:
            import openpyxl  # noqa: F401
            engine = "openpyxl"
        except ImportError as exc:
            raise RuntimeError(
                "Excel export requires xlsxwriter or openpyxl. "
                "CSV download still works."
            ) from exc
    with pd.ExcelWriter(buf, engine=engine) as writer:
        export.to_excel(writer, sheet_name="Option Chain", index=False)
        meta_df.to_excel(writer, sheet_name="Inputs & Conventions", index=False)
        if engine == "xlsxwriter":
            wb = writer.book
            ws = writer.sheets["Option Chain"]
            num_fmt = wb.add_format({"num_format": "#,##0.000000"})
            ws.set_column(0, len(export.columns) - 1, 16, num_fmt)
            writer.sheets["Inputs & Conventions"].set_column(0, 1, 40)
    return buf.getvalue()
