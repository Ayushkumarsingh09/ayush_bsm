"""CSV / Excel market-data provider with intelligent column detection.

Responsibilities:

* Parse an uploaded CSV or Excel workbook robustly (clear errors, never a
  stack trace). Excel sheets are selectable; the first sheet is the default.
* Detect required fields from common column-name aliases
  (e.g. ``spot`` / ``spot_price`` / ``underlying_price`` / ``SPX``).
* Report detected, missing and ambiguous fields so the UI can offer a
  manual column-mapping step instead of guessing.
* Normalize percentage-style values (e.g. volatility ``18.5`` meaning
  18.5%) with explicit, reported heuristics -- never silently.

Both formats produce the same DataFrame + report, so everything downstream
(detection, mapping, provider) is format-agnostic.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from data.base import DataProvider, DataProviderError, MarketInputs

logger = logging.getLogger(__name__)

# Canonical field -> accepted (normalized) column-name aliases.
FIELD_ALIASES: dict[str, set[str]] = {
    "spot": {"spot", "spot_price", "underlying", "underlying_price", "spx",
             "spx_price", "s", "index_price", "underlying_spot", "price"},
    "volatility": {"volatility", "vol", "iv", "implied_volatility",
                   "implied_vol", "sigma", "atm_iv"},
    "risk_free_rate": {"risk_free_rate", "rate", "r", "rf", "riskfree",
                       "risk_free", "interest_rate", "rfr"},
    "dividend_yield": {"dividend_yield", "q", "div_yield", "dividend",
                       "yield", "div", "dividendyield"},
    "expiry": {"expiry", "expiration", "expiry_date", "expiration_date",
               "maturity", "maturity_date", "exp_date", "exp", "expiry_dt",
               "expiry_date_and_time"},
    "dte": {"dte", "days_to_expiry", "days_to_expiration", "days"},
    "strike": {"strike", "k", "strike_price"},
    "asof": {"asof", "current_date_and_time", "current_date", "valuation_date",
             "trade_date", "as_of"},
}

REQUIRED_FIELDS = ["spot", "volatility", "risk_free_rate"]
# expiry OR dte satisfies the expiry requirement; dividend_yield is optional
# (defaults to 0 with an explicit, visible assumption flag).

# Values above these thresholds are interpreted as percentages and divided
# by 100. SPX vol above 150% and rates above 100% are implausible, so the
# heuristic is safe; every conversion is reported to the user.
VOL_PERCENT_THRESHOLD = 1.5
RATE_PERCENT_THRESHOLD = 1.0


@dataclass
class CSVReport:
    """Everything the UI needs to render the file preview/validation panel."""

    filename: str
    n_rows: int = 0
    n_cols: int = 0
    columns: list[str] = field(default_factory=list)
    detected: dict[str, str] = field(default_factory=dict)     # field -> column
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    interpretations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    file_format: str = "CSV"                    # "CSV" or "Excel"
    sheets: list[str] = field(default_factory=list)   # Excel sheet names
    sheet_used: str | None = None
    # "flat" = ordinary table; "bsm_chain_workbook" = header + strike/IV chain
    layout: str = "flat"
    is_strike_chain: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors and not self.missing and not self.ambiguous


def _normalize_name(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in str(name).strip().lower())


def parse_csv(content: bytes, filename: str) -> tuple[pd.DataFrame | None, CSVReport]:
    """Parse CSV bytes; return (DataFrame or None, report with any errors)."""
    report = CSVReport(filename=filename)
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:  # pandas raises many concrete types here
        logger.warning("CSV parse failure for %s: %s", filename, exc)
        report.errors.append(
            f"Could not parse '{filename}' as CSV: {exc}. "
            "Check that the file is comma-separated text with a header row."
        )
        return None, report
    if df.empty:
        report.errors.append("The CSV contains no data rows.")
        return None, report
    report.n_rows = len(df)
    report.n_cols = len(df.columns)
    report.columns = [str(c) for c in df.columns]
    return df, report


EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm")


def parse_excel(content: bytes, filename: str, sheet_name: str | None = None,
                ) -> tuple[pd.DataFrame | None, CSVReport]:
    """Parse Excel bytes; return (DataFrame or None, report).

    Reads ``sheet_name`` if given, otherwise the first sheet. All sheet
    names are listed in the report so the UI can offer a sheet selector.
    Fully-empty rows/columns (common in hand-made workbooks) are dropped.

    If the sheet matches the proprietary BSM chain workbook layout
    (Underlying spot / Risk free Rate header + Strike/IV table), it is
    normalized to a flat per-strike DataFrame automatically.
    """
    from data.bsm_workbook import try_normalize_bsm_chain_workbook

    report = CSVReport(filename=filename, file_format="Excel")
    try:
        workbook = pd.ExcelFile(io.BytesIO(content))
        report.sheets = [str(s) for s in workbook.sheet_names]
        target = sheet_name if sheet_name is not None else report.sheets[0]
        if target not in report.sheets:
            report.errors.append(
                f"Sheet '{target}' not found in '{filename}'. "
                f"Available sheets: {', '.join(report.sheets)}."
            )
            return None, report
        raw = workbook.parse(target, header=None)
    except Exception as exc:
        logger.warning("Excel parse failure for %s: %s", filename, exc)
        report.errors.append(
            f"Could not parse '{filename}' as an Excel workbook: {exc}. "
            "Check that the file is a valid .xlsx/.xls with a header row."
        )
        return None, report

    report.sheet_used = target
    raw = raw.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if raw.empty:
        report.errors.append(
            f"Sheet '{target}' of '{filename}' contains no data rows."
        )
        return None, report

    normalized = try_normalize_bsm_chain_workbook(raw)
    if normalized is not None:
        df, notes = normalized
        report.layout = "bsm_chain_workbook"
        report.is_strike_chain = True
        report.interpretations.extend(notes)
        report.n_rows = len(df)
        report.n_cols = len(df.columns)
        report.columns = [str(c) for c in df.columns]
        return df, report

    # Ordinary flat table: promote first non-empty row to header.
    df = workbook.parse(target)
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    df.columns = [str(c) for c in df.columns]
    if df.empty:
        report.errors.append(
            f"Sheet '{target}' of '{filename}' contains no data rows."
        )
        return None, report
    report.n_rows = len(df)
    report.n_cols = len(df.columns)
    report.columns = list(df.columns)
    return df, report


def parse_tabular(content: bytes, filename: str,
                  sheet_name: str | None = None,
                  ) -> tuple[pd.DataFrame | None, CSVReport]:
    """Parse CSV or Excel based on the file extension."""
    if filename.lower().endswith(EXCEL_EXTENSIONS):
        return parse_excel(content, filename, sheet_name)
    return parse_csv(content, filename)


def detect_columns(df: pd.DataFrame, report: CSVReport) -> CSVReport:
    """Fill the report with detected/ambiguous/missing field mappings."""
    normalized = {col: _normalize_name(col) for col in df.columns}
    for fld, aliases in FIELD_ALIASES.items():
        matches = [col for col, norm in normalized.items() if norm in aliases]
        if len(matches) == 1:
            report.detected[fld] = matches[0]
        elif len(matches) > 1:
            report.ambiguous[fld] = matches

    for fld in REQUIRED_FIELDS:
        if fld not in report.detected and fld not in report.ambiguous:
            report.missing.append(fld)
    if ("expiry" not in report.detected and "expiry" not in report.ambiguous
            and "dte" not in report.detected and "dte" not in report.ambiguous):
        report.missing.append("expiry (or dte)")
    return report


def _to_float(value, field_name: str, row: int) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise DataProviderError(
            f"Row {row}: value '{value}' in field '{field_name}' is not numeric."
        )
    if pd.isna(v):
        raise DataProviderError(
            f"Row {row}: field '{field_name}' is missing (NaN)."
        )
    return v


class CSVDataProvider(DataProvider):
    """Builds :class:`MarketInputs` from a parsed tabular row and mapping.

    Works identically for CSV and Excel input -- both are parsed into a
    DataFrame upstream (``parse_tabular``).

    Args:
        df: parsed DataFrame.
        mapping: field -> column name. Must cover the required fields and
            either ``expiry`` or ``dte``.
        row_index: which row to price from (file may contain several scenarios).
        now: reference timestamp for dte -> expiry conversion (testable).
        source_label: shown in the UI/export, e.g. "CSV" or "Excel".
    """

    name = "CSV"

    def __init__(self, df: pd.DataFrame, mapping: dict[str, str],
                 row_index: int = 0, now: datetime | None = None,
                 source_label: str = "CSV") -> None:
        self._df = df
        self._mapping = mapping
        self._row_index = row_index
        self._now = now or datetime.now()
        self._source_label = source_label
        self.interpretations: list[str] = []

    def get_market_inputs(self) -> MarketInputs:
        if self._row_index < 0 or self._row_index >= len(self._df):
            raise DataProviderError(
                f"Row {self._row_index} is out of range (CSV has "
                f"{len(self._df)} rows)."
            )
        row = self._df.iloc[self._row_index]
        r_ix = self._row_index

        def get(fld: str):
            col = self._mapping.get(fld)
            return None if col is None or col not in self._df.columns else row[col]

        spot = _to_float(get("spot"), "spot", r_ix)

        vol = _to_float(get("volatility"), "volatility", r_ix)
        if vol > VOL_PERCENT_THRESHOLD:
            self.interpretations.append(
                f"Volatility {vol:g} interpreted as {vol:g}% -> {vol / 100:.4f}."
            )
            vol /= 100.0
        elif 1.0 < vol <= VOL_PERCENT_THRESHOLD:
            # Ambiguous band: e.g. 1.2 could mean σ=1.2 (120%) or a mistyped %.
            self.interpretations.append(
                f"Volatility {vol:g} kept as decimal \u03c3 = {vol:g} "
                f"({vol * 100:.2f}%). If you intended {vol:g}% "
                f"(\u03c3 = {vol / 100:.4f}), enter it as a percentage "
                f"(e.g. 18.5 for 18.5%)."
            )

        rate = _to_float(get("risk_free_rate"), "risk_free_rate", r_ix)
        if abs(rate) > RATE_PERCENT_THRESHOLD:
            self.interpretations.append(
                f"Risk-free rate {rate:g} interpreted as {rate:g}% -> "
                f"{rate / 100:.4f}."
            )
            rate /= 100.0

        div_raw = get("dividend_yield")
        dividend_assumed = div_raw is None or pd.isna(div_raw)
        if dividend_assumed:
            div = 0.0
            self.interpretations.append(
                "No dividend yield in file: assuming q = 0% (flagged in UI)."
            )
        else:
            div = _to_float(div_raw, "dividend_yield", r_ix)
            if abs(div) > RATE_PERCENT_THRESHOLD:
                self.interpretations.append(
                    f"Dividend yield {div:g} interpreted as {div:g}% -> "
                    f"{div / 100:.4f}."
                )
                div /= 100.0

        expiry = self._resolve_expiry(get("expiry"), get("dte"), r_ix)
        asof = self._resolve_asof(get("asof"), r_ix)

        return MarketInputs(
            spot=spot, volatility=vol, risk_free_rate=rate,
            dividend_yield=div, expiry=expiry,
            source=f"{self._source_label} (row {r_ix})",
            dividend_assumed=dividend_assumed,
            asof=asof,
        )

    def _resolve_asof(self, asof_raw, row: int) -> datetime | None:
        if asof_raw is None or pd.isna(asof_raw):
            return None
        try:
            ts = pd.to_datetime(asof_raw).to_pydatetime()
        except (ValueError, TypeError):
            raise DataProviderError(
                f"Row {row}: could not parse asof/current date '{asof_raw}'."
            )
        self.interpretations.append(
            f"Valuation time (asof) from file: {ts.isoformat(sep=' ', timespec='minutes')}."
        )
        return ts

    def _resolve_expiry(self, expiry_raw, dte_raw, row: int) -> datetime:
        if expiry_raw is not None and not pd.isna(expiry_raw):
            try:
                ts = pd.to_datetime(expiry_raw)
            except (ValueError, TypeError):
                raise DataProviderError(
                    f"Row {row}: could not parse expiry '{expiry_raw}' as a "
                    "date. Use an ISO format like 2026-09-18 or "
                    "2026-09-18 16:00."
                )
            expiry = ts.to_pydatetime()
            # Date-only expiries default to a 16:00 market close so same-day
            # expiries keep their remaining intraday time.
            if expiry.hour == 0 and expiry.minute == 0 and expiry.second == 0:
                expiry = expiry.replace(hour=16, minute=0)
                self.interpretations.append(
                    "Expiry had no time component: assuming 16:00 "
                    "(market close)."
                )
            return expiry
        if dte_raw is not None and not pd.isna(dte_raw):
            dte = _to_float(dte_raw, "dte", row)
            self.interpretations.append(
                f"Expiry derived from dte = {dte:g} calendar days from now."
            )
            return self._now + timedelta(days=dte)
        raise DataProviderError(
            f"Row {row}: no expiry date or dte value found."
        )
