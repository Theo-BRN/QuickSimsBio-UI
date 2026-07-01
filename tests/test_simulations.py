"""Tests for the simulation adapter (app/simulations.py).

Pure-logic tests: ``quicksimsbio`` and basico are monkeypatched so they don't
load COPASI or run a real simulation. They check the two table translations
(``default_input_table`` and ``build_scan_dict_from_table``), the grid maths, and
that ``run`` forwards its args and restores model state. One real-model test at
the end is the regression guard for the shared-model state leak.
"""

import numpy as np
import pandas as pd
import pytest

import simulations


def _species_df(values: dict) -> pd.DataFrame:
    """A stand-in for ``bsc.get_species`` output: index=name, initial_concentration col."""
    return pd.DataFrame({"initial_concentration": pd.Series(values)})


def _params_df(values: dict | None):
    """A stand-in for ``bsc.get_parameters`` (None when a model has no parameters)."""
    return None if values is None else pd.DataFrame({"initial_value": pd.Series(values)})


@pytest.fixture
def stub_model_state(monkeypatch):
    """Stub basico's get/set initial-state calls and record every restore write.

    Lets the hermetic ``get_inputs`` / ``run`` tests work without a real COPASI
    model. Returns the list that restore writes are appended to.
    """
    monkeypatch.setattr(simulations.bsc, "get_species", lambda model=None: _species_df({"X": 3.0}))
    monkeypatch.setattr(simulations.bsc, "get_parameters", lambda model=None: _params_df({"k": 2.0}))
    restored: list[tuple[str, dict]] = []
    monkeypatch.setattr(simulations.bsc, "set_species", lambda **kw: restored.append(("species", kw)))
    monkeypatch.setattr(simulations.bsc, "set_parameters", lambda **kw: restored.append(("params", kw)))
    return restored


def _row(param, value=np.nan, lower=np.nan, upper=np.nan,
         type_=simulations.TYPE_SINGLE, scale=simulations.SCALE_LINEAR, n=np.nan):
    """Build one inputs-table row dict with every column set."""
    return {
        simulations.COL_PARAM: param,
        simulations.COL_VALUE: value,
        simulations.COL_LOWER: lower,
        simulations.COL_UPPER: upper,
        simulations.COL_TYPE: type_,
        simulations.COL_SCALE: scale,
        simulations.COL_N: n,
    }


def _table(*rows) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=simulations.INPUT_COLUMNS)


# --- get_inputs ---------------------------------------------------------------
def test_get_inputs_maps_each_input_to_its_default(monkeypatch, stub_model_state):
    monkeypatch.setattr(
        simulations.qsb, "get_model_inputs", lambda model=None: {"X": None, "k": None}
    )
    # stub_model_state supplies species {"X": 3.0} and parameters {"k": 2.0}.
    assert simulations.get_inputs("MODEL") == {"X": 3.0, "k": 2.0}


# --- default_input_table ------------------------------------------------------
def test_default_input_table_starts_every_input_as_single_at_default():
    table = simulations.default_input_table({"X": 3.0, "k": 2.0})

    assert list(table.columns) == simulations.INPUT_COLUMNS
    assert list(table[simulations.COL_PARAM]) == ["X", "k"]
    assert list(table[simulations.COL_VALUE]) == [3.0, 2.0]
    assert set(table[simulations.COL_TYPE]) == {simulations.TYPE_SINGLE}
    assert set(table[simulations.COL_SCALE]) == {simulations.SCALE_LINEAR}
    assert table[simulations.COL_LOWER].isna().all()  # blank until switched to Grid
    assert table[simulations.COL_N].isna().all()


# --- _grid_values -------------------------------------------------------------
def test_grid_values_linear_is_evenly_spaced():
    assert simulations._grid_values(0, 10, 5, log=False) == [0.0, 2.5, 5.0, 7.5, 10.0]


def test_grid_values_log_is_geometric_in_real_values():
    vals = simulations._grid_values(0.01, 100, 5, log=True)
    assert vals == pytest.approx([0.01, 0.1, 1.0, 10.0, 100.0])


def test_grid_values_rejects_bad_inputs_with_friendly_messages():
    with pytest.raises(ValueError, match="at least 2"):
        simulations._grid_values(0, 10, 1, log=False)
    with pytest.raises(ValueError, match="less than Upper"):
        simulations._grid_values(10, 1, 5, log=False)
    with pytest.raises(ValueError, match="Lower greater than 0"):
        simulations._grid_values(0, 10, 5, log=True)
    with pytest.raises(ValueError, match="Lower, Upper and n"):
        simulations._grid_values(np.nan, 10, 5, log=False)


# --- build_scan_dict_from_table -----------------------------------------------
def test_build_scan_dict_single_becomes_a_one_point_list():
    scan, errors = simulations.build_scan_dict_from_table(_table(_row("A", value=1.0)))

    assert scan == {"A": [1.0]}
    assert errors == {}


def test_build_scan_dict_all_single_each_have_length_one():
    table = _table(_row("A", value=1.0), _row("B", value=2.0))
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert scan == {"A": [1.0], "B": [2.0]}
    assert all(len(v) == 1 for v in scan.values())  # ⇒ one combined parameter set
    assert errors == {}


