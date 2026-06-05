"""
Plotly boxplots for lot summaries -- one figure per CTQ.

Each figure shows the distribution of the CTQ's measured values across the lot,
with the spec limits (and nominal) drawn as reference lines and the individual
units overlaid as points coloured by their status. Optionally the boxes are
split by sheet so you can compare sheets within a lot at a glance.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .config import CTQ
from .evaluate import FAILED, INCOMPLETE, MARGINAL, PASSED

_STATUS_COLOR = {
    PASSED: "#2e9e5b",
    MARGINAL: "#e6a700",
    FAILED: "#d6453d",
    INCOMPLETE: "#8a8a8a",
}


def boxplot_for_ctq(
    long: pd.DataFrame,
    ctq: CTQ,
    group_by_sheet: bool = False,
) -> go.Figure:
    sub = long[long["ctq_id"] == ctq.id].copy()
    fig = go.Figure()

    x = sub["sheet"] if group_by_sheet else None

    fig.add_trace(
        go.Box(
            y=sub["value"],
            x=x,
            name=ctq.name,
            boxpoints=False,
            marker_color="#4c78a8",
            line_color="#4c78a8",
            showlegend=False,
        )
    )

    # Overlay individual units, coloured by status.
    for status, color in _STATUS_COLOR.items():
        pts = sub[sub["status"] == status]
        if pts.empty:
            continue
        fig.add_trace(
            go.Scatter(
                y=pts["value"],
                x=(pts["sheet"] if group_by_sheet else [ctq.name] * len(pts)),
                mode="markers",
                marker=dict(color=color, size=6, opacity=0.7),
                name=status,
                text=pts["serial"],
                hovertemplate="%{text}<br>%{y:.4g}<extra>" + status + "</extra>",
            )
        )

    # Spec limit / nominal reference lines.
    for value, label, dash, col in (
        (ctq.lower, "LSL", "dash", "#d6453d"),
        (ctq.upper, "USL", "dash", "#d6453d"),
        (ctq.nominal, "nominal", "dot", "#2e9e5b"),
    ):
        if value is not None:
            fig.add_hline(
                y=value,
                line_dash=dash,
                line_color=col,
                annotation_text=f"{label} = {value:g}",
                annotation_position="right",
            )

    ytitle = ctq.name + (f" ({ctq.unit})" if ctq.unit else "")
    fig.update_layout(
        title=f"{ctq.name} — lot distribution",
        yaxis_title=ytitle,
        xaxis_title="Sheet" if group_by_sheet else "",
        boxmode="group",
        height=420,
        margin=dict(l=60, r=120, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig
