"""
Safe-ish vectorized formula evaluation.

Formulas are authored in the process YAML files by your QA/engineering team
(a trusted source), but we still strip Python builtins so a typo can't do
anything dangerous and only a whitelist of numpy functions is exposed.

Reference columns in a formula by their bare name if the name is a valid
Python identifier, e.g.   raw_gain * cal_factor
Otherwise use the c[] accessor for names with spaces/symbols, e.g.
    (c["Measured Voltage"] - offset) / slope

Everything is vectorized over the dataframe (operations act on whole columns),
so transforms are fast even on large lots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Whitelisted numpy functions available inside formulas.
_ALLOWED_FUNCS = {
    name: getattr(np, name)
    for name in (
        "sqrt", "log", "log10", "log2", "exp", "abs", "absolute",
        "sin", "cos", "tan", "arcsin", "arccos", "arctan", "arctan2",
        "floor", "ceil", "round", "sign", "clip", "where",
        "minimum", "maximum", "power", "mod",
        "mean", "median", "std", "var", "nanmean", "nanmedian",
    )
    if hasattr(np, name)
}
_ALLOWED_CONSTS = {"pi": np.pi, "e": np.e, "nan": np.nan, "inf": np.inf}


class FormulaError(Exception):
    """Raised when a formula references an unknown name or fails to evaluate."""


def evaluate_formula(formula: str, df: pd.DataFrame) -> pd.Series:
    """Evaluate a single formula against a dataframe, returning a Series.

    `df` should already contain any prerequisite transform columns.
    """
    # Namespace: every column accessible by bare name (when it's a valid
    # identifier) and always via the c[] dict accessor.
    column_accessor = {col: df[col] for col in df.columns}

    namespace: dict = {}
    namespace.update(_ALLOWED_CONSTS)
    namespace.update(_ALLOWED_FUNCS)
    namespace["c"] = column_accessor
    namespace["np"] = np
    # Bare-name access for clean column names.
    for col in df.columns:
        if col.isidentifier():
            namespace[col] = df[col]

    try:
        result = eval(formula, {"__builtins__": {}}, namespace)  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        raise FormulaError(f"Failed to evaluate formula {formula!r}: {exc}") from exc

    # Coerce scalars/arrays to a Series aligned with the dataframe.
    if np.isscalar(result):
        result = pd.Series(result, index=df.index)
    elif isinstance(result, np.ndarray):
        result = pd.Series(result, index=df.index)
    elif not isinstance(result, pd.Series):
        result = pd.Series(result, index=df.index)

    return pd.to_numeric(result, errors="coerce")
