# MFG QA Report App

A Streamlit app for turning per-unit imaging CSVs from the line into pass/fail
QA reports, with configurable CTQs, marginal-yield buckets, and lot-summary
boxplots. One page per process, each defined entirely by a YAML file.

## Quick start

```bash
cd qa_app
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
streamlit run app.py
```

A page appears for each YAML in `configs/`. Pick a process, upload a CSV, and
the report renders.

## How it works

```
CSV  ─►  resolve serial (sheet + position)
     ─►  compute transforms (in order)
     ─►  check each CTQ against its limits, measure % out of spec
     ─►  bucket each unit:
            passed      every CTQ in spec
            marginal    fails a CTQ, but worst miss ≤ tolerance %
            failed      worst miss  > tolerance %
            incomplete  a CTQ value was missing / unevaluable
     ─►  report lists (downloadable) + boxplots per CTQ
```

The marginal tolerance is a live slider in the UI (defaults from the YAML), so
QA can loosen it during development without touching config.

## Adding a process

Copy `configs/screen_printing.yaml`, rename it, edit the fields. Schema:

```yaml
process:
  name: <display name>          # required — page title
  id: <slug>                    # required — unique, used in the URL
  description: <text>           # optional

serial_number:                  # serial = sheet + position, e.g. Z32R-968-A1
  column: SerialNumber          # use an existing column, OR
  # construct_from: [Sheet, Position]   # build it from parts
  # separator: "-"
  parse_regex: "^(?P<sheet>.+)-(?P<position>[^-]+)$"   # split into components

default_tolerance_pct: 5.0      # default marginal band (UI overrides live)
margin_basis: limit             # limit | range | nominal (see below)

transforms:                     # optional derived values, computed in order
  - name: thickness_um
    formula: "(raw_thickness_v - offset_v) / slope_um_per_v"
  - name: gain
    formula: "raw_gain * gain_cal_factor"

ctqs:                           # at least one required
  - name: Print Thickness
    id: print_thickness
    source: thickness_um        # a raw column OR a transform name
    unit: "um"
    lower: 12.0                 # at least one of lower/upper required
    upper: 18.0
    nominal: 15.0               # optional, drawn on plots
    # margin_basis: range       # optional per-CTQ override
```

### Formulas

Vectorized over the whole column. Reference a column by **bare name** if it's a
valid identifier (`raw_gain * cal_factor`); otherwise use the **`c[]` accessor**
(`(c["Measured Voltage"] - offset) / slope`). Available numpy functions:
`sqrt log log10 log2 exp abs clip where minimum maximum power mod sin cos tan`
… and constants `pi`, `e`. Python builtins are stripped — formulas come from
trusted config files authored by your team, but this keeps a typo harmless.

### Margin basis — what "% out of spec" means

When a unit misses a limit, the miss is normalized to a percentage:

| basis     | denominator                       |
|-----------|-----------------------------------|
| `limit`   | the violated limit value (default)|
| `range`   | spec width (`upper − lower`)      |
| `nominal` | the nominal value                 |

Example: thickness 19.0 µm against an upper limit of 18.0 → miss = 1.0.
With `limit` basis that's `1.0 / 18.0 = 5.56%`, so at a 5% tolerance it lands in
**failed**; at 6% it would be **marginal**.

## Project layout

```
qa_app/
├── app.py                      # Streamlit UI, dynamic per-process pages
├── engine/
│   ├── config.py               # YAML load + validation (dataclasses)
│   ├── transforms.py           # safe vectorized formula evaluation
│   ├── evaluate.py             # serials, CTQ checks, margins, bucketing
│   └── plotting.py             # per-CTQ lot boxplots (plotly)
├── configs/
│   └── screen_printing.yaml    # example — EDIT with your real columns
├── requirements.txt
└── README.md
```

## Notes / next steps

- The example YAML uses **placeholder column names**. Swap them for the real
  columns from your CSV.
- `incomplete` is a deliberate fourth bucket for units with a missing/unparseable
  measurement, so they're never silently counted as passes.
- Boxplots can be split by sheet for sheet-to-sheet comparison within a lot.
