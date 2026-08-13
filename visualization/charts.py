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


def vol_smile_chart(df: pd.DataFrame, spot: float,
                    title: str = "Implied Volatility Smile") -> go.Figure:
    """Plot market / model IV vs strike for a single expiry."""
    fig = go.Figure()
    if "market_iv" in df.columns and df["market_iv"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["market_iv"] * 100.0,
            name="Market IV", mode="lines+markers",
            line=dict(color=SPOT_COLOR, width=2),
            marker=dict(size=5),
        )
    if "call_iv" in df.columns and df["call_iv"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["call_iv"] * 100.0,
            name="Call IV", mode="markers",
            marker=dict(color=CALL_COLOR, size=6, symbol="circle-open"),
        )
    if "put_iv" in df.columns and df["put_iv"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["put_iv"] * 100.0,
            name="Put IV", mode="markers",
            marker=dict(color=PUT_COLOR, size=6, symbol="diamond-open"),
        )
    if "sigma" in df.columns and df["sigma"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["sigma"] * 100.0,
            name="Pricing σ", mode="lines",
            line=dict(color=ATM_COLOR, width=2, dash="dot"),
        )
    fig.add_vline(x=spot, line_dash="dot", line_color=SPOT_COLOR,
                  annotation_text=f"Spot {spot:,.0f}")
    fig.update_layout(
        title=title,
        xaxis_title="Strike",
        yaxis_title="Implied Vol (%)",
        **_LAYOUT,
    )
    return fig


def vol_surface_chart(surface: pd.DataFrame,
                      title: str = "Implied Volatility Surface") -> go.Figure:
    """3D surface / mesh of IV vs strike and tenor from long-form points."""
    if surface is None or surface.empty:
        fig = go.Figure()
        fig.update_layout(title="No surface points", **_LAYOUT)
        return fig

    # Pivot to grid for surface plot
    s = surface.dropna(subset=["strike", "T", "market_iv"]).copy()
    s["T_days"] = s["T"] * 365.0
    strikes = np.sort(s["strike"].unique())
    tenors = np.sort(s["T"].unique())
    # Build Z via nearest-neighbour on (K,T) scatter for a regular grid
    k_grid = np.linspace(strikes.min(), strikes.max(), min(60, len(strikes)))
    t_grid = tenors
    Z = np.full((len(t_grid), len(k_grid)), np.nan)
    for i, t in enumerate(t_grid):
        slice_ = s[np.isclose(s["T"], t, atol=1e-8)]
        if slice_.empty:
            continue
        order = np.argsort(slice_["strike"].to_numpy())
        Z[i, :] = np.interp(
            k_grid,
            slice_["strike"].to_numpy()[order],
            slice_["market_iv"].to_numpy()[order] * 100.0,
        )
    fig = go.Figure(data=[go.Surface(
        x=k_grid, y=t_grid * 365.0, z=Z,
        colorscale="Viridis",
        colorbar=dict(title="IV %"),
        hovertemplate=("K=%{x:,.0f}<br>T=%{y:.1f}d<br>IV=%{z:.2f}%"
                       "<extra></extra>"),
    )])
    layout_kwargs = {k: v for k, v in _LAYOUT.items()
                     if k not in {"hovermode", "margin"}}
    fig.update_layout(
        title=title,
        scene=dict(
            xaxis_title="Strike",
            yaxis_title="Tenor (days)",
            zaxis_title="IV (%)",
            bgcolor="rgba(17,20,24,1)",
        ),
        margin=dict(l=10, r=10, t=48, b=10),
        **layout_kwargs,
    )
    return fig


def bid_ask_premium_chart(df: pd.DataFrame, meta: ChainMeta) -> go.Figure:
    """Overlay market mid vs BSM premium when quotes exist."""
    fig = go.Figure()
    fig.add_scatter(x=df["strike"], y=df["call_bsm_premium"],
                    name="Call BSM", line=dict(color=CALL_COLOR, width=2))
    fig.add_scatter(x=df["strike"], y=df["put_bsm_premium"],
                    name="Put BSM", line=dict(color=PUT_COLOR, width=2))
    if "call_market_mid" in df.columns and df["call_market_mid"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["call_market_mid"],
            name="Call Mid", mode="markers",
            marker=dict(color=CALL_COLOR, size=6, symbol="x"),
        )
    if "put_market_mid" in df.columns and df["put_market_mid"].notna().any():
        fig.add_scatter(
            x=df["strike"], y=df["put_market_mid"],
            name="Put Mid", mode="markers",
            marker=dict(color=PUT_COLOR, size=6, symbol="x"),
        )
    _add_markers(fig, meta)
    fig.update_layout(
        title="Market Mid vs BSM Premium",
        xaxis_title="Strike",
        yaxis_title="Premium (index points)",
        **_LAYOUT,
    )
    return fig
