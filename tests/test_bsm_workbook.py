"""Tests for the proprietary BSM chain workbook layout (BSM inputs.xlsx)."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analytics.chain import ChainConfig, build_option_chain
from data.bsm_workbook import try_normalize_bsm_chain_workbook
from data.csv_provider import detect_columns, parse_tabular
from pricing.bsm import bsm_price
from pricing.volatility import PerStrikeVolatility
from utils.dates import time_to_expiry


ROOT = Path(__file__).resolve().parent.parent
CHAIN_XLSX = ROOT / "sample_data" / "BSM_chain_inputs.xlsx"
ALT_XLSX = ROOT / "BSM inputs.xlsx"


def _workbook_path() -> Path:
    if CHAIN_XLSX.exists():
        return CHAIN_XLSX
    if ALT_XLSX.exists():
        return ALT_XLSX
    pytest.skip("BSM chain workbook not present")


class TestBsmWorkbookNormalize:
    def test_detects_layout_and_spot_rate(self):
        path = _workbook_path()
        raw = pd.read_excel(path, header=None)
        out = try_normalize_bsm_chain_workbook(raw)
        assert out is not None
        df, notes = out
        assert len(df) == 57
        assert float(df["spot"].iloc[0]) == pytest.approx(7500.0)
        assert float(df["risk_free_rate"].iloc[0]) == pytest.approx(0.0365)
        assert set(df["strike"]) >= {6800.0, 7500.0, 8200.0}
        assert any("chain workbook" in n for n in notes)

    def test_parse_tabular_sets_chain_layout(self):
        path = _workbook_path()
        df, report = parse_tabular(path.read_bytes(), path.name)
        assert df is not None
        assert report.layout == "bsm_chain_workbook"
        assert report.is_strike_chain
        report = detect_columns(df, report)
        assert report.ok
        assert report.detected["spot"] == "spot"
        assert report.detected["strike"] == "strike"
        assert report.detected["asof"] == "asof"


class TestBsmWorkbookPricing:
    def test_atm_premiums_match_independent_bsm(self):
        path = _workbook_path()
        df, report = parse_tabular(path.read_bytes(), path.name)
        report = detect_columns(df, report)
        assert report.ok

        spot = float(df["spot"].iloc[0])
        r = float(df["risk_free_rate"].iloc[0])
        q = 0.0
        asof = pd.to_datetime(df["asof"].iloc[0]).to_pydatetime()
        expiry = pd.to_datetime(df["expiry"].iloc[0]).to_pydatetime()
        T = time_to_expiry(expiry, now=asof)
        strikes = df["strike"].to_numpy(dtype=float)
        vols = df["volatility"].to_numpy(dtype=float)

        chain, meta = build_option_chain(
            spot, T, r, q, PerStrikeVolatility(strikes, vols),
            ChainConfig(), expiry, strikes=strikes, asof=asof,
        )
        assert meta.atm_strike == 7500.0
        assert meta.n_strikes == 57
        atm = chain.loc[chain["is_atm"]].iloc[0]
        assert atm["sigma"] == pytest.approx(0.1289)
        expected_call = bsm_price(spot, 7500.0, T, r, q, 0.1289, True)
        expected_put = bsm_price(spot, 7500.0, T, r, q, 0.1289, False)
        assert atm["call_bsm_premium"] == pytest.approx(expected_call, rel=1e-12)
        assert atm["put_bsm_premium"] == pytest.approx(expected_put, rel=1e-12)
        assert float(chain["parity_error"].abs().max()) < 1e-9
        # Per-strike smile: deep OTM call IV higher than ATM in this file.
        assert float(chain.loc[chain["strike"] == 8200.0, "sigma"].iloc[0]) > 0.1289
