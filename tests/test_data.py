"""Data layer tests: CSV detection/normalization, chain config, dates,
validators, implied vol round-trip."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import io

from analytics.chain import ChainConfig, detect_atm_strike, generate_strikes
from data.base import DataProviderError, DataProviderNotConfiguredError
from data.csv_provider import (CSVDataProvider, CSVReport, detect_columns,
                               parse_csv, parse_excel, parse_tabular)
from data.live_provider import LiveMarketDataProvider
from data.manual import ManualDataProvider
from pricing.bsm import bsm_price
from pricing.implied_vol import ImpliedVolError, implied_volatility
from utils.dates import DayCount, time_to_expiry
from validation.validators import validate_chain_config, validate_market_inputs

NOW = datetime(2026, 8, 13, 12, 0, 0)


class TestStrikeGeneration:
    def test_default_57_strikes(self):
        cfg = ChainConfig()
        atm = detect_atm_strike(6137.0, cfg)
        strikes = generate_strikes(atm, cfg)
        assert len(strikes) == 57
        assert np.all(np.diff(strikes) == 25.0)
        assert strikes[28] == atm

    def test_atm_is_nearest_grid_strike(self):
        cfg = ChainConfig()
        # 6137 is 12 away from 6125 and 13 away from 6150.
        assert detect_atm_strike(6137.0, cfg) == 6125.0
        assert detect_atm_strike(6138.0, cfg) == 6150.0

    def test_atm_midpoint_prefers_lower_strike(self):
        cfg = ChainConfig()
        # Exactly halfway between 6125 and 6150 — prefer lower (deterministic).
        assert detect_atm_strike(6137.5, cfg) == 6125.0

    def test_explicit_atm(self):
        cfg = ChainConfig(atm_method="explicit", explicit_atm=6100.0)
        assert detect_atm_strike(6137.0, cfg) == 6100.0

    def test_configurable_counts(self):
        cfg = ChainConfig(strike_interval=50.0, strikes_each_side=10)
        strikes = generate_strikes(detect_atm_strike(6137.0, cfg), cfg)
        assert len(strikes) == 21
        assert np.all(np.diff(strikes) == 50.0)

    def test_non_positive_strikes_rejected(self):
        from analytics.chain import StrikeGridError
        cfg = ChainConfig(strike_interval=25.0, strikes_each_side=28,
                          atm_method="explicit", explicit_atm=50.0)
        with pytest.raises(StrikeGridError, match="non-positive"):
            generate_strikes(50.0, cfg)


class TestCSVDetection:
    def _report(self, df):
        return detect_columns(df, CSVReport(filename="test.csv"))

    def test_alias_detection(self):
        df = pd.DataFrame({"SPX_price": [6137.0], "iv": [18.5],
                           "rf": [5.25], "q": [1.35],
                           "expiration": ["2026-09-18"]})
        rep = self._report(df)
        assert rep.detected == {
            "spot": "SPX_price", "volatility": "iv", "risk_free_rate": "rf",
            "dividend_yield": "q", "expiry": "expiration",
        }
        assert rep.ok

    def test_missing_fields_reported(self):
        rep = self._report(pd.DataFrame({"spot": [6137.0]}))
        assert "volatility" in rep.missing
        assert "risk_free_rate" in rep.missing
        assert "expiry (or dte)" in rep.missing

    def test_ambiguous_columns_reported(self):
        df = pd.DataFrame({"vol": [0.18], "iv": [0.19], "spot": [6137.0],
                           "rate": [0.05], "expiry": ["2026-09-18"]})
        rep = self._report(df)
        assert set(rep.ambiguous["volatility"]) == {"vol", "iv"}
        assert not rep.ok

    def test_parse_invalid_bytes(self):
        df, rep = parse_csv(b"\x00\x01\x02", "bad.bin")
        assert df is None or rep.errors == []  # tolerate pandas parsing bytes
        # An empty file must always error clearly:
        df2, rep2 = parse_csv(b"", "empty.csv")
        assert df2 is None and rep2.errors


class TestCSVProvider:
    MAPPING = {"spot": "spot", "volatility": "volatility",
               "risk_free_rate": "rate", "dividend_yield": "div",
               "expiry": "expiry"}

    def test_percent_normalization(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [18.5],
                           "rate": [5.25], "div": [1.35],
                           "expiry": ["2026-09-18"]})
        mi = CSVDataProvider(df, self.MAPPING, now=NOW).get_market_inputs()
        assert mi.volatility == pytest.approx(0.185)
        assert mi.risk_free_rate == pytest.approx(0.0525)
        assert mi.dividend_yield == pytest.approx(0.0135)
        assert not mi.dividend_assumed

    def test_decimal_inputs_untouched(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [0.185],
                           "rate": [0.0525], "div": [0.0135],
                           "expiry": ["2026-09-18"]})
        mi = CSVDataProvider(df, self.MAPPING, now=NOW).get_market_inputs()
        assert mi.volatility == pytest.approx(0.185)
        assert mi.risk_free_rate == pytest.approx(0.0525)

    def test_ambiguous_vol_band_warns_but_keeps_decimal(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [1.2],
                           "rate": [0.0525], "div": [0.0135],
                           "expiry": ["2026-09-18"]})
        provider = CSVDataProvider(df, self.MAPPING, now=NOW)
        mi = provider.get_market_inputs()
        assert mi.volatility == pytest.approx(1.2)
        assert any("kept as decimal" in s for s in provider.interpretations)

    def test_missing_dividend_flagged_not_silent(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [0.185],
                           "rate": [0.0525], "expiry": ["2026-09-18"]})
        mapping = {k: v for k, v in self.MAPPING.items()
                   if k != "dividend_yield"}
        provider = CSVDataProvider(df, mapping, now=NOW)
        mi = provider.get_market_inputs()
        assert mi.dividend_yield == 0.0
        assert mi.dividend_assumed
        assert any("q = 0" in s for s in provider.interpretations)

    def test_dte_column(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [0.185],
                           "rate": [0.0525], "div": [0.0135], "dte": [30]})
        mapping = {**{k: v for k, v in self.MAPPING.items() if k != "expiry"},
                   "dte": "dte"}
        mi = CSVDataProvider(df, mapping, now=NOW).get_market_inputs()
        assert mi.expiry == NOW + timedelta(days=30)

    def test_bad_expiry_message(self):
        df = pd.DataFrame({"spot": [6137.0], "volatility": [0.185],
                           "rate": [0.0525], "div": [0.0135],
                           "expiry": ["not-a-date"]})
        with pytest.raises(DataProviderError, match="could not parse expiry"):
            CSVDataProvider(df, self.MAPPING, now=NOW).get_market_inputs()

    def test_non_numeric_spot_message(self):
        df = pd.DataFrame({"spot": ["abc"], "volatility": [0.185],
                           "rate": [0.0525], "div": [0.0135],
                           "expiry": ["2026-09-18"]})
        with pytest.raises(DataProviderError, match="not numeric"):
            CSVDataProvider(df, self.MAPPING, now=NOW).get_market_inputs()


def _excel_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
    return buf.getvalue()


class TestExcelParsing:
    FRAME = pd.DataFrame({"SPX_price": [6137.0], "iv": [18.5],
                          "rf": [5.25], "q": [1.35],
                          "expiration": ["2026-09-18"]})

    def test_parse_excel_and_detect_aliases(self):
        df, rep = parse_excel(_excel_bytes({"Sheet1": self.FRAME}), "in.xlsx")
        assert rep.file_format == "Excel" and rep.sheet_used == "Sheet1"
        assert df is not None and rep.n_rows == 1
        rep = detect_columns(df, rep)
        assert rep.detected["spot"] == "SPX_price"
        assert rep.ok

    def test_multi_sheet_default_and_explicit(self):
        other = pd.DataFrame({"unrelated": [1]})
        content = _excel_bytes({"Notes": other, "Inputs": self.FRAME})
        df, rep = parse_excel(content, "in.xlsx")
        assert rep.sheet_used == "Notes" and rep.sheets == ["Notes", "Inputs"]
        df, rep = parse_excel(content, "in.xlsx", sheet_name="Inputs")
        assert rep.sheet_used == "Inputs" and "SPX_price" in df.columns

    def test_missing_sheet_error(self):
        df, rep = parse_excel(_excel_bytes({"Sheet1": self.FRAME}), "in.xlsx",
                              sheet_name="Nope")
        assert df is None
        assert any("not found" in e for e in rep.errors)

    def test_invalid_bytes_error(self):
        df, rep = parse_excel(b"not an excel file", "bad.xlsx")
        assert df is None and rep.errors

    def test_parse_tabular_dispatch(self):
        _, rep_x = parse_tabular(_excel_bytes({"S": self.FRAME}), "a.XLSX")
        assert rep_x.file_format == "Excel"
        _, rep_c = parse_tabular(b"spot\n6137\n", "a.csv")
        assert rep_c.file_format == "CSV"

    def test_provider_end_to_end_from_excel(self):
        # Excel gives datetime cells for dates; provider must handle them.
        frame = pd.DataFrame({"spot": [6137.0], "volatility": [18.5],
                              "rate": [5.25], "div": [1.35],
                              "expiry": [pd.Timestamp("2026-09-18")]})
        df, rep = parse_excel(_excel_bytes({"Inputs": frame}), "in.xlsx")
        mapping = {"spot": "spot", "volatility": "volatility",
                   "risk_free_rate": "rate", "dividend_yield": "div",
                   "expiry": "expiry"}
        provider = CSVDataProvider(df, mapping, now=NOW, source_label="Excel")
        mi = provider.get_market_inputs()
        assert mi.volatility == pytest.approx(0.185)
        assert mi.expiry == datetime(2026, 9, 18, 16, 0)  # close assumed
        assert mi.source.startswith("Excel")


class TestProviders:
    def test_manual_provider_dividend_assumed(self):
        mi = ManualDataProvider(6137.0, 0.185, 0.0525, None,
                                NOW + timedelta(days=30)).get_market_inputs()
        assert mi.dividend_yield == 0.0 and mi.dividend_assumed

    def test_unconfigured_live_sentinel_refuses(self):
        from data.live_provider import UnconfiguredLiveProvider
        provider = UnconfiguredLiveProvider()
        assert not provider.is_configured()
        with pytest.raises(DataProviderNotConfiguredError,
                           match="Not configured"):
            provider.get_market_inputs()

    def test_live_provider_is_configured_for_yahoo_chart(self):
        provider = LiveMarketDataProvider()
        assert provider.is_configured()


class TestDates:
    def test_same_day_expiry_keeps_intraday_time(self):
        expiry = NOW.replace(hour=16, minute=15)
        T = time_to_expiry(expiry, NOW)
        assert T == pytest.approx((4.25 * 3600) / (365 * 24 * 3600))

    def test_act_360(self):
        expiry = NOW + timedelta(days=36)
        assert time_to_expiry(expiry, NOW, DayCount.ACT_360) == pytest.approx(0.1)

    def test_expired_negative(self):
        assert time_to_expiry(NOW - timedelta(days=1), NOW) < 0

    def test_fractional_days(self):
        expiry = NOW + timedelta(days=1, hours=6)
        assert time_to_expiry(expiry, NOW) == pytest.approx(1.25 / 365)


class TestValidators:
    def test_negative_spot(self):
        res = validate_market_inputs(-5.0, 0.185, 0.05, 0.01, 0.5)
        assert not res.ok and any("Spot" in e for e in res.errors)

    def test_negative_vol_human_message(self):
        res = validate_market_inputs(6137.0, -0.1, 0.05, 0.01, 0.5)
        assert any("greater than or equal to 0%" in e for e in res.errors)

    def test_expired_is_warning_not_error(self):
        res = validate_market_inputs(6137.0, 0.185, 0.05, 0.01, -0.1)
        assert res.ok and res.warnings

    def test_nan_rejected(self):
        res = validate_market_inputs(float("nan"), 0.185, 0.05, 0.01, 0.5)
        assert not res.ok

    def test_chain_config(self):
        assert not validate_chain_config(-25.0, 28).ok
        assert not validate_chain_config(25.0, -1).ok
        assert validate_chain_config(25.0, 28).ok

    def test_chain_config_rejects_non_positive_grid(self):
        res = validate_chain_config(25.0, 28, atm_strike=50.0)
        assert not res.ok
        assert any("non-positive" in e for e in res.errors)

    def test_chain_config_accepts_valid_spx_grid(self):
        res = validate_chain_config(25.0, 28, atm_strike=6125.0)
        assert res.ok


class TestImpliedVol:
    @pytest.mark.parametrize("is_call", [True, False])
    @pytest.mark.parametrize("sigma", [0.08, 0.185, 0.6])
    def test_round_trip(self, is_call, sigma):
        S, K, T, r, q = 6137.0, 6000.0, 0.4, 0.0525, 0.0135
        price = bsm_price(S, K, T, r, q, sigma, is_call)
        iv = implied_volatility(price, S, K, T, r, q, is_call)
        assert iv == pytest.approx(sigma, abs=1e-8)

    def test_arbitrage_violation_rejected(self):
        with pytest.raises(ImpliedVolError, match="no-arbitrage"):
            implied_volatility(-1.0, 6137.0, 6000.0, 0.4, 0.0525, 0.0135, True)

    def test_expired_rejected(self):
        with pytest.raises(ImpliedVolError, match="expired"):
            implied_volatility(100.0, 6137.0, 6000.0, 0.0, 0.0525, 0.0135, True)
