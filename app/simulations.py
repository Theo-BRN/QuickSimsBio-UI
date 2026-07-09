"""Simulation adapter for QuickSimsBio-UI.

This is the **only** module that talks to the ``quicksimsbio`` package (see
CLAUDE.md > Architecture). It adapts UI state into the package's ``scan_dict`` /
argument shapes and returns plain DataFrames, so the rest of the app never
imports ``quicksimsbio`` directly.

Like ``models.py``, this module deliberately does **not** import Streamlit:
keeping it Streamlit-free means every function here is unit-testable without a
running app (the tests monkeypatch ``qsb`` the same way ``test_models`` patches
``basico``).

The UI builds inputs as an editable **table** (one row per model input). This
module owns the two pure translations around it: ``default_input_table`` (model
defaults → starting table) and ``build_scan_dict_from_table`` (edited table →
``scan_dict``). Keeping them here, free of Streamlit, is what makes the core
logic testable.
"""

import basico as bsc
import numpy as np
import pandas as pd
import quicksimsbio as qsb

# --- Inputs-table schema ------------------------------------------------------
# Column names and the cell values for the Type / Scale dropdowns. Defined once
# and shared by ``default_input_table`` and ``build_scan_dict_from_table`` so the
# table the UI shows and the table the adapter reads can never drift apart.
COL_PARAM = "Param"
COL_VALUE = "Value"
COL_LOWER = "Lower"
COL_UPPER = "Upper"
COL_TYPE = "Type"
COL_SCALE = "Scale"
COL_N = "n"
INPUT_COLUMNS = [COL_PARAM, COL_VALUE, COL_LOWER, COL_UPPER, COL_TYPE, COL_SCALE, COL_N]

TYPE_SINGLE = "Single"
TYPE_GRID = "Grid"
INPUT_TYPES = [TYPE_SINGLE, TYPE_GRID]

SCALE_LINEAR = "Linear"
SCALE_LOG = "Log"
INPUT_SCALES = [SCALE_LINEAR, SCALE_LOG]


# --- Model-state helpers ------------------------------------------------------
def _snapshot_initial_state(model) -> tuple[dict, dict]:
    """Capture a model's initial species concentrations and parameter values.

    Returned as two ``{name: value}`` dicts. Used both to read input defaults
    (see ``get_inputs``) and to restore the model after a run (see ``run``).
    ``get_parameters`` returns ``None`` for a model with no global quantities
    (e.g. brusselator), which we treat as "nothing to snapshot".
    """
    species = bsc.get_species(model=model)
    params = bsc.get_parameters(model=model)
    species_state = (
        {} if species is None else species["initial_concentration"].to_dict()
    )
    param_state = {} if params is None else params["initial_value"].to_dict()
    return species_state, param_state


def _restore_initial_state(model, snapshot: tuple[dict, dict]) -> None:
    """Write a snapshot from ``_snapshot_initial_state`` back onto the model."""
    species_state, param_state = snapshot
    for name, value in species_state.items():
        bsc.set_species(name=name, initial_concentration=value, model=model)
    for name, value in param_state.items():
        bsc.set_parameters(name=name, initial_value=value, model=model)


# --- Inputs: defaults → table → scan_dict -------------------------------------
def get_inputs(model) -> dict[str, float]:
    """Return each model input mapped to its current default value.

    The package lists the model's fixed global quantities and initial species as
    inputs; we look up each one's default from basico (the same initial-value
    fields the snapshot/restore guard reads). The UI seeds its inputs table with
    these defaults, so every input starts as a "Single" value at its default.

    ``model`` is passed explicitly rather than relying on basico's "current
    model" so the inputs are never tied to whatever happens to be loaded globally.
    """
    names = qsb.get_model_inputs(model=model)
    species_state, param_state = _snapshot_initial_state(model)
    defaults = {**species_state, **param_state}
    return {name: float(defaults.get(name, np.nan)) for name in names}


def default_input_table(defaults: dict[str, float]) -> pd.DataFrame:
    """Build the starting inputs table from ``{name: default_value}``.

    Every input starts as ``Single`` (held at its default Value); Lower/Upper/n
    are blank (``NaN``) and only matter once a row is switched to ``Grid``. Numeric
    columns are float so ``st.data_editor``'s ``NumberColumn`` shows blanks for the
    unused cells rather than choking on mixed/object dtypes.
    """
    rows = [
        {
            COL_PARAM: name,
            COL_VALUE: value,
            COL_LOWER: np.nan,
            COL_UPPER: np.nan,
            COL_TYPE: TYPE_SINGLE,
            COL_SCALE: SCALE_LINEAR,
            COL_N: np.nan,
        }
        for name, value in defaults.items()
    ]
    return pd.DataFrame(rows, columns=INPUT_COLUMNS)