def test_build_scan_dict_grid_linear_and_log():
    table = _table(
        _row("B", lower=1, upper=4, type_=simulations.TYPE_GRID, n=4),
        _row("C", lower=0.01, upper=100, type_=simulations.TYPE_GRID,
             scale=simulations.SCALE_LOG, n=5),
    )
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert errors == {}
    assert scan["B"] == [1.0, 2.0, 3.0, 4.0]
    assert scan["C"] == pytest.approx([0.01, 0.1, 1.0, 10.0, 100.0])


def test_build_scan_dict_random_is_not_wired_yet():
    table = _table(_row("A", type_=simulations.TYPE_RANDOM, lower=0, upper=1))
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert "A" not in scan
    assert "M4" in errors["A"]


def test_build_scan_dict_single_without_a_value_errors():
    scan, errors = simulations.build_scan_dict_from_table(_table(_row("A", value=np.nan)))

    assert "A" not in scan
    assert "Value" in errors["A"]


def test_build_scan_dict_collects_errors_without_dropping_good_rows():
    table = _table(
        _row("good", value=1.0),
        _row("bad", lower=10, upper=1, type_=simulations.TYPE_GRID, n=3),  # lower >= upper
    )
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert scan == {"good": [1.0]}
    assert "less than Upper" in errors["bad"]


# --- input_scales_from_table --------------------------------------------------
def test_input_scales_from_table_maps_each_input_to_its_scale():
    table = _table(
        _row("A", value=1.0),  # Single ⇒ Linear (the default)
        _row("B", lower=0.1, upper=10, type_=simulations.TYPE_GRID,
             scale=simulations.SCALE_LOG, n=5),
    )
    assert simulations.input_scales_from_table(table) == {
        "A": simulations.SCALE_LINEAR,
        "B": simulations.SCALE_LOG,
    }


# --- make_timepoints ----------------------------------------------------------
def test_make_timepoints_spans_zero_to_end_inclusive():
    tps = simulations.make_timepoints(10, 5)

    assert tps == [0.0, 2.5, 5.0, 7.5, 10.0]
    assert len(tps) == 5
    assert all(isinstance(t, float) for t in tps)  # plain Python floats, not np


# --- run ----------------------------------------------------------------------
def test_run_forwards_args_and_returns_wide_long_pair(monkeypatch, stub_model_state):
    seen = {}
    wide, long = object(), object()  # stand-ins for the two DataFrames

    def fake_run_simulations(
        scan_dict, timepoints=None, format_output=None, model=None, **kwargs
    ):
        seen.update(
            scan_dict=scan_dict, timepoints=timepoints,
            format_output=format_output, model=model,
        )
        return [wide, long]  # the package returns [wide, long] for format_output="both"

    monkeypatch.setattr(simulations.qsb, "run_simulations", fake_run_simulations)
    out = simulations.run("MODEL", {"drug": [1, 2]}, timepoints=[0, 1, 2])

    assert out == (wide, long)  # run unpacks the pair and returns it as a tuple
    assert seen == {
        "scan_dict": {"drug": [1, 2]},
        "timepoints": [0, 1, 2],
        "format_output": "both",
        "model": "MODEL",
    }


def test_run_restores_initial_state_after_running(monkeypatch, stub_model_state):
    monkeypatch.setattr(simulations.qsb, "run_simulations", lambda *a, **k: ["wide", "long"])
    simulations.run("MODEL", {"X": [0.1, 1, 10]})

    # The snapshot ({"X": 3.0} species, {"k": 2.0} param) is written back verbatim.
    assert ("species", {"name": "X", "initial_concentration": 3.0, "model": "MODEL"}) in stub_model_state
    assert ("params", {"name": "k", "initial_value": 2.0, "model": "MODEL"}) in stub_model_state


def test_run_restores_initial_state_even_when_the_run_raises(monkeypatch, stub_model_state):
    def boom(*a, **k):
        raise RuntimeError("simulation failed")

    monkeypatch.setattr(simulations.qsb, "run_simulations", boom)

    with pytest.raises(RuntimeError, match="simulation failed"):
        simulations.run("MODEL", {"X": [0.1, 1, 10]})

    assert stub_model_state  # restore still happened via the finally block


def test_run_restores_model_state_so_held_inputs_stay_at_default():
    # Real integration (no stubs): a scan must not pollute a later default run.
    # This is the regression guard for the shared-model state-leak bug. An empty
    # scan_dict ({}) runs once at the model's defaults ("hold everything").
    path = next(p for p in simulations.bsc.get_examples() if "brusselator" in p.lower())
    model = simulations.bsc.load_model(path)
    timepoints = simulations.make_timepoints(100, 50)

    pristine_wide, _ = simulations.run(model, {}, timepoints)
    simulations.run(model, {"X": [0.1, 1.0, 10.0]}, timepoints)  # mutates initial X
    after_wide, _ = simulations.run(model, {}, timepoints)

    pd.testing.assert_frame_equal(pristine_wide, after_wide)
