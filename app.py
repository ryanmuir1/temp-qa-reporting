"""
QA Report app — Streamlit entry point.

Run from the project root:
    streamlit run app.py

Pages are built dynamically from the YAML files in configs/. Drop a new
process YAML in there and it appears as a new page on the next rerun.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from engine import (
    EvaluationError,
    bucket_counts,
    discover_configs,
    evaluate_lot,
)
from engine.config import ProcessConfig
from engine.evaluate import FAILED, INCOMPLETE, MARGINAL, PASSED
from engine.plotting import boxplot_for_ctq

CONFIGS_DIR = Path(__file__).parent / "configs"

_BUCKET_LABEL = {
    PASSED: "✅ Passed",
    MARGINAL: "🟡 Marginal",
    FAILED: "❌ Failed",
    INCOMPLETE: "⚪ Incomplete",
}


def _download_button(df: pd.DataFrame, label: str, filename: str):
    csv = df.to_csv(index=False).encode()
    st.download_button(label, data=csv, file_name=filename, mime="text/csv")


def render_process_page(cfg: ProcessConfig):
    st.title(f"{cfg.name} — QA Report")
    if cfg.description:
        st.caption(cfg.description)

    with st.expander("CTQ definitions for this process", expanded=False):
        spec_rows = [{
            "CTQ": c.name, "source": c.source, "unit": c.unit,
            "lower": c.lower, "upper": c.upper, "nominal": c.nominal,
        } for c in cfg.ctqs]
        st.dataframe(pd.DataFrame(spec_rows), use_container_width=True)

    # ---- Controls ----------------------------------------------------------
    c1, c2 = st.columns([3, 2])
    with c1:
        uploaded = st.file_uploader(
            "Upload imaging CSV for this lot", type=["csv"], key=f"up_{cfg.id}"
        )
    with c2:
        tol = st.slider(
            "Marginal tolerance (%)", min_value=0.0, max_value=25.0,
            value=float(cfg.default_tolerance_pct), step=0.5, key=f"tol_{cfg.id}",
            help="A unit that fails a CTQ by no more than this is 'marginal' "
                 "rather than 'failed'.",
        )

    if uploaded is None:
        st.info("Upload a CSV to generate the report.")
        return

    try:
        raw = pd.read_csv(uploaded)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read CSV: {e}")
        return

    try:
        long, summary = evaluate_lot(raw, cfg, tolerance_pct=tol)
    except EvaluationError as e:
        st.error(str(e))
        with st.expander("Columns found in your file"):
            st.write(list(raw.columns))
        return

    counts = bucket_counts(summary)
    total = len(summary)

    # ---- Summary cards -----------------------------------------------------
    st.subheader("Lot summary")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total units", total)
    m2.metric("Passed", counts[PASSED],
              f"{100*counts[PASSED]/total:.1f}%" if total else "")
    m3.metric("Marginal", counts[MARGINAL])
    m4.metric("Failed", counts[FAILED])
    m5.metric("Incomplete", counts[INCOMPLETE])

    # ---- Buckets -----------------------------------------------------------
    st.subheader("Report")
    tabs = st.tabs([
        f"{_BUCKET_LABEL[PASSED]} ({counts[PASSED]})",
        f"{_BUCKET_LABEL[MARGINAL]} ({counts[MARGINAL]})",
        f"{_BUCKET_LABEL[FAILED]} ({counts[FAILED]})",
        f"{_BUCKET_LABEL[INCOMPLETE]} ({counts[INCOMPLETE]})",
    ])
    for tab, bucket in zip(
        tabs, [PASSED, MARGINAL, FAILED, INCOMPLETE]
    ):
        with tab:
            b = summary[summary["bucket"] == bucket]
            cols = ["serial", "sheet", "position"]
            if bucket in (MARGINAL, FAILED):
                cols += ["worst_ctq", "worst_margin_pct", "failed_ctqs"]
            if bucket == INCOMPLETE:
                cols += ["incomplete_ctqs"]
            st.dataframe(b[cols], use_container_width=True, hide_index=True)
            if len(b):
                _download_button(
                    b, f"Download {bucket} list",
                    f"{cfg.id}_{bucket}.csv",
                )

    # ---- Plots -------------------------------------------------------------
    st.subheader("Lot summary plots")
    pc1, pc2 = st.columns([3, 2])
    with pc1:
        chosen = st.multiselect(
            "CTQs to plot", options=[c.name for c in cfg.ctqs],
            default=[cfg.ctqs[0].name], key=f"plot_{cfg.id}",
        )
    with pc2:
        n_sheets = summary["sheet"].nunique()
        group = st.toggle(
            "Split boxes by sheet", value=False, key=f"grp_{cfg.id}",
            help=f"{n_sheets} sheet(s) in this lot.",
        )
    for ctq in cfg.ctqs:
        if ctq.name in chosen:
            st.plotly_chart(
                boxplot_for_ctq(long, ctq, group_by_sheet=group),
                use_container_width=True,
            )

    # ---- Raw detail --------------------------------------------------------
    with st.expander("Per-unit, per-CTQ detail"):
        st.dataframe(long, use_container_width=True, hide_index=True)
        _download_button(long, "Download full detail", f"{cfg.id}_detail.csv")


def main():
    st.set_page_config(page_title="MFG QA Reports", layout="wide")
    configs = discover_configs(CONFIGS_DIR)

    if not configs:
        st.error(
            f"No valid process configs found in {CONFIGS_DIR}. "
            "Add a YAML file (see configs/screen_printing.yaml)."
        )
        return

    pages = [
        st.Page(
            (lambda c=cfg: render_process_page(c)),
            title=cfg.name,
            url_path=cfg.id,
        )
        for cfg in configs
    ]
    st.navigation(pages).run()


if __name__ == "__main__":
    main()
