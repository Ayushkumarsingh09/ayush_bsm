"""SPX BSM Options Analytics dashboard (Streamlit entry point).

UI orchestration only -- all mathematics lives in ``pricing``/``analytics``,
data ingestion in ``data``, validation in ``validation`` and presentation
helpers in ``visualization``/``utils``.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
import streamlit as st

from dataclasses import dataclass

from analytics.chain import (ChainConfig, StrikeGridError, build_option_chain,
                             detect_atm_strike)
from config import settings
from data.base import DataProviderError, MarketInputs
from data.csv_provider import (CSVDataProvider, EXCEL_EXTENSIONS,
                               detect_columns, parse_tabular)
from data.manual import ManualDataProvider
from analytics.scenarios import DEFAULT_SCENARIOS, run_scenarios
from data.market_quotes import (attach_market_columns, enrich_market_ivs,
                                summarize_mispricing)
from pricing.conventions import DEFAULT_CONVENTIONS
from pricing.models import resolve_model
from pricing.registry import GREEK_GROUPS, GREEK_REGISTRY, GREEKS_BY_KEY
from pricing.volatility import ConstantVolatility, PerStrikeVolatility
from utils import export as export_utils
from utils.dates import DayCount, time_to_expiry
from utils.formatting import fmt_number, fmt_pct
from validation.validators import (ValidationResult, validate_chain_config,
                                   validate_market_inputs)
from visualization import charts
from visualization.option_chain import (GROUP_GREEKS, build_display_frame,
                                        filter_chain, style_chain)


@dataclass
class SidebarMarketState:
    """Market inputs plus optional per-strike chain from an uploaded workbook."""

    market_inputs: MarketInputs
    use_file_strikes: bool = False
    file_strikes: list[float] | None = None
    file_sigmas: list[float] | None = None
    contract: str | None = None
    market_table: pd.DataFrame | None = None
    interpretations: list[str] | None = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spx_bsm_dashboard")

st.set_page_config(page_title=settings.APP_TITLE, page_icon="chart_with_upwards_trend",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.6rem; padding-bottom: 2rem; }
      div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 8px; padding: 10px 14px;
      }
      div[data-testid="stMetricLabel"] { font-size: 0.78rem; opacity: 0.75; }
      .disclaimer {
        font-size: 0.8rem; opacity: 0.75; border-left: 3px solid #ffc400;
        padding: 6px 12px; margin: 6px 0 14px 0;
        background: rgba(255,196,0,0.06); border-radius: 4px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Cached computation
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=64)
def compute_chain(spot: float, sigma: float, r: float, q: float, T: float,
                  interval: float, each_side: int, atm_method: str,
                  explicit_atm: float | None, expiry_iso: str,
                  file_strikes: tuple[float, ...] | None = None,
                  file_sigmas: tuple[float, ...] | None = None,
                  asof_iso: str | None = None,
                  model_label: str = "BSM (equity index)"):
    import numpy as np
    cfg = ChainConfig(strike_interval=interval, strikes_each_side=each_side,
                      atm_method=atm_method, explicit_atm=explicit_atm)
    expiry = datetime.fromisoformat(expiry_iso)
    asof = datetime.fromisoformat(asof_iso) if asof_iso else None
    if file_strikes is not None and file_sigmas is not None:
        strikes = np.asarray(file_strikes, dtype=float)
        vol_provider = PerStrikeVolatility(
            strikes, np.asarray(file_sigmas, dtype=float))
        df, meta = build_option_chain(
            spot=spot, T=T, r=r, q=q, vol_provider=vol_provider, cfg=cfg,
            expiry=expiry, strikes=strikes, asof=asof,
            model_label=model_label,
        )
    else:
        df, meta = build_option_chain(
            spot=spot, T=T, r=r, q=q,
            vol_provider=ConstantVolatility(sigma),
            cfg=cfg, expiry=expiry, asof=asof,
            model_label=model_label,
        )
    return df, meta


# ---------------------------------------------------------------------------
# Sidebar: data source + inputs
# ---------------------------------------------------------------------------
def sidebar_market_inputs() -> SidebarMarketState | None:
    """Render the Data Source section; return market state or None."""
    st.sidebar.header("Data Source")
    mode = st.sidebar.radio(
        "Source", ["Manual", "CSV / Excel", "Live API"],
        horizontal=True, label_visibility="collapsed")

    if mode == "Live API":
        return sidebar_live_inputs()

    if mode == "CSV / Excel":
        return sidebar_file_inputs()
    mi = sidebar_manual_inputs()
    return SidebarMarketState(market_inputs=mi)


def sidebar_live_inputs() -> SidebarMarketState | None:
    from data.live_provider import LiveMarketDataProvider

    st.sidebar.subheader("Live underlier")
    underlying = st.sidebar.selectbox(
        "Underlying", ["SPX", "ES"],
        help="SPX uses ^GSPC spot + dividend proxy; ES uses ES=F futures "
             "spot and Black-76 (q = r).")
    dte = st.sidebar.number_input(
        "Target DTE (days) for synthetic expiry", min_value=1.0,
        max_value=365.0, value=30.0, step=1.0)
    fetch = st.sidebar.button("FETCH LIVE SNAPSHOT", type="secondary",
                              width="stretch")
    if not fetch and "live_state" not in st.session_state:
        st.sidebar.info(
            "Fetches live spot (^GSPC or ES=F), ^IRX rate and VIX vol proxy "
            "via Yahoo chart API. Option bid/ask chains are not fabricated "
            "when unavailable.")
        return None
    if fetch or "live_state" in st.session_state:
        try:
            provider = LiveMarketDataProvider(
                underlying=underlying, dte_days=float(dte))
            mi = provider.get_market_inputs()
        except DataProviderError as exc:
            st.sidebar.error(str(exc))
            return None
        for note in provider.interpretations:
            st.sidebar.caption(f"ℹ️ {note}")
        state = SidebarMarketState(
            market_inputs=mi,
            contract="ES Sep" if underlying == "ES" else "SPX",
            interpretations=list(provider.interpretations),
        )
        st.session_state["live_state"] = state
        return state
    return None


def _expiry_widget(key_prefix: str) -> datetime:
    st.sidebar.subheader("Contract Inputs")
    col1, col2 = st.sidebar.columns(2)
    exp_date = col1.date_input("Expiry date", value=date.today() + timedelta(days=30),
                               key=f"{key_prefix}_exp_date")
    exp_time = col2.time_input("Expiry time", value=dtime(16, 0),
                               key=f"{key_prefix}_exp_time",
                               help="SPX weeklies settle 4:00 pm America/New_York; "
                                    "AM-settled monthlies use the 9:30 am ET opening "
                                    "print. Enter the clock time in the same zone "
                                    "you intend (prefer ET for SPX). Naive timestamps "
                                    "are compared without conversion.")
    return datetime.combine(exp_date, exp_time)


def sidebar_manual_inputs() -> MarketInputs:
    st.sidebar.subheader("Market Inputs")
    spot = st.sidebar.number_input("Spot price (S)", min_value=0.01,
                                   value=settings.DEFAULT_SPOT, step=1.0,
                                   format="%.2f")
    vol_pct = st.sidebar.number_input(
        "Volatility \u03c3 (%)", min_value=0.0, max_value=500.0,
        value=settings.DEFAULT_VOLATILITY * 100, step=0.25, format="%.2f",
        help="Enter as a percentage: 18.50 means \u03c3 = 0.185.")
    rate_pct = st.sidebar.number_input(
        "Risk-free rate r (%)", min_value=-100.0, max_value=100.0,
        value=settings.DEFAULT_RISK_FREE_RATE * 100, step=0.05, format="%.2f")
    no_div = st.sidebar.checkbox(
        "No dividend input (assume q = 0%)", value=False,
        help="BSM for SPX should use a continuous dividend yield. Only check "
             "this if you deliberately want the q = 0 fallback.")
    div_pct = st.sidebar.number_input(
        "Dividend yield q (%)", min_value=-50.0, max_value=100.0,
        value=settings.DEFAULT_DIVIDEND_YIELD * 100, step=0.05, format="%.2f",
        disabled=no_div)

    expiry = _expiry_widget("manual")
    provider = ManualDataProvider(
        spot=spot, volatility=vol_pct / 100.0, risk_free_rate=rate_pct / 100.0,
        dividend_yield=None if no_div else div_pct / 100.0, expiry=expiry)
    return provider.get_market_inputs()


def sidebar_file_inputs() -> SidebarMarketState | None:
    uploaded = st.sidebar.file_uploader(
        "Upload CSV or Excel", type=["csv", "xlsx", "xls", "xlsm"],
        help="Flat tables or BSM chain workbooks (Underlying spot + "
             "Strike / Implied Volatility) are both supported.")
    if uploaded is None:
        st.sidebar.info(
            "Upload a CSV/Excel flat table, or a BSM chain workbook with "
            "Underlying spot, Risk free Rate, and a Strike / IV table "
            "(e.g. `BSM inputs.xlsx`).")
        return None

    # For Excel workbooks with several sheets, let the user pick one.
    sheet_name = None
    if uploaded.name.lower().endswith(EXCEL_EXTENSIONS):
        _, probe = parse_tabular(uploaded.getvalue(), uploaded.name)
        if len(probe.sheets) > 1:
            sheet_name = st.sidebar.selectbox("Excel sheet", probe.sheets)

    df_csv, report = parse_tabular(uploaded.getvalue(), uploaded.name,
                                   sheet_name)
    if df_csv is None:
        for err in report.errors:
            st.sidebar.error(err)
        return None
    for note in report.interpretations:
        st.sidebar.caption(f"\u2139\ufe0f {note}")
    report = detect_columns(df_csv, report)

    with st.sidebar.expander("File preview & validation", expanded=True):
        sheet_info = (f", sheet '{report.sheet_used}'"
                      if report.sheet_used else "")
        layout = (f", layout={report.layout}" if report.layout != "flat"
                  else "")
        st.caption(f"**{report.filename}** ({report.file_format}{sheet_info}"
                   f"{layout}) \u2014 {report.n_rows} rows, "
                   f"{report.n_cols} columns")
        st.dataframe(df_csv.head(8), height=180)
        if report.detected:
            st.caption("Detected: " + ", ".join(
                f"`{col}` \u2192 {fld}" for fld, col in report.detected.items()))
        if report.missing:
            st.warning("Missing fields: " + ", ".join(report.missing)
                       + ". Map them below or fix the file.")
        if report.ambiguous:
            st.warning("Ambiguous columns for: "
                       + ", ".join(report.ambiguous) + ". Choose below.")

    # Column mapping UI: prefilled with detections, manual override allowed.
    mapping: dict[str, str] = {}
    st.sidebar.caption("Column mapping")
    none_label = "\u2014 not present \u2014"
    map_fields = ["spot", "volatility", "risk_free_rate", "dividend_yield",
                  "expiry", "dte", "asof", "strike"]
    for fld in map_fields:
        options = [none_label] + list(df_csv.columns)
        default = report.detected.get(fld)
        index = options.index(default) if default in options else 0
        chosen = st.sidebar.selectbox(fld, options, index=index,
                                      key=f"map_{fld}")
        if chosen != none_label:
            mapping[fld] = chosen

    use_file_strikes = False
    if report.is_strike_chain and "strike" in mapping:
        use_file_strikes = st.sidebar.checkbox(
            f"Price all {len(df_csv)} strikes from file (per-strike IV)",
            value=True,
            help="Uses each row's strike and implied volatility. "
                 "Valuation time comes from the file asof/current date.")

    row_ix = 0
    if not use_file_strikes and len(df_csv) > 1:
        # Default to ATM row (closest strike to spot) when available.
        default_row = 0
        if "strike" in mapping and "spot" in mapping:
            try:
                spot_v = float(df_csv[mapping["spot"]].iloc[0])
                strikes_v = pd.to_numeric(df_csv[mapping["strike"]],
                                          errors="coerce")
                default_row = int((strikes_v - spot_v).abs().idxmin())
            except Exception:
                default_row = 0
        row_ix = st.sidebar.number_input(
            "Row to price (single-vol synthetic grid)", min_value=0,
            max_value=len(df_csv) - 1, value=default_row, step=1)

    required_ok = all(f in mapping for f in ["spot", "volatility",
                                             "risk_free_rate"])
    if not required_ok or ("expiry" not in mapping and "dte" not in mapping):
        st.sidebar.error("Please map spot, volatility, risk-free rate and "
                         "expiry (or dte) to proceed.")
        return None

    if use_file_strikes:
        # ATM row supplies the headline σ shown in KPIs.
        spot_v = float(df_csv[mapping["spot"]].iloc[0])
        strikes_v = pd.to_numeric(df_csv[mapping["strike"]], errors="coerce")
        row_ix = int((strikes_v - spot_v).abs().idxmin())

    provider = CSVDataProvider(df_csv, mapping, row_index=int(row_ix),
                               source_label=report.file_format)
    try:
        mi = provider.get_market_inputs()
    except DataProviderError as exc:
        st.sidebar.error(str(exc))
        return None
    for note in provider.interpretations:
        st.sidebar.caption(f"\u2139\ufe0f {note}")

    file_strikes = file_sigmas = None
    if use_file_strikes:
        strikes_num = pd.to_numeric(df_csv[mapping["strike"]], errors="coerce")
        vols_num = pd.to_numeric(df_csv[mapping["volatility"]], errors="coerce")
        if strikes_num.isna().any() or vols_num.isna().any():
            st.sidebar.error("Strike/IV rows contain non-numeric values.")
            return None
        file_strikes = [float(x) for x in strikes_num.tolist()]
        file_sigmas = []
        for v in vols_num.tolist():
            v = float(v)
            if v > 1.5:
                v /= 100.0
            file_sigmas.append(v)

    contract = None
    if "contract" in df_csv.columns and len(df_csv):
        contract = str(df_csv["contract"].iloc[0])

    # Optional market quote columns for mispricing (never fabricated).
    market_table = None
    quote_cols = [c for c in df_csv.columns if c in {
        "strike", "call_bid", "call_ask", "call_last", "call_mid",
        "put_bid", "put_ask", "put_last", "put_mid",
    }]
    if use_file_strikes and "strike" in quote_cols and len(quote_cols) > 1:
        market_table = df_csv[quote_cols].copy()

    return SidebarMarketState(
        market_inputs=mi,
        use_file_strikes=use_file_strikes,
        file_strikes=file_strikes,
        file_sigmas=file_sigmas,
        contract=contract,
        market_table=market_table,
        interpretations=list(provider.interpretations),
    )


def sidebar_chain_inputs(file_chain_mode: bool = False,
                         ) -> tuple[ChainConfig, DayCount]:
    st.sidebar.subheader("Strike Grid")
    if file_chain_mode:
        st.sidebar.info(
            "Using **strikes and IVs from the uploaded file**. Synthetic "
            "grid controls below are unused for this calculation.")
    interval = st.sidebar.number_input(
        "Strike interval", min_value=0.5, value=settings.DEFAULT_STRIKE_INTERVAL,
        step=5.0, format="%.1f", disabled=file_chain_mode)
    each_side = st.sidebar.number_input(
        "Strikes each side of ATM", min_value=0, max_value=500,
        value=settings.DEFAULT_STRIKES_EACH_SIDE, step=1,
        help=f"Total strikes = 2 x this + 1 (default "
             f"{2 * settings.DEFAULT_STRIKES_EACH_SIDE + 1}).",
        disabled=file_chain_mode)
    atm_method = st.sidebar.radio(
        "ATM method", ["Nearest grid strike to spot", "Explicit ATM strike"],
        disabled=file_chain_mode)
    explicit_atm = None
    if atm_method == "Explicit ATM strike" and not file_chain_mode:
        explicit_atm = st.sidebar.number_input("ATM strike", min_value=0.01,
                                               value=6125.0, step=25.0)
    day_count = st.sidebar.selectbox(
        "Day count", [DayCount.ACT_365, DayCount.ACT_360],
        format_func=lambda d: d.value,
        help="Convention for converting remaining time to T (years). "
             "T = remaining_seconds / (days_per_year x 86400). "
             "When a file asof timestamp is present, T uses that clock.")
    cfg = ChainConfig(
        strike_interval=float(interval), strikes_each_side=int(each_side),
        atm_method="explicit" if explicit_atm is not None else "nearest",
        explicit_atm=explicit_atm)
    return cfg, day_count


# ---------------------------------------------------------------------------
# Result sections
# ---------------------------------------------------------------------------
def render_kpis(meta, mi: MarketInputs, df: pd.DataFrame) -> None:
    atm_row = df[df["is_atm"]]
    atm_call = (float(atm_row["call_bsm_premium"].iloc[0])
                if len(atm_row) else float("nan"))
    atm_put = (float(atm_row["put_bsm_premium"].iloc[0])
               if len(atm_row) else float("nan"))

    row1 = st.columns(4)
    row1[0].metric("SPX Spot", f"{meta.spot:,.2f}")
    row1[1].metric("ATM Strike", f"{meta.atm_strike:,.0f}",
                   delta=f"Spot \u2212 ATM: {meta.spot_minus_atm:+,.2f}",
                   delta_color="off")
    row1[2].metric("DTE", f"{meta.dte_days:,.2f} days",
                   delta=f"T = {meta.T:.6f} yrs", delta_color="off")
    row1[3].metric("Volatility \u03c3", fmt_pct(mi.volatility))

    row2 = st.columns(4)
    row2[0].metric("ATM Call BSM Premium", fmt_number(atm_call, 2))
    row2[1].metric("ATM Put BSM Premium", fmt_number(atm_put, 2))
    row2[2].metric("Risk-Free Rate", fmt_pct(mi.risk_free_rate))
    q_label = fmt_pct(mi.dividend_yield)
    if mi.dividend_assumed:
        q_label += " (assumed)"
    row2[3].metric("Dividend Yield", q_label)


def render_chain_tab(df: pd.DataFrame, meta, inputs: dict) -> None:
    c1, c2, c3, c4 = st.columns([2.2, 1.2, 1.6, 1.4])
    groups = c1.multiselect("Greek groups", GREEK_GROUPS,
                            default=["First Order", "Second Order"])
    decimals = c2.slider("Decimals", 0, 8, settings.DEFAULT_DECIMALS)
    lo, hi = float(df["strike"].min()), float(df["strike"].max())
    strike_range = c3.slider("Strike range", lo, hi, (lo, hi),
                             step=float(inputs["interval"]))
    quick = c4.selectbox("Filter", ["All strikes", "Near ATM (\u00b110 strikes)",
                                    "ITM calls", "ITM puts"])

    filtered = filter_chain(df, strike_range[0], strike_range[1], quick)
    display = build_display_frame(filtered, meta, DEFAULT_CONVENTIONS, groups)
    st.dataframe(style_chain(display, filtered, decimals), height=620)

    max_parity = float(df["parity_error"].abs().max())
    st.caption(
        f"**BSM Premium** = Black\u2013Scholes\u2013Merton theoretical value "
        f"in index points (not market bid/ask/mid/last). Listed SPX options "
        f"use a $100 multiplier \u2014 dollar P&L \u2248 index points \u00d7 100."
    )
    st.caption(
        f"Put-call parity check: max |C \u2212 P \u2212 (S e^(\u2212qT) "
        f"\u2212 K e^(\u2212rT))| = {max_parity:.3e} "
        f"{'\u2705' if max_parity < 1e-8 else '\u26a0\ufe0f investigate'} "
        f"\u00b7 ATM row highlighted in amber \u00b7 ITM cells shaded green "
        f"\u00b7 N/A = mathematically undefined (e.g. at expiry)")

    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "\u2b07 Download CSV",
        data=export_utils.to_csv_bytes(df, meta, inputs, DEFAULT_CONVENTIONS),
        file_name="spx_bsm_option_chain.csv", mime="text/csv")
    d2.download_button(
        "\u2b07 Download Excel",
        data=export_utils.to_excel_bytes(df, meta, inputs, DEFAULT_CONVENTIONS),
        file_name="spx_bsm_option_chain.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def render_charts_tab(df: pd.DataFrame, meta) -> None:
    st.plotly_chart(charts.premium_chart(df, meta), width="stretch")
    default_keys = ["delta", "gamma", "vega", "theta", "rho", "vanna",
                    "volga", "charm"]
    keys = st.multiselect(
        "Greek charts", [g.key for g in GREEK_REGISTRY], default=default_keys,
        format_func=lambda k: GREEKS_BY_KEY[k].label)
    cols = st.columns(2)
    for i, key in enumerate(keys):
        with cols[i % 2]:
            st.plotly_chart(charts.greek_chart(df, meta, key,
                                               DEFAULT_CONVENTIONS),
                            width="stretch")


def render_heatmap_tab(df: pd.DataFrame, meta) -> None:
    c1, c2 = st.columns([1, 3])
    side = c1.radio("Side", ["call", "put"], horizontal=True,
                    format_func=str.upper)
    keys = c2.multiselect(
        "Greeks", [g.key for g in GREEK_REGISTRY],
        default=["gamma", "vega", "theta", "delta", "vanna", "volga", "charm"],
        format_func=lambda k: GREEKS_BY_KEY[k].label)
    if keys:
        st.plotly_chart(charts.heatmap_chart(df, meta, keys, side,
                                             DEFAULT_CONVENTIONS),
                        width="stretch")


def render_model_tab(meta, mi: MarketInputs, inputs: dict) -> None:
    st.subheader("Model Details")
    detail_rows = [
        ("Model", "Black-Scholes-Merton"),
        ("Option style", "European (SPX is European, cash-settled)"),
        ("Underlying", "SPX"),
        ("Spot", f"{meta.spot:,.2f}"),
        ("Volatility", fmt_pct(mi.volatility) + " (constant across strikes)"),
        ("Risk-free rate", fmt_pct(mi.risk_free_rate)),
        ("Dividend yield", fmt_pct(mi.dividend_yield)
         + (" — assumed 0 (no input provided)" if mi.dividend_assumed else "")),
        ("Expiry", meta.expiry.strftime("%Y-%m-%d %H:%M")),
        ("T (years)", f"{meta.T:.10f}"),
        ("Day count", inputs.get("day_count", "ACT/365")
         + "  \u00b7  T = remaining_seconds / (days_per_year \u00d7 86400)"),
        ("Strike interval", f"{inputs['interval']:g}"),
        ("Strikes", f"{meta.n_strikes} ({inputs['each_side']} below + ATM + "
                    f"{inputs['each_side']} above)"),
        ("ATM strike", f"{meta.atm_strike:,.0f}"),
        ("Data source", mi.source),
        ("Premium definition", "BSM Premium = theoretical model value "
                               "(Case A: no market option quotes ingested)"),
        ("SPX multiplier", "$100 per index point on listed contracts; "
                           "table values are in index points"),
        ("Expiry timezone", "Naive local/entered clock; SPX settlement is "
                            "America/New_York \u2014 enter ET times for SPX"),
        ("Pricing model", inputs.get("model_label", meta.model_label)),
        ("q effective", f"{inputs.get('q', mi.dividend_yield)*100:.4f}%"),
        ("Contract", inputs.get("contract") or "—"),
        ("Model version", "bsm-dashboard-2.0"),
    ]
    st.table(pd.DataFrame(detail_rows, columns=["Field", "Value"]))
    if inputs.get("model_rationale"):
        st.info(inputs["model_rationale"])

    st.subheader("Formulas")
    st.latex(r"d_1 = \frac{\ln(S/K) + (r - q + \sigma^2/2)\,T}{\sigma\sqrt{T}}"
             r"\qquad d_2 = d_1 - \sigma\sqrt{T}")
    st.latex(r"C = S e^{-qT} N(d_1) - K e^{-rT} N(d_2) \qquad "
             r"P = K e^{-rT} N(-d_2) - S e^{-qT} N(-d_1)")
    st.latex(r"\text{Put-call parity:}\quad C - P = S e^{-qT} - K e^{-rT}")

    st.subheader("Display Conventions")
    st.markdown(
        f"""
| Greek | Convention |
|---|---|
| Theta, Charm, Color | per **calendar day** (annual value / {DEFAULT_CONVENTIONS.days_per_year:.0f}) |
| Vega, Vanna, Zomma, Rho | per **+1 percentage point** (raw \u00d7 0.01) |
| Volga | per (1 vol pct pt)\u00b2 (raw \u00d7 10\u207b\u2074) |
| Ultima | per (1 vol pct pt)\u00b3 (raw \u00d7 10\u207b\u2076) |
| Delta, Gamma, Speed | per 1 index point (unscaled) |

*Example: Vega = 12.4 means the option value changes by about $12.40 for a
+1 percentage-point change in volatility (e.g. 18.5% \u2192 19.5%), other
inputs held constant.*
""")

    with st.expander("Model Assumptions"):
        st.markdown(
            """
The Black-Scholes-Merton model assumes:

* **European exercise** (correct for SPX; no early-exercise logic applies)
* Lognormal underlying dynamics with **constant volatility**
* **Constant risk-free rate** and **continuous dividend yield**
* Continuous, frictionless trading; no transaction costs; continuous hedging
* No jumps, no stochastic volatility, unlimited liquidity

Real SPX option markets exhibit volatility smiles/skews, stochastic rates,
discrete dividends, jumps and bid/ask spreads. **BSM prices here are
theoretical model values and may differ from actual market prices.**
""")


def render_market_tab(df: pd.DataFrame, inputs: dict) -> None:
    st.subheader("Market vs Model")
    st.markdown(
        """
