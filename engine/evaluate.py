"""
Core evaluation engine.

Pipeline for an uploaded lot dataframe + a ProcessConfig:
  1. Resolve the serial number column (existing or constructed) and parse it
     into sheet / position components.
  2. Compute all transforms (in order; later transforms may use earlier ones).
  3. For each CTQ, pull its source series, check against limits, and compute
     how far out of spec it is (a margin %).
  4. Bucket each unit (serial) into passed / marginal / failed / incomplete.

Two dataframes come out:
  - `long`  : one row per (serial, CTQ) -- feeds the boxplots and detail tables.
  - `summary`: one row per serial with its bucket + worst CTQ + worst margin.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .config import CTQ, ProcessConfig
from .transforms import FormulaError, evaluate_condition, evaluate_formula

PASSED = "passed"
MARGINAL = "marginal"
FAILED = "failed"
INCOMPLETE = "incomplete"
NA = "n/a"


class EvaluationError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Serial handling
# --------------------------------------------------------------------------- #
def resolve_serials(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    """Return df with guaranteed 'serial', 'sheet', 'position' columns."""
    df = df.copy()
    spec = cfg.serial

    if spec.column:
        if spec.column not in df.columns:
            raise EvaluationError(
                f"Serial column {spec.column!r} not found. "
                f"Available columns: {list(df.columns)}"
            )
        serial = df[spec.column].astype(str)
    else:
        missing = [c for c in spec.construct_from if c not in df.columns]
        if missing:
            raise EvaluationError(
                f"Cannot construct serial; missing columns {missing}."
            )
        serial = (
            df[spec.construct_from]
            .astype(str)
            .agg(spec.separator.join, axis=1)
        )

    df["serial"] = serial.str.strip()

    # Parse into components for grouping/plots.
    pattern = re.compile(spec.parse_regex)
    sheets, positions = [], []
    for s in df["serial"]:
        m = pattern.match(s)
        if m:
            gd = m.groupdict()
            sheets.append(gd.get("sheet", ""))
            positions.append(gd.get("position", ""))
        else:
            sheets.append("")
            positions.append("")
    df["sheet"] = sheets
    df["position"] = positions
    return df


# --------------------------------------------------------------------------- #
# Transforms
# --------------------------------------------------------------------------- #
def apply_transforms(df: pd.DataFrame, cfg: ProcessConfig) -> pd.DataFrame:
    df = df.copy()
    for t in cfg.transforms:
        try:
            df[t.name] = evaluate_formula(t.formula, df)
        except FormulaError as e:
            raise EvaluationError(f"Transform {t.name!r}: {e}") from e
    return df


def _resolve_source(df: pd.DataFrame, ctq: CTQ) -> pd.Series:
    if ctq.source in df.columns:
        return pd.to_numeric(df[ctq.source], errors="coerce")
    raise EvaluationError(
        f"CTQ {ctq.id!r}: source {ctq.source!r} is neither a column nor a "
        f"transform. Available: {list(df.columns)}"
    )


# --------------------------------------------------------------------------- #
# Margin computation
# --------------------------------------------------------------------------- #
def _margin_pct(value, ctq: CTQ, basis: str) -> float:
    """Percent out of spec for a single value. 0.0 if in spec; NaN if value NaN.

    basis:
      'limit'   -> exceedance relative to the violated limit value
      'range'   -> exceedance relative to the spec width (upper-lower)
      'nominal' -> exceedance relative to the nominal value
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan

    L, U = ctq.lower, ctq.upper
    exceed = 0.0
    violated_limit = None
    if U is not None and value > U:
        exceed = value - U
        violated_limit = U
    elif L is not None and value < L:
        exceed = L - value
        violated_limit = L
    if exceed <= 0:
        return 0.0

    use_basis = ctq.margin_basis or basis
    denom = None
    if use_basis == "range" and L is not None and U is not None and U != L:
        denom = abs(U - L)
    elif use_basis == "nominal" and ctq.nominal not in (None, 0):
        denom = abs(ctq.nominal)
    else:  # 'limit' (default) and fallbacks
        if violated_limit not in (None, 0):
            denom = abs(violated_limit)
        elif L is not None and U is not None and U != L:
            denom = abs(U - L)
        elif ctq.nominal not in (None, 0):
            denom = abs(ctq.nominal)

    if not denom:
        return float("inf")  # can't normalize -> treat as a hard fail
    return 100.0 * exceed / denom


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def evaluate_lot(
    raw_df: pd.DataFrame,
    cfg: ProcessConfig,
    tolerance_pct: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate an uploaded lot. Returns (long_df, summary_df)."""
    df = resolve_serials(raw_df, cfg)
    df = apply_transforms(df, cfg)

    records = []
    for ctq in cfg.ctqs:
        series = _resolve_source(df, ctq)
        if ctq.applies_when:
            try:
                applicable = evaluate_condition(ctq.applies_when, df)
            except FormulaError as e:
                raise EvaluationError(
                    f"CTQ {ctq.id!r} applies_when: {e}"
                ) from e
        else:
            applicable = pd.Series(True, index=df.index)
        for idx in df.index:
            v = series.loc[idx]
            if not bool(applicable.loc[idx]):
                status = NA
                margin = np.nan
            else:
                margin = _margin_pct(v, ctq, cfg.margin_basis)
                if np.isnan(v):
                    status = INCOMPLETE
                elif margin == 0.0:
                    status = PASSED
                elif margin <= tolerance_pct:
                    status = MARGINAL
                else:
                    status = FAILED
            records.append({
                "serial": df.at[idx, "serial"],
                "sheet": df.at[idx, "sheet"],
                "position": df.at[idx, "position"],
                "ctq_id": ctq.id,
                "ctq": ctq.name,
                "unit": ctq.unit,
                "value": v,
                "lower": ctq.lower,
                "upper": ctq.upper,
                "nominal": ctq.nominal,
                "margin_pct": margin,
                "status": status,
            })

    long = pd.DataFrame.from_records(records)

    # Roll up to one bucket per serial: worst CTQ outcome wins.
    summary_rows = []
    for serial, grp_all in long.groupby("serial", sort=False):
        grp = grp_all[grp_all["status"] != NA]
        if grp.empty:
            bucket = INCOMPLETE
        elif (grp["status"] == INCOMPLETE).any():
            bucket = INCOMPLETE
        elif (grp["status"] == FAILED).any():
            bucket = FAILED
        elif (grp["status"] == MARGINAL).any():
            bucket = MARGINAL
        else:
            bucket = PASSED

        failing = grp[grp["margin_pct"] > 0]
        if len(failing):
            worst = failing.loc[failing["margin_pct"].idxmax()]
            worst_ctq = worst["ctq"]
            worst_margin = float(worst["margin_pct"])
        else:
            worst_ctq = ""
            worst_margin = 0.0

        failed_ctqs = sorted(
            set(grp.loc[grp["status"].isin([FAILED, MARGINAL]), "ctq"])
        )
        incomplete_ctqs = sorted(
            set(grp.loc[grp["status"] == INCOMPLETE, "ctq"])
        )
        summary_rows.append({
            "serial": serial,
            "sheet": grp_all["sheet"].iloc[0],
            "position": grp_all["position"].iloc[0],
            "bucket": bucket,
            "worst_ctq": worst_ctq,
            "worst_margin_pct": worst_margin,
            "failed_ctqs": ", ".join(failed_ctqs),
            "incomplete_ctqs": ", ".join(incomplete_ctqs),
        })

    summary = pd.DataFrame.from_records(summary_rows)
    return long, summary


def bucket_counts(summary: pd.DataFrame) -> dict[str, int]:
    counts = {PASSED: 0, MARGINAL: 0, FAILED: 0, INCOMPLETE: 0}
    if len(summary):
        vc = summary["bucket"].value_counts().to_dict()
        counts.update({k: int(v) for k, v in vc.items()})
    return counts
