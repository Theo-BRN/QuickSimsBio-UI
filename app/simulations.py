"""Simulation adapter for QuickSimsBio-UI.

This is the **only** module that talks to the ``quicksimsbio`` package (see
CLAUDE.md > Architecture). It adapts UI state into the package's ``scan_dict`` /
argument shapes and returns plain DataFrames, so the rest of the app never
imports ``quicksimsbio`` directly.

Like ``models.py``, this module deliberately does **not** import Streamlit:
keeping it Streamlit-free means every function here is unit-testable without a
running app (the tests monkeypatch ``qsb`` the same way ``test_models`` patches
``basico``).

The UI gives each chosen input a slider (or, for the one input being scanned, a
set of evenly spaced values). This module owns the pure maths behind those
controls — slider stops, safe settings, scan values — and the translation into a
``scan_dict``. Keeping it free of Streamlit is what makes that logic testable.
"""

import math

import basico as bsc
import numpy as np
import pandas as pd
import quicksimsbio as qsb

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


# --- Inputs: sliders ----------------------------------------------------------
# Every chosen input gets a slider. Its settings are plain: a minimum, a maximum,
# and log scale or not.
LOG_STOPS_PER_DECADE = 20  # stops per factor of ten on a log slider
LINEAR_INTERVALS = 100  # equal steps from min to max on a linear slider
MAX_STOPS = 1000  # cap, so an extreme log range can't build a huge slider
LOG_MIN_FRACTION = 1e-6  # Min, as a fraction of Max, when log is switched on at 0


def default_slider_settings(default: float) -> dict:
    """Starting settings: 1/100 to 100× the default, on a log scale where possible.

    Inputs span orders of magnitude, so a log scale is the sensible start. A log
    scale needs positive values, so a negative default starts linear instead. A
    default of 0 gives no hint of a sensible range, so it starts at a linear 0 – 1
    for the user to widen in the settings.
    Bounds are rounded to 6 significant figures — what the settings boxes display
    and hand back — so the slider's stops don't shift once those boxes exist.
    """
    if default == 0:
        return {"min": 0.0, "max": 1.0, "log": False}
    low, high = sorted(
        float(f"{bound:.6g}") for bound in (default / 100, default * 100)
    )
    return {"min": low, "max": high, "log": low > 0}


def slider_options(settings: dict, default: float) -> list[float]:
    """The values a slider stops at, from ``settings`` — always including the default.

    Log: ``LOG_STOPS_PER_DECADE`` evenly spaced stops per factor of ten. Linear:
    ``LINEAR_INTERVALS`` equal steps from min to max. Stops are rounded to 6
    significant figures so they display cleanly; the default is added exactly
    (when it lies within range) so the slider can sit on it.
    """
    low, high = settings["min"], settings["max"]
    if settings["log"]:
        # log10(high) - log10(low), not log10(high / low): that ratio can overflow
        # to infinity for an extreme range (1e-300 → 1e300) and crash.
        decades = math.log10(high) - math.log10(low)
        count = min(MAX_STOPS, max(2, round(LOG_STOPS_PER_DECADE * decades) + 1))
        values = np.geomspace(low, high, count)
    else:
        values = np.linspace(low, high, LINEAR_INTERVALS + 1)
    options = {float(f"{value:.6g}") for value in values}
    if low <= default <= high:
        options.add(default)
    return sorted(options)


def check_slider_settings(settings: dict, default: float) -> tuple[dict, str]:
    """Make user-edited slider settings safe. Returns (settings to use, message).

    Anything unusable falls back to something that works, with a plain message
    saying what happened ("" when the settings are fine).
    """
    low, high = settings["min"], settings["max"]
    fallback = default_slider_settings(default)
    if not (math.isfinite(low) and math.isfinite(high)):
        return (
            fallback,
            "Min and Max must be ordinary numbers — using the default range.",
        )
    # Also catches a range too narrow to show: the slider displays 6 significant
    # figures, so bounds that look identical there would give one usable stop.
    if not low < high or f"{low:.6g}" == f"{high:.6g}":
        return fallback, "Min must be below Max — using the default range."
    if settings["log"] and low <= 0:
        return {
            **settings,
            "log": False,
        }, "A log scale needs Min above 0 — using linear."
    return settings, ""


def nearest_option(options: list[float], value: float, log: bool) -> float:
    """The option closest to ``value``, on a log scale for a log slider."""
    if log and value > 0:
        return min(
            options, key=lambda option: abs(math.log10(option) - math.log10(value))
        )
    return min(options, key=lambda option: abs(option - value))


# --- Inputs: scanning one input -----------------------------------------------
# One input at a time can swap its slider for a scan: evenly spaced values across
# the same Min / Max (log or linear, as the slider). Each value is one run, and
# the results are shown as one panel per value.
DEFAULT_SCAN_VALUES = 9  # a 3 x 3 grid of panels
MAX_SCAN_VALUES = 16  # a 4 x 4 grid; beyond that the panels get too small to read


def scan_values(settings: dict, n: int) -> list[float]:
    """The ``n`` values a scanned input runs at, from its (checked) slider settings."""
    return _grid_values(settings["min"], settings["max"], n, log=settings["log"])


def _grid_values(lower, upper, n, log: bool) -> list[float]:
    """``n`` evenly spaced points from ``lower`` to ``upper`` inclusive.

    Linear → ``np.linspace``; Log → ``np.geomspace`` (real, log-spaced points, to
    match how the package interprets log bounds — actual values, not exponents).
    Raises ``ValueError`` with a plain message on input that can't make a grid.
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


# --- Inputs: chosen values → scan_dict ----------------------------------------


def build_scan_dict_from_values(
    defaults: dict[str, float], chosen: dict[str, list[float]]
) -> dict[str, list[float]]:
    """The run's ``scan_dict``: each chosen input at its values, the rest at default.

    ``chosen`` maps an input to the values it runs at: one value for a slider, or
    the scan's values for the input being scanned. Every other input is held at
    its default as a one-point list, so every entry has the same shape. Any name
    in ``chosen`` that isn't a model input is ignored.
    """
    return {
        name: [float(value) for value in chosen.get(name, [default])]
        for name, default in defaults.items()
    }


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