BSM/Black premiums are **theoretical**. When the file supplies market IVs,
those premiums are the **market-implied European fair values** for the chosen
model — the strongest market alignment possible without bid/ask quotes.

When call/put **mid** (or bid/ask) columns are present, mispricing is:

`Mispricing = BSM Premium − Market Mid` (same for puts).
"""
    )
    has_mid = (
        "call_market_mid" in df.columns
        and df["call_market_mid"].notna().any()
    ) or (
        "put_market_mid" in df.columns
        and df["put_market_mid"].notna().any()
    )
    if inputs.get("use_file_strikes"):
        st.metric("Market IV smile", "Active (per-strike)")
        st.caption(
            f"Model: {inputs.get('model_label')} · "
            f"{inputs.get('model_rationale', '')}"
        )
    if not has_mid:
        st.info(
            "No market mid/bid/ask columns in this dataset. Add optional "
            "columns `call_mid` / `put_mid` (or bid/ask) to unlock numerical "
            "mispricing MAE/MAPE. With market IVs alone, model inclination is "
            "already maximized for European pricing."
        )
        show = df[["strike", "sigma", "call_bsm_premium", "put_bsm_premium",
                   "call_status", "put_status"]].copy()
        show = show.rename(columns={
            "sigma": "Market IV",
            "call_bsm_premium": "Call BSM Premium",
            "put_bsm_premium": "Put BSM Premium",
        })
        st.dataframe(show, height=480)
        return

    summary = summarize_mispricing(df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quotes joined", f"{summary.n_with_market}")
    c2.metric("Call MAE", "N/A" if summary.call_mae is None
              else f"{summary.call_mae:.4f}")
    c3.metric("Put MAE", "N/A" if summary.put_mae is None
              else f"{summary.put_mae:.4f}")
    c4.metric("Call MAPE", "N/A" if summary.call_mape_pct is None
              else f"{summary.call_mape_pct:.2f}%")
    cols = [c for c in [
        "strike", "sigma", "call_market_mid", "call_bsm_premium",
        "call_mispricing", "call_mispricing_pct", "call_market_iv",
        "put_market_mid", "put_bsm_premium", "put_mispricing",
        "put_mispricing_pct", "put_market_iv",
    ] if c in df.columns]
    st.dataframe(df[cols], height=520)


def render_scenarios_tab(df: pd.DataFrame, inputs: dict) -> None:
    st.subheader("Scenario / Shock Analysis")
    st.caption(
        "Shocks applied to spot, vol (parallel vol-point shift), rate and "
        "calendar time. ATM premiums and Greeks are recomputed from the same "
        "engine as the main chain."
    )
    expiry = datetime.fromisoformat(inputs["expiry_iso"])
    asof = (datetime.fromisoformat(inputs["asof_iso"])
            if inputs.get("asof_iso") else None)
    scen = run_scenarios(
        df, inputs["spot"], inputs["T"], inputs["r"], inputs["q"],
        expiry, asof, DEFAULT_SCENARIOS,
    )
    show = scen.copy()
    show["spot"] = show["spot"].map(lambda x: f"{x:,.2f}")
    show["call_bsm_premium"] = show["call_bsm_premium"].map(lambda x: f"{x:,.4f}")
    show["put_bsm_premium"] = show["put_bsm_premium"].map(lambda x: f"{x:,.4f}")
    show["d_call"] = show["d_call"].map(lambda x: f"{x:+,.4f}")
    show["d_put"] = show["d_put"].map(lambda x: f"{x:+,.4f}")
    st.dataframe(show, height=520)
    st.plotly_chart(
        charts.scenario_pnl_chart(scen), width="stretch",
    )


def render_greek_guide() -> None:
    st.subheader("Greek Definitions & Conventions")
    for group in GREEK_GROUPS:
        st.markdown(f"#### {group}")
        for spec in [g for g in GREEK_REGISTRY if g.group == group]:
            with st.expander(f"{spec.label} \u2014 {spec.definition}"):
                st.markdown(f"**Formula:** `{spec.formula}`")
                st.markdown(f"**Raw unit:** {spec.raw_unit}")
                st.markdown(f"**Displayed as:** {spec.display_unit}")
                st.markdown(spec.help_text)
                if not spec.per_side:
                    st.caption("Identical for calls and puts.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.title(settings.APP_TITLE)
    st.markdown(
        '<div class="disclaimer">BSM outputs are <b>theoretical model '
        'values</b>. They are not guarantees of market prices, execution '
        'prices, profitability or trading outcomes. Differences vs. market '
        'arise from volatility smile/skew, stochastic rates, dividends, '
        'jumps, liquidity and bid/ask spreads.</div>',
        unsafe_allow_html=True)

    state = sidebar_market_inputs()
    file_chain = bool(state and state.use_file_strikes)
    cfg, day_count = sidebar_chain_inputs(file_chain_mode=file_chain)

    st.sidebar.subheader("Pricing Model")
    model_choice = st.sidebar.selectbox(
        "Model",
        ["Auto (detect ES/SPX)", "Black-76 futures", "BSM equity index"],
        help="ES futures options should use Black-76 (q = r). SPX cash "
             "options use BSM with a dividend yield.")

    calculate = st.sidebar.button("CALCULATE OPTION CHAIN", type="primary",
                                  width="stretch",
                                  disabled=state is None)

    if state is None:
        st.info("Configure a data source in the sidebar to begin.")
        return

    mi = state.market_inputs
    asof = mi.asof
    T = time_to_expiry(mi.expiry, now=asof, convention=day_count)

    explicit_model = None
    if model_choice.startswith("Black-76"):
        explicit_model = "BLACK76"
    elif model_choice.startswith("BSM"):
        explicit_model = "BSM"
    model = resolve_model(
        state.contract, mi.risk_free_rate, mi.dividend_yield,
        explicit=explicit_model, dividend_assumed=mi.dividend_assumed)
    st.sidebar.caption(f"Using **{model.label}** — {model.rationale}")

    val = validate_market_inputs(mi.spot, mi.volatility, mi.risk_free_rate,
                                 model.q_effective, T)
    if file_chain:
        cfg_val = ValidationResult()
    else:
        try:
            preview_atm = detect_atm_strike(mi.spot, cfg)
        except ValueError as exc:
            st.error(str(exc))
            return
        cfg_val = validate_chain_config(cfg.strike_interval,
                                        cfg.strikes_each_side,
                                        atm_strike=preview_atm)

    for err in val.errors + cfg_val.errors:
        st.error(err)
    if not (val.ok and cfg_val.ok):
        logger.warning("Validation failed: %s", val.errors + cfg_val.errors)
        return
    for warning in val.warnings + cfg_val.warnings:
        st.warning(warning)

    if calculate:
        market_records = None
        if state.market_table is not None:
            market_records = state.market_table.to_dict(orient="list")
        st.session_state["calc"] = dict(
            spot=mi.spot, sigma=mi.volatility, r=mi.risk_free_rate,
            q=model.q_effective, q_input=mi.dividend_yield, T=T,
            interval=cfg.strike_interval,
            each_side=cfg.strikes_each_side, atm_method=cfg.atm_method,
            explicit_atm=cfg.explicit_atm,
            expiry_iso=mi.expiry.isoformat(),
            source=mi.source, day_count=day_count.value,
            dividend_assumed=mi.dividend_assumed,
            asof_iso=asof.isoformat() if asof else None,
            file_strikes=tuple(state.file_strikes)
            if state.use_file_strikes and state.file_strikes else None,
            file_sigmas=tuple(state.file_sigmas)
            if state.use_file_strikes and state.file_sigmas else None,
            use_file_strikes=state.use_file_strikes,
            model_label=model.label,
            model_rationale=model.rationale,
            model_name=model.model.value,
            contract=state.contract,
            market_records=market_records,
        )

    if "calc" not in st.session_state:
        st.info("Set your inputs, then click **CALCULATE OPTION CHAIN**.")
        return

    p = st.session_state["calc"]
    # Render results from the calculated snapshot, not live sidebar values,
    # so KPIs/table/charts always agree with each other.
    mi_calc = MarketInputs(
        spot=p["spot"], volatility=p["sigma"], risk_free_rate=p["r"],
        dividend_yield=p.get("q_input", p["q"]),
        expiry=datetime.fromisoformat(p["expiry_iso"]),
        source=p["source"], dividend_assumed=p["dividend_assumed"],
        asof=(datetime.fromisoformat(p["asof_iso"])
              if p.get("asof_iso") else None))
    try:
        df, meta = compute_chain(
            p["spot"], p["sigma"], p["r"], p["q"], p["T"], p["interval"],
            p["each_side"], p["atm_method"], p["explicit_atm"], p["expiry_iso"],
            file_strikes=p.get("file_strikes"),
            file_sigmas=p.get("file_sigmas"),
            asof_iso=p.get("asof_iso"),
            model_label=p.get("model_label", "BSM (equity index)"),
        )
    except StrikeGridError as exc:
        st.error(str(exc))
        return
    except Exception:
        logger.exception("Chain calculation failed")
        st.error("The option chain could not be calculated with these inputs. "
                 "Please review the input values and try again.")
        return

    market_df = None
    if p.get("market_records"):
        market_df = pd.DataFrame(p["market_records"])
        df = attach_market_columns(df, market_df)
        df = enrich_market_ivs(df, p["spot"], p["T"], p["r"], p["q"])

    vol_note = (f"ATM \u03c3 = {p['sigma'] * 100:.2f}% (market/file smile)"
                if p.get("use_file_strikes")
                else f"\u03c3 = {p['sigma'] * 100:.2f}%")
    asof_note = ""
    if p.get("asof_iso"):
        asof_note = (f" \u00b7 asof "
                     f"{datetime.fromisoformat(p['asof_iso']).strftime('%Y-%m-%d %H:%M')}")
    st.caption(
        f"Data source: **{p['source']}** \u00b7 Model **{p.get('model_label', meta.model_label)}** "
        f"\u00b7 Expiry {meta.expiry.strftime('%Y-%m-%d %H:%M')} \u00b7 "
        f"Day count {p['day_count']}{asof_note} \u00b7 {vol_note}, "
        f"r = {p['r'] * 100:.2f}%, q_eff = {p['q'] * 100:.2f}% "
        f"\u00b7 strikes={meta.n_strikes}"
    )
    if p.get("use_file_strikes"):
        st.success(
            "Market inclination: chain priced with **per-strike market IVs** "
            f"under **{p.get('model_label')}**. BSM/Black premiums are the "
            "market-implied European fair values for those IVs (not a forecast "
            "of future mids)."
        )
    if meta.is_expired:
        st.warning("This expiry is in the past: prices shown are intrinsic "
                   "values and Greeks are N/A (undefined at expiry).")

    render_kpis(meta, mi_calc, df)
    (tab_chain, tab_market, tab_scen, tab_charts, tab_heat,
     tab_model, tab_guide) = st.tabs(
        ["Option Chain", "Market vs Model", "Scenarios", "Charts",
         "Greek Heatmap", "Model Details", "Greek Guide"])
    with tab_chain:
        render_chain_tab(df, meta, p)
    with tab_market:
        render_market_tab(df, p)
    with tab_scen:
        render_scenarios_tab(df, p)
    with tab_charts:
        render_charts_tab(df, meta)
    with tab_heat:
        render_heatmap_tab(df, meta)
    with tab_model:
        render_model_tab(meta, mi_calc, p)
    with tab_guide:
        render_greek_guide()


if __name__ == "__main__":
    main()
