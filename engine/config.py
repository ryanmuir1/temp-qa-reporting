"""
Load and validate per-process configuration from YAML.

Each process is one YAML file in the configs/ directory. Dropping a new file
in there automatically adds a new page to the app -- no code changes needed.

See configs/screen_printing.yaml for an annotated example.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SerialSpec:
    """How to obtain / parse the serial number (sheet + position)."""
    column: str | None = None              # existing column holding the full serial
    construct_from: list[str] = field(default_factory=list)  # build it from parts
    separator: str = "-"
    # Regex with named groups to split a serial into components for grouping.
    # Default: everything before the last '-' is the sheet, the rest the position.
    parse_regex: str = r"^(?P<sheet>.+)-(?P<position>[^-]+)$"


@dataclass
class Transform:
    name: str
    formula: str


@dataclass
class CTQ:
    name: str
    id: str
    source: str                # a raw column name OR a transform name
    unit: str = ""
    lower: float | None = None
    upper: float | None = None
    nominal: float | None = None
    # Per-CTQ override of how the margin-out-of-spec percentage is computed.
    # One of: 'limit' (default), 'range', 'nominal'. See evaluate.py.
    margin_basis: str | None = None
    # Optional boolean expression; the CTQ is only evaluated on rows where it is
    # true (e.g. "c['Front/Back'] == 'Front'"). Rows where it is false are
    # 'n/a' and excluded from pass/fail, rather than counted as missing data.
    applies_when: str | None = None
    # What to do when out of range:
    #   'reject'  -> Critical-to-Performance. Out beyond tolerance rejects the
    #                product; out within tolerance is 'marginal'.
    #   'flag'    -> diagnostic. Out of range flags to Process Dev, never
    #                rejects the product.
    #   'monitor' -> no spec; value is tracked/plotted only, never disposed.
    disposition: str = "reject"

    def __post_init__(self):
        allowed = {"reject", "flag", "monitor"}
        if self.disposition not in allowed:
            raise ConfigError(
                f"CTQ {self.id!r}: disposition must be one of {sorted(allowed)}, "
                f"got {self.disposition!r}."
            )
        if self.disposition == "monitor":
            return  # monitor metrics need no limits
        if self.lower is None and self.upper is None:
            raise ConfigError(
                f"CTQ {self.id!r} must define at least one of 'lower' / 'upper'."
            )
        if (
            self.lower is not None
            and self.upper is not None
            and self.lower > self.upper
        ):
            raise ConfigError(
                f"CTQ {self.id!r}: lower ({self.lower}) > upper ({self.upper})."
            )


@dataclass
class ProcessConfig:
    name: str
    id: str
    description: str = ""
    serial: SerialSpec = field(default_factory=SerialSpec)
    transforms: list[Transform] = field(default_factory=list)
    ctqs: list[CTQ] = field(default_factory=list)
    # Default marginal tolerance (%) shown in the UI; user can override live.
    default_tolerance_pct: float = 5.0
    # Default basis for margin computation across CTQs.
    margin_basis: str = "limit"
    source_path: Path | None = None

    def ctq_by_id(self, ctq_id: str) -> CTQ:
        for ctq in self.ctqs:
            if ctq.id == ctq_id:
                return ctq
        raise KeyError(ctq_id)


class ConfigError(Exception):
    pass


def _require(d: dict, key: str, ctx: str):
    if key not in d:
        raise ConfigError(f"Missing required key {key!r} in {ctx}.")
    return d[key]


def load_process_config(path: str | Path) -> ProcessConfig:
    path = Path(path)
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping.")

    proc = _require(raw, "process", str(path))
    name = _require(proc, "name", "process")
    pid = _require(proc, "id", "process")

    serial_raw = raw.get("serial_number", {}) or {}
    serial = SerialSpec(
        column=serial_raw.get("column"),
        construct_from=serial_raw.get("construct_from", []) or [],
        separator=serial_raw.get("separator", "-"),
        parse_regex=serial_raw.get(
            "parse_regex", SerialSpec.parse_regex
        ),
    )
    if not serial.column and not serial.construct_from:
        raise ConfigError(
            f"{path}: serial_number needs either 'column' or 'construct_from'."
        )

    transforms = [
        Transform(name=_require(t, "name", "transform"),
                  formula=_require(t, "formula", "transform"))
        for t in (raw.get("transforms", []) or [])
    ]

    ctqs = []
    for c in (raw.get("ctqs", []) or []):
        ctqs.append(CTQ(
            name=_require(c, "name", "ctq"),
            id=_require(c, "id", "ctq"),
            source=_require(c, "source", "ctq"),
            unit=c.get("unit", ""),
            lower=c.get("lower"),
            upper=c.get("upper"),
            nominal=c.get("nominal"),
            margin_basis=c.get("margin_basis"),
            applies_when=c.get("applies_when"),
            disposition=c.get("disposition", "reject"),
        ))
    if not ctqs:
        raise ConfigError(f"{path}: at least one CTQ is required.")

    # Cross-checks: duplicate ids, transform name collisions.
    ids = [c.id for c in ctqs]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{path}: duplicate CTQ ids: {ids}.")
    tnames = [t.name for t in transforms]
    if len(tnames) != len(set(tnames)):
        raise ConfigError(f"{path}: duplicate transform names: {tnames}.")

    return ProcessConfig(
        name=name,
        id=pid,
        description=proc.get("description", ""),
        serial=serial,
        transforms=transforms,
        ctqs=ctqs,
        default_tolerance_pct=float(raw.get("default_tolerance_pct", 5.0)),
        margin_basis=raw.get("margin_basis", "limit"),
        source_path=path,
    )


def discover_configs(configs_dir: str | Path) -> list[ProcessConfig]:
    """Load every *.yaml/*.yml file in a directory, sorted by process name."""
    configs_dir = Path(configs_dir)
    out: list[ProcessConfig] = []
    errors: list[str] = []
    for p in sorted(configs_dir.glob("*.y*ml")):
        try:
            out.append(load_process_config(p))
        except ConfigError as e:
            errors.append(f"{p.name}: {e}")
    if errors:
        # Surface config errors but don't kill the whole app.
        out_attr = dataclasses.field
        del out_attr
        for e in errors:
            print(f"[config error] {e}")
    return out