def _grid_values(lower, upper, n, log: bool) -> list[float]:
    """Build the list of grid points for a ``Grid`` row.

    Linear → ``np.linspace``; Log → ``np.geomspace`` (real, log-spaced points, to
    match how the package interprets log bounds — actual values, not exponents).
    Raises ``ValueError`` with a user-facing message on the cases the UI must
    guard against, so they surface as inline errors next to the table.
    """
    if any(pd.isna(x) for x in (lower, upper, n)):
        raise ValueError("Grid needs Lower, Upper and n.")
    n = int(n)
    if n < 2:
        raise ValueError("n must be at least 2 for a grid.")
    if lower >= upper:
        raise ValueError("Lower must be less than Upper.")
    if log:
        if lower <= 0:
            raise ValueError("Log scans need Lower greater than 0.")
        return np.geomspace(lower, upper, n).tolist()
    return np.linspace(lower, upper, n).tolist()


def build_scan_dict_from_table(table: pd.DataFrame) -> tuple[dict, dict]:
    """Translate the edited inputs table into a ``scan_dict`` (+ per-row errors).

    Each row becomes one ``scan_dict`` entry, with meaning decided by its Type
    (``st.data_editor`` can't disable cells per-row, so unused cells are simply
    ignored here):

    - ``Single`` → ``[value]`` (a one-point "grid" — holds/sets that input).
    - ``Grid``   → a list from ``_grid_values`` (Linear or Log).

    Returns ``(scan_dict, errors)`` where ``errors`` maps an input name to a
    friendly message. The UI shows those and disables Run while any exist, so a
    bad row never reaches the simulator.
    """
    scan_dict: dict[str, list[float]] = {}
    errors: dict[str, str] = {}

    for row in table.to_dict("records"):
        name = row[COL_PARAM]
        input_type = row[COL_TYPE]
        try:
            if input_type == TYPE_SINGLE:
                value = row[COL_VALUE]
                if pd.isna(value):
                    raise ValueError("Single needs a Value.")
                scan_dict[name] = [float(value)]
            elif input_type == TYPE_GRID:
                scan_dict[name] = _grid_values(
                    row[COL_LOWER],
                    row[COL_UPPER],
                    row[COL_N],
                    log=row[COL_SCALE] == SCALE_LOG,
                )
            else:
                raise ValueError(f"Unknown type {input_type!r}.")
        except ValueError as exc:
            errors[name] = str(exc)

    return scan_dict, errors


def input_scales_from_table(table: pd.DataFrame) -> dict[str, str]:
    """Map each input name to its Scale (``"Linear"`` / ``"Log"``) from the table.

    The scan_dict alone can't tell plotting which axes are logarithmic — a Log grid
    is just a plain ``geomspace`` list of real values, indistinguishable from a
    linear one. The "this was Log" flag lives only in the table's Scale column, so
    we lift it out here (keyed by input name) for the plot layer to apply to the
    matching axis. Mirrors ``build_scan_dict_from_table``'s row iteration.
    """
    return {row[COL_PARAM]: row[COL_SCALE] for row in table.to_dict("records")}


# --- Running ------------------------------------------------------------------
def make_timepoints(end: float, n_points: int) -> list[float]:
    """Evenly spaced timepoints from 0 to ``end`` inclusive, ``n_points`` of them.

    Mirrors the package's own default (``np.linspace(0, 1000, 300)``). Returns a
    plain Python list (``.tolist()``) so it stays hashable-by-value for Streamlit
    caching and easy to assert on in tests.
    """
    return np.linspace(0, end, n_points).tolist()


def run(model, scan_dict: dict, timepoints: list[float] | None = None):
    """Run time-course simulations for ``scan_dict``; return ``(wide, long)`` frames.

    Thin wrapper over ``qsb.run_simulations``. Single values run one point each;
    grid lists scan a grid. ``model`` is passed explicitly (not via basico's
    global current-model state).

    We request ``format_output="both"``, so the package hands back ``[wide, long]``
    and we return them as a tuple:

    - **wide** — a ``Time`` column, one column per output species, the varied-input
      columns, and a ``Sim_Num`` column. Human-friendly; drives the results table
      (and, later, the CSV export).
    - **long** — the same data melted to ``Time`` + varied-input columns +
      ``Model_Output_Type`` (which species) + ``Model_Output`` (the value); note it
      has **no** ``Sim_Num``. This is the shape the plot builders consume. The melt
      is done by the package (``auto_melt``), which needs the model handle — so it
      has to come from the package, not a UI-side reshape.

    **Why the snapshot/restore:** ``run_simulations`` sets each scanned input as a
    new *initial* value on the model and never puts it back. Because the app
    reuses one cached COPASI handle across runs, a scan would otherwise leave the
    model dirty — so a later "held" input would silently use the last scanned
    value instead of its true default. We snapshot the initial state and restore
    it in a ``finally`` so every run leaves the model exactly as it found it,
    keeping held-at-default results history-independent.
    """
    snapshot = _snapshot_initial_state(model)
    try:
        wide, long = qsb.run_simulations(
            scan_dict, timepoints=timepoints, format_output="both", model=model
        )
        return wide, long
    finally:
        _restore_initial_state(model, snapshot)
