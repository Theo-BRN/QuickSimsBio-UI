"""Simulation adapter for QuickSimsBio-UI.

This is the **only** module that talks to the ``quicksimsbio`` package (see
CLAUDE.md > Architecture). It adapts UI state into the package's ``scan_dict`` /
argument shapes and returns plain DataFrames, so the rest of the app never
imports ``quicksimsbio`` directly.

Like ``models.py``, this module deliberately does **not** import Streamlit:
keeping it Streamlit-free means every function here is unit-testable without a
running app (the tests monkeypatch ``qsb`` the same way ``test_models`` patches
``basico``).
"""

import basico as bsc
import numpy as np

import quicksimsbio as qsb


def get_inputs(model) -> dict[str, object]:
    """Return the ``scan_dict`` skeleton for ``model``: ``{input_name: None}``.

    The package extracts the model's fixed global quantities and initial species
    and lists them as inputs, each holding at its default (``None``). The UI then
    overrides the ones the user wants to scan (see ``build_scan_dict``).

    We pass ``model`` explicitly rather than relying on basico's "current model"
    so a run is never tied to whatever model happens to be loaded globally.
    """
    return qsb.get_model_inputs(model=model)


def parse_grid_values(text: str) -> list[float] | None:
    """Parse a comma-separated string of numbers into a grid list of floats.

    This is the per-input adapter for the **grid** mode: the user types values
    like ``"0.1, 1, 10"`` and we turn them into ``[0.1, 1.0, 10.0]`` for the
    ``scan_dict``. Blank / whitespace-only input (and a string of only commas)
    means "hold at default", so we return ``None``. Stray empty tokens from a
    trailing or double comma are tolerated.

    Raises ``ValueError`` with a user-facing message if a token isn't a number,
    so the UI can show it via ``st.error`` (same friendly-message pattern as the
    model-loading code in ``main.py``).
    """
    if not text or not text.strip():
        return None

    values: list[float] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue  # tolerate "1, 2," or "1,,2"
        try:
            values.append(float(token))
        except ValueError:
            raise ValueError(
                f"'{token}' isn't a number — enter comma-separated values, "
                f"e.g. 0.1, 1, 10."
            )

    return values or None


def build_scan_dict(skeleton: dict, overrides: dict) -> dict:
    """Combine the all-``None`` input skeleton with the user's overrides.

    ``skeleton`` is ``get_inputs(model)`` output (every input → ``None``).
    ``overrides`` maps the inputs the user chose to scan to their value
    (currently a grid ``list``; a range ``dict`` will slot in unchanged once that
    UI lands). Inputs not overridden — or overridden with ``None`` (e.g. the user
    picked "scan" but left the box blank) — stay held at default.

    An override key that isn't a real model input raises ``ValueError`` — this
    catches typos and stale UI state rather than silently scanning nothing.
    """
    unknown = set(overrides) - set(skeleton)
    if unknown:
        raise ValueError(f"Unknown input(s): {', '.join(sorted(unknown))}")

    scan = {name: None for name in skeleton}
    scan.update({name: value for name, value in overrides.items() if value is not None})
    return scan


def make_timepoints(end: float, n_points: int) -> list[float]:
    """Evenly spaced timepoints from 0 to ``end`` inclusive, ``n_points`` of them.

    Mirrors the package's own default (``np.linspace(0, 1000, 300)``). Returns a
    plain Python list (``.tolist()``) so it stays hashable-by-value for Streamlit
    caching and easy to assert on in tests.
    """
    return np.linspace(0, end, n_points).tolist()


def _snapshot_initial_state(model) -> tuple[dict, dict]:
    """Capture a model's initial species concentrations and parameter values.

    Returned as two ``{name: value}`` dicts so a run can put them back afterwards
    (see ``run``). ``get_parameters`` returns ``None`` for a model with no global
    quantities (e.g. brusselator), which we treat as "nothing to snapshot".
    """
    species = bsc.get_species(model=model)
    params = bsc.get_parameters(model=model)
    species_state = {} if species is None else species["initial_concentration"].to_dict()
    param_state = {} if params is None else params["initial_value"].to_dict()
    return species_state, param_state


def _restore_initial_state(model, snapshot: tuple[dict, dict]) -> None:
    """Write a snapshot from ``_snapshot_initial_state`` back onto the model."""
    species_state, param_state = snapshot
    for name, value in species_state.items():
        bsc.set_species(name=name, initial_concentration=value, model=model)
    for name, value in param_state.items():
        bsc.set_parameters(name=name, initial_value=value, model=model)


def run(model, scan_dict: dict, timepoints: list[float] | None = None):
    """Run time-course simulations for ``scan_dict`` and return a wide DataFrame.

    Thin wrapper over ``qsb.run_simulations``. An all-``None`` ``scan_dict`` runs
    a single simulation at the model's defaults; lists scan a grid. ``model`` is
    passed explicitly (not via basico's global current-model state).

    The returned DataFrame is wide: a ``Time`` column, one column per output
    species, the varied-input columns, and a ``Sim_Num`` column identifying each
    parameter set.

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
        return qsb.run_simulations(scan_dict, timepoints=timepoints, model=model)
    finally:
        _restore_initial_state(model, snapshot)
