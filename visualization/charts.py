"""Interactive Plotly charts: premiums, Greeks-vs-strike, Greek heatmap.

All chart values are in *display units* (per-day theta, per-vol-point
vega, ...) so charts and table always agree.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from analytics.chain import ChainMeta
from pricing.conventions import GreekConventions
from pricing.registry import GREEKS_BY_KEY
from visualization.option_chain import scaled_greek

CALL_COLOR = "#26a65b"
PUT_COLOR = "#e0555f"
SPOT_COLOR = "#4aa8ff"
ATM_COLOR = "#ffc400"

_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(17,20,24,1)",
    font=dict(size=12),
    margin=dict(l=50, r=20, t=48, b=40),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)


def _add_markers(fig: go.Figure, meta: ChainMeta) -> None:
    fig.add_vline(x=meta.spot, line_dash="dot", line_color=SPOT_COLOR,
                  annotation_text=f"Spot {meta.spot:,.0f}",
                  annotation_font_color=SPOT_COLOR)
    fig.add_vline(x=meta.atm_strike, line_dash="dash", line_color=ATM_COLOR,
                  annotation_text=f"ATM {meta.atm_strike:,.0f}",
                  annotation_position="bottom right",
                  annotation_font_color=ATM_COLOR)


def premium_chart(df: pd.DataFrame, meta: ChainMeta) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=df["strike"], y=df["call_bsm_premium"],
                    name="BSM Call Premium",
                    line=dict(color=CALL_COLOR, width=2))
    fig.add_scatter(x=df["strike"], y=df["put_bsm_premium"],
                    name="BSM Put Premium",
                    line=dict(color=PUT_COLOR, width=2))
    _add_markers(fig, meta)
    fig.update_layout(
        title="BSM Theoretical Premium vs Strike (not market quotes)",
        xaxis_title="Strike",
        yaxis_title="BSM Premium (index points)",
        **_LAYOUT)
    return fig


def greek_chart(df: pd.DataFrame, meta: ChainMeta, key: str,
                conventions: GreekConventions) -> go.Figure:
    spec = GREEKS_BY_KEY[key]
    fig = go.Figure()
    if spec.per_side:
        fig.add_scatter(x=df["strike"],
                        y=scaled_greek(df, key, "call", conventions),
                        name=f"Call {spec.label}",
                        line=dict(color=CALL_COLOR, width=2))
        fig.add_scatter(x=df["strike"],
                        y=scaled_greek(df, key, "put", conventions),
                        name=f"Put {spec.label}",
                        line=dict(color=PUT_COLOR, width=2))
    else:
        fig.add_scatter(x=df["strike"],
                        y=scaled_greek(df, key, "call", conventions),
                        name=spec.label,
                        line=dict(color=SPOT_COLOR, width=2))
    _add_markers(fig, meta)
    fig.update_layout(title=f"{spec.label} vs Strike",
                      xaxis_title="Strike",
                      yaxis_title=f"{spec.label} ({spec.display_unit})",
                      **_LAYOUT)
    return fig


def scenario_pnl_chart(scen: pd.DataFrame) -> go.Figure:
    """Bar chart of ATM call/put premium changes by scenario."""
    fig = go.Figure()
    names = scen["scenario"].tolist()
    fig.add_bar(name="Δ Call BSM", x=names, y=scen["d_call"],
                marker_color=CALL_COLOR)
    fig.add_bar(name="Δ Put BSM", x=names, y=scen["d_put"],
                marker_color=PUT_COLOR)
    fig.update_layout(title="ATM premium change by scenario",
                      barmode="group", xaxis_title="Scenario",
                      yaxis_title="Δ Premium (index pts)", **_LAYOUT)
    return fig


def heatmap_chart(df: pd.DataFrame, meta: ChainMeta, keys: list[str],
                  side: str, conventions: GreekConventions) -> go.Figure:
    """Strike x Greek heatmap.

    Each Greek row is normalized by its own max absolute value so rows with
    very different scales remain comparable; hover shows true display-unit
    values.
    """
    z_rows, hover_rows, labels = [], [], []
    for key in keys:
        spec = GREEKS_BY_KEY[key]
        vals = scaled_greek(df, key, side, conventions).to_numpy()
        max_abs = np.nanmax(np.abs(vals)) if np.any(np.isfinite(vals)) else 1.0
        z_rows.append(vals / max_abs if max_abs > 0 else vals)
        hover_rows.append(vals)
        labels.append(spec.label)

    fig = go.Figure(go.Heatmap(
        z=z_rows,
        x=df["strike"],
        y=labels,
        customdata=np.array(hover_rows),
        colorscale="RdBu",
        zmid=0.0,
        colorbar=dict(title="Normalized"),
        hovertemplate=("Strike %{x:,.0f}<br>%{y}: %{customdata:.6f}"
                       "<extra></extra>"),
    ))
    fig.add_vline(x=meta.atm_strike, line_dash="dash", line_color=ATM_COLOR)
    fig.update_layout(
        title=f"Greek Sensitivity Heatmap ({side.upper()} side, "
              "each Greek normalized by its own max |value|)",
        xaxis_title="Strike", **_LAYOUT)
    return fig
