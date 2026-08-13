"""Run the full analytics pipeline on an Excel/CSV input file from the CLI.

Usage:
    python scripts/run_excel_file.py "BSM inputs.xlsx"
    python scripts/run_excel_file.py "sample_data/BSM inputs.xlsx" [row]

Supports:
* Flat market-input tables (one scenario per row)
* BSM chain workbooks (Underlying spot + Strike / IV table) — prices every
  listed strike with its own IV using the file's Current date as asof.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.chain import ChainConfig, build_option_chain
from data.csv_provider import CSVDataProvider, detect_columns, parse_tabular
from pricing.models import resolve_model
from pricing.volatility import ConstantVolatility, PerStrikeVolatility
from utils.dates import time_to_expiry
from validation.validators import validate_market_inputs


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "BSM inputs.xlsx")
    row = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    if not path.exists():
        print(f"ERROR: file not found: {path}")
        raise SystemExit(1)

    df, report = parse_tabular(path.read_bytes(), path.name)
    if df is None:
        for err in report.errors:
            print("ERROR:", err)
        raise SystemExit(1)
    for note in report.interpretations:
        print("  note:", note)
    report = detect_columns(df, report)
    sheet = f", sheet {report.sheet_used!r}" if report.sheet_used else ""
    print(f"Parsed {report.filename} ({report.file_format}{sheet}, "
          f"layout={report.layout}): {report.n_rows} rows x {report.n_cols} cols")
    print("Detected mapping:", report.detected)
    if not report.ok:
        print("Missing:", report.missing, "| Ambiguous:", report.ambiguous)
        raise SystemExit(1)

    mapping = dict(report.detected)
    use_file_chain = report.is_strike_chain and "strike" in mapping

    if use_file_chain:
        spot_v = float(df[mapping["spot"]].iloc[0])
        strikes_v = pd.to_numeric(df[mapping["strike"]], errors="coerce")
        row = int((strikes_v - spot_v).abs().idxmin())
        print(f"File chain mode: pricing {len(df)} strikes with per-strike IV "
              f"(ATM row index = {row})")

    provider = CSVDataProvider(df, mapping, row_index=row,
                               source_label=report.file_format)
    mi = provider.get_market_inputs()
    for note in provider.interpretations:
        print("  note:", note)

    contract = (str(df["contract"].iloc[0])
                if "contract" in df.columns and len(df) else None)
    model = resolve_model(contract, mi.risk_free_rate, mi.dividend_yield,
                          dividend_assumed=mi.dividend_assumed)
    print(f"Model: {model.label}")
    print(f"  {model.rationale}")

    T = time_to_expiry(mi.expiry, now=mi.asof)
    val = validate_market_inputs(mi.spot, mi.volatility, mi.risk_free_rate,
                                 model.q_effective, T)
    for msg in val.errors:
        print("ERROR:", msg)
    for msg in val.warnings:
        print("WARNING:", msg)
    if not val.ok:
        raise SystemExit(1)

    if use_file_chain:
        strikes = pd.to_numeric(df[mapping["strike"]], errors="coerce").to_numpy(
            dtype=float)
        vols = pd.to_numeric(df[mapping["volatility"]], errors="coerce").to_numpy(
            dtype=float)
        vols = np.where(vols > 1.5, vols / 100.0, vols)
        vol_provider = PerStrikeVolatility(strikes, vols)
        chain, meta = build_option_chain(
            mi.spot, T, mi.risk_free_rate, model.q_effective,
            vol_provider, ChainConfig(), mi.expiry,
            strikes=strikes, asof=mi.asof, model_label=model.label,
        )
    else:
        chain, meta = build_option_chain(
            mi.spot, T, mi.risk_free_rate, model.q_effective,
            ConstantVolatility(mi.volatility), ChainConfig(), mi.expiry,
            asof=mi.asof, model_label=model.label,
        )

    print()
    print(f"Source={mi.source}  Spot={meta.spot:,.2f}  "
          f"ATM={meta.atm_strike:,.0f}  DTE={meta.dte_days:.2f}d  "
          f"T={meta.T:.6f}y  strikes={meta.n_strikes}")
    print(f"ATM sigma={mi.volatility:.4%}  r={mi.risk_free_rate:.4%}  "
          f"q_eff={model.q_effective:.4%}"
          + ("  (input q assumed 0; Black-76 uses q=r)" if mi.dividend_assumed
             and model.model.value == "BLACK76" else
             ("  (q assumed)" if mi.dividend_assumed else "")))
    if mi.asof is not None:
        print(f"asof={mi.asof.isoformat(sep=' ', timespec='minutes')}  "
              f"expiry={mi.expiry.isoformat(sep=' ', timespec='minutes')}")
    atm = chain[chain["is_atm"]].iloc[0]
    print(f"ATM {atm['strike']:.0f}:  "
          f"Call BSM Premium={atm['call_bsm_premium']:.4f}  "
          f"Put BSM Premium={atm['put_bsm_premium']:.4f}  "
          f"IV={atm['sigma']:.4%}  "
          f"dC={atm['call_delta']:.4f}  "
          f"dP={atm['put_delta']:.4f}  gamma={atm['gamma']:.6f}  "
          f"vega/1pct={atm['vega'] * 0.01:.3f}  "
          f"thetaC/day={atm['call_theta'] / 365:.3f}")
    print(f"max |parity error| = {chain['parity_error'].abs().max():.3e}")

    # Print a compact premium table for file-chain mode.
    if use_file_chain:
        print()
        print(f"{'Strike':>8}  {'IV%':>7}  {'Call BSM':>12}  {'Put BSM':>12}  "
              f"{'Call Δ':>8}  {'Put Δ':>8}  Status")
        for _, r in chain.iterrows():
            mark = " <-- ATM" if r["is_atm"] else ""
            print(f"{r['strike']:8.0f}  {r['sigma']*100:7.2f}  "
                  f"{r['call_bsm_premium']:12.4f}  {r['put_bsm_premium']:12.4f}  "
                  f"{r['call_delta']:8.4f}  {r['put_delta']:8.4f}  "
                  f"{r['call_status']}/{r['put_status']}{mark}")


if __name__ == "__main__":
    main()
