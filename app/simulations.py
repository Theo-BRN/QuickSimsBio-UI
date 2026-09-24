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


# --- Inputs: what a model exposes ---------------------------------------------
KIND_PARAMETER = "parameter"
KIND_SPECIES = "species"


def _input_parameter_set(model) -> dict:
    """Snapshot a model's inputs through a temporary COPASI parameter set.

    This is how ``qsb.get_model_inputs`` finds a model's inputs, and we mirror it
    on purpose: a parameter set records every input under the same unique name the
    package uses — including compartment-qualified species such as
    ``Calcium{compartment[0]}``, which basico's plainer ``get_species`` reduces to
    just ``Calcium`` — together with its kind and initial value.

    Known package limitation (2026-09): ``run_simulations`` does not yet recognise
    those compartment-qualified species names, so it ignores them (printing "not
    recognised") and changes to them have no effect on the run.

    The temporary set is removed in ``finally``: the loaded model is cached and
    shared across sessions, so it must be left exactly as we found it.
    """
    name = "quicksimsbio-ui temporary"
    existing = {p["name"] for p in bsc.get_parameter_sets(model=model)}
    while name in existing:
        name += " copy"
    bsc.add_parameter_set(name, model=model)
    try:
        return bsc.get_parameter_sets(name, exact=True, model=model)[0]
    finally:
        bsc.remove_parameter_sets(name, exact=True, model=model)


def _read_inputs(model) -> dict[str, tuple[str, float]]:
    """``{name: (kind, default)}`` for every model input, in the package's order.

    Same selection as ``qsb.get_model_inputs``: fixed global quantities first (an
    assignment or ODE quantity depends on others, so it can't be set), then every
    species at its initial concentration.
    """
    param_set = _input_parameter_set(model)
    inputs = {
        str(name): (KIND_PARAMETER, float(info["value"]))
        for name, info in param_set["Initial Global Quantities"].items()
        if info["simulation_type"] == "fixed"
    }
    for name, info in param_set["Initial Species Values"].items():
        inputs[str(name)] = (KIND_SPECIES, float(info["concentration"]))
    return inputs


def get_inputs(model) -> dict[str, float]:
    """Return each model input mapped to its default value.

    The UI seeds its inputs with these, so every input starts at its default.
    ``model`` is passed explicitly rather than relying on basico's "current
    model" so the inputs are never tied to whatever happens to be loaded globally.
    """
    return {name: default for name, (_kind, default) in _read_inputs(model).items()}


def get_input_kinds(model) -> dict[str, str]:
    """Return each model input mapped to its kind: ``"parameter"`` or ``"species"``.

    The package computes this split internally but returns only the names; the UI
    needs it to label inputs and to pick a newcomer's example of each kind.
    """
    return {name: kind for name, (kind, _default) in _read_inputs(model).items()}


def example_inputs(
    kinds: dict[str, str], show_all_up_to: int = 5, per_kind: int = 3
) -> list[str]:
    """The inputs to pre-select as a newcomer's example, in the model's own order.

    A small model (at most ``show_all_up_to`` inputs) shows every input. A larger
    one shows the first ``per_kind`` of each kind, so the example teaches that
    both parameters and species can be changed.
    """
    if len(kinds) <= show_all_up_to:
        return list(kinds)
    chosen = []
    taken_per_kind: dict[str, int] = {}
    for name, kind in kinds.items():
        if taken_per_kind.get(kind, 0) < per_kind:
            chosen.append(name)
            taken_per_kind[kind] = taken_per_kind.get(kind, 0) + 1
    return chosen


# --- Inputs: time course (values → scan_dict) ---------------------------------
def build_scan_dict_from_values(
    defaults: dict[str, float], changed: dict[str, float]
) -> dict[str, list[float]]:
    """A single-run ``scan_dict``: changed inputs at their new value, the rest at default.

    Every input becomes a one-point list — exactly what an all-``Single`` inputs
    table produces — so the run and plot code downstream sees the shape it always
    has. ``changed`` holds only the inputs the user chose to change; any name in
    it that isn't a model input is ignored.
    """
    return {
        name: [float(changed.get(name, default))] for name, default in defaults.items()
    }


# --- Inputs: general table (defaults → table → scan_dict) ---------------------


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
def make_timepoints(start: float, end: float, n_points: int) -> list[float]:
    """Evenly spaced timepoints from ``start`` to ``end`` inclusive.

    These are the times COPASI **records output at**, not the span it integrates:
    it always integrates from time 0, so a later ``start`` doesn't skip any of the
    model's history — events still fire and equilibration still happens, we just
    don't record the part we don't want.

    That's what makes ``start`` worth having. A model whose drug lands at 1e6 needs
    a run reaching past 1e6, but recording 300 points across 0 -> 1,000,120 spaces
    them ~3,334 apart, so *none* land in the 120 units after the drug — the
    interesting part reads as a flat line. Recording 999,990 -> 1,000,120 instead
    puts all 300 points where the action is.

    Returns a plain Python list (``.tolist()``) so it stays hashable-by-value for
    Streamlit caching and easy to assert on in tests.
    """
    return np.linspace(start, end, n_points).tolist()


def _restore_inputs(model, snapshot: dict[str, tuple[str, float]]) -> None:
    """Write a ``_read_inputs`` snapshot back onto the model, one input at a time.

    Each value goes back under its exact, compartment-qualified name
    (``exact=True``), so ``Calcium{compartment[3]}`` gets its own value back.
    Keying by basico's plain names instead collapsed every ``Calcium`` into one
    value and wrote it to all of them — erasing ``array_1d``'s calcium pulse.
    """
    for name, (kind, value) in snapshot.items():
        if kind == KIND_SPECIES:
            bsc.set_species(
                name=name, exact=True, initial_concentration=value, model=model
            )
        else:
            bsc.set_parameters(name=name, exact=True, initial_value=value, model=model)


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
    value instead of its true default. We snapshot every input's initial value
    (by its exact name — see ``_restore_inputs``) and restore it in a ``finally``,
    so every run leaves the model exactly as it found it, keeping held-at-default
    results history-independent. Only inputs are snapshotted, because inputs are
    all ``run_simulations`` can set.
    """
    snapshot = _read_inputs(model)
    try:
        wide, long = qsb.run_simulations(
            scan_dict, timepoints=timepoints, format_output="both", model=model
        )
        return wide, long
    finally:
        _restore_inputs(model, snapshot)
