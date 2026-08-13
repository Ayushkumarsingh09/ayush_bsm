"""Presentation-layer tests: display labels, column mirror, export naming."""

from datetime import datetime

import numpy as np
import pytest

from analytics.chain import ChainConfig, build_option_chain
from pricing.conventions import DEFAULT_CONVENTIONS
from pricing.registry import GREEKS_BY_KEY
from pricing.volatility import ConstantVolatility
from utils.export import build_export_frame, to_csv_bytes
from visualization.option_chain import (BSM_PREMIUM_LABEL, build_display_frame)


def _sample_chain():
    expiry = datetime(2026, 9, 18, 16, 0, 0)
    df, meta = build_option_chain(
        spot=6137.0, T=30 / 365, r=0.0525, q=0.0135,
        vol_provider=ConstantVolatility(0.185),
        cfg=ChainConfig(), expiry=expiry,
    )
    return df, meta


class TestDisplayPremiumLabels:
    def test_bsm_premium_columns_present_and_mirrored(self):
        df, meta = _sample_chain()
        display = build_display_frame(
            df, meta, DEFAULT_CONVENTIONS, ["First Order"])
        cols = list(display.columns)
        assert ("CALL", BSM_PREMIUM_LABEL) in cols
        assert ("PUT", BSM_PREMIUM_LABEL) in cols
        # Premium sits immediately beside the strike block on both sides.
        strike_ix = cols.index(("STRIKE", "Strike"))
        assert cols[strike_ix - 1] == ("CALL", BSM_PREMIUM_LABEL)
        assert cols[strike_ix + 2] == ("PUT", BSM_PREMIUM_LABEL)  # after moneyness
        # Status is outermost on both sides.
        assert cols[0] == ("CALL", "Status")
        assert cols[-1] == ("PUT", "Status")

    def test_put_greeks_mirror_call_order(self):
        df, meta = _sample_chain()
        display = build_display_frame(
            df, meta, DEFAULT_CONVENTIONS, ["First Order"])
        cols = [c[1] for c in display.columns if c[0] == "CALL"
                and c[1] not in ("Status", BSM_PREMIUM_LABEL)]
        put_cols = [c[1] for c in display.columns if c[0] == "PUT"
                    and c[1] not in ("Status", BSM_PREMIUM_LABEL)]
        assert put_cols == list(reversed(cols))

    def test_bsm_premium_matches_analytics(self):
        df, meta = _sample_chain()
        display = build_display_frame(
            df, meta, DEFAULT_CONVENTIONS, ["First Order"])
        assert display[("CALL", BSM_PREMIUM_LABEL)].equals(df["call_bsm_premium"])
        assert display[("PUT", BSM_PREMIUM_LABEL)].equals(df["put_bsm_premium"])


class TestExportNaming:
    def test_export_uses_bsm_premium_names(self):
        df, meta = _sample_chain()
        export = build_export_frame(df, DEFAULT_CONVENTIONS)
        assert "call_bsm_premium" in export.columns
        assert "put_bsm_premium" in export.columns
        assert "call_price" not in export.columns
        assert "bsm_call_value" not in export.columns

    def test_csv_metadata_mentions_theoretical_premium(self):
        df, meta = _sample_chain()
        inputs = dict(sigma=0.185, r=0.0525, q=0.0135, source="Manual",
                      day_count="ACT/365")
        raw = to_csv_bytes(df, meta, inputs, DEFAULT_CONVENTIONS).decode("utf-8")
        assert "call_bsm_premium" in raw
        assert "theoretical" in raw.lower() or "BSM" in raw
        assert "model_version" in raw


class TestColorRegistryDocs:
    def test_color_formula_uses_calendar_time_positive_sign(self):
        spec = GREEKS_BY_KEY["color"]
        assert spec.formula.lstrip().startswith("+")
        assert "calendar time" in spec.definition.lower()


class TestChartSmoke:
    def test_premium_chart_builds_from_bsm_premium_columns(self):
        from visualization.charts import premium_chart
        df, meta = _sample_chain()
        fig = premium_chart(df, meta)
        assert fig.data[0].name == "BSM Call Premium"
        assert fig.data[1].name == "BSM Put Premium"
        assert np.allclose(fig.data[0].y, df["call_bsm_premium"])
