"""Tests for the simulation adapter (app/simulations.py).

Pure-logic tests: ``quicksimsbio`` and basico are monkeypatched so they don't
load COPASI or run a real simulation. They check the two table translations
(``default_input_table`` and ``build_scan_dict_from_table``), the grid maths, and
that ``run`` forwards its args and restores model state. One real-model test at
the end is the regression guard for the shared-model state leak.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import simulations


@pytest.fixture
def stub_model_state(monkeypatch, stub_parameter_sets):
    """Stub reading a model's inputs and record every restore write.

    Reading goes through ``stub_parameter_sets`` (parameter ``k`` = 2.0, species
    ``X`` = 3.0, plus a non-input assignment quantity), so the hermetic ``run``
    tests work without a real COPASI model. Returns the list that restore writes
    are appended to.
    """
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


# --- reading a model's inputs -------------------------------------------------
_FAKE_PARAMETER_SET = {
    "Initial Global Quantities": {
        "k": {"value": 2.0, "simulation_type": "fixed"},
        "k_assigned": {"value": 9.0, "simulation_type": "assignment"},  # not an input
    },
    "Initial Species Values": {"X": {"concentration": 3.0}},
}


@pytest.fixture
def stub_parameter_sets(monkeypatch):
    """Stub basico's parameter-set calls with one fake set; record add/remove.

    ``get_parameter_sets(model=…)`` lists existing sets (none here); called with a
    name it returns the freshly "added" set.
    """
    calls: list[tuple[str, str]] = []

    def get_parameter_sets(name=None, exact=False, model=None):
        return [] if name is None else [_FAKE_PARAMETER_SET]

    monkeypatch.setattr(simulations.bsc, "get_parameter_sets", get_parameter_sets)
    monkeypatch.setattr(
        simulations.bsc, "add_parameter_set", lambda name, model=None: calls.append(("add", name))
    )
    monkeypatch.setattr(
        simulations.bsc,
        "remove_parameter_sets",
        lambda name, exact=False, model=None: calls.append(("remove", name)),
    )
    return calls


def test_get_inputs_lists_fixed_parameters_then_species_at_their_defaults(
    stub_parameter_sets,
):
    inputs = simulations.get_inputs("MODEL")

    assert inputs == {"k": 2.0, "X": 3.0}  # the assignment quantity is not an input
    assert list(inputs) == ["k", "X"]  # the package's order: parameters, then species


def test_get_input_kinds_labels_each_input(stub_parameter_sets):
    assert simulations.get_input_kinds("MODEL") == {
        "k": simulations.KIND_PARAMETER,
        "X": simulations.KIND_SPECIES,
    }


def test_reading_inputs_removes_its_temporary_parameter_set(stub_parameter_sets):
    simulations.get_inputs("MODEL")

    (add, name), (remove, same_name) = stub_parameter_sets
    assert (add, remove) == ("add", "remove") and name == same_name


def test_reading_inputs_removes_the_temporary_set_even_when_reading_fails(
    monkeypatch, stub_parameter_sets
):
    def broken_read(name=None, exact=False, model=None):
        if name is None:
            return []
        raise RuntimeError("COPASI hiccup")

    monkeypatch.setattr(simulations.bsc, "get_parameter_sets", broken_read)

    with pytest.raises(RuntimeError):
        simulations.get_inputs("MODEL")
    assert [call for call, _ in stub_parameter_sets] == ["add", "remove"]


# --- example_inputs -----------------------------------------------------------
_P, _S = simulations.KIND_PARAMETER, simulations.KIND_SPECIES


def test_example_inputs_shows_every_input_of_a_small_model():
    kinds = {"k1": _P, "A": _S, "B": _S}
    assert simulations.example_inputs(kinds) == ["k1", "A", "B"]


def test_example_inputs_shows_the_first_few_of_each_kind_in_model_order():
    kinds = {f"k{i}": _P for i in range(5)} | {f"S{i}": _S for i in range(5)}
    assert simulations.example_inputs(kinds) == ["k0", "k1", "k2", "S0", "S1", "S2"]


def test_example_inputs_of_a_large_single_kind_model_shows_just_the_first_few():
    kinds = {f"S{i}": _S for i in range(10)}
    assert simulations.example_inputs(kinds) == ["S0", "S1", "S2"]


# --- fold_options / nearest_fold ----------------------------------------------
def test_fold_options_steps_one_two_five_through_the_default_range():
    assert simulations.fold_options(*simulations.DEFAULT_FOLD_RANGE) == [
        0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0
    ]


def test_fold_options_are_exact_round_numbers():
    # Built from strings, so no 0.020000000000000004-style float noise.
    assert 0.02 in simulations.fold_options(0.01, 1)
    assert 0.0005 in simulations.fold_options(1e-4, 1e-3)


def test_fold_options_respect_a_narrowed_range():
    assert simulations.fold_options(0.5, 20) == [0.5, 1.0, 2.0, 5.0, 10.0, 20.0]


def test_every_bound_option_is_on_the_scale():
    bounds = simulations.FOLD_BOUND_OPTIONS
    assert bounds[0] == 1e-4 and bounds[-1] == 1e4
    assert set(simulations.fold_options(*simulations.DEFAULT_FOLD_RANGE)) <= set(bounds)


def test_nearest_fold_measures_distance_on_a_log_scale():
    options = [0.1, 1.0, 10.0]
    assert simulations.nearest_fold(options, 3.0) == 1.0  # 3 is nearer 1 than 10 in ratio terms
    assert simulations.nearest_fold(options, 4.0) == 10.0
    assert simulations.nearest_fold(options, 1.0) == 1.0


# --- input_step ---------------------------------------------------------------
@pytest.mark.parametrize(
    "default, step",
    [(100.0, 10.0), (1e-12, 1e-13), (-2.0, 0.2), (0.0, 0.1)],
)
def test_input_step_is_a_tenth_of_the_default_whatever_its_size(default, step):
    assert simulations.input_step(default) == pytest.approx(step)


# --- build_scan_dict_from_values ----------------------------------------------
def test_build_scan_dict_from_values_holds_unchanged_inputs_at_default():
    defaults = {"k": 2.0, "X": 3.0, "Y": 4.0}
    scan_dict = simulations.build_scan_dict_from_values(defaults, {"X": 0.5})

    assert scan_dict == {"k": [2.0], "X": [0.5], "Y": [4.0]}


def test_build_scan_dict_from_values_ignores_names_that_are_not_inputs():
    scan_dict = simulations.build_scan_dict_from_values({"X": 3.0}, {"nope": 1.0})
    assert scan_dict == {"X": [3.0]}


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


def test_build_scan_dict_rejects_an_unknown_type():
    # The UI can only emit Single/Grid (fixed dropdown), but the adapter still
    # guards its `else` branch so a stray type surfaces as a friendly error.
    scan, errors = simulations.build_scan_dict_from_table(_table(_row("A", type_="Bogus")))

    assert "A" not in scan
    assert "Unknown type" in errors["A"]


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
def test_make_timepoints_spans_start_to_end_inclusive():
    tps = simulations.make_timepoints(0, 10, 5)

    assert tps == [0.0, 2.5, 5.0, 7.5, 10.0]
    assert len(tps) == 5
    assert all(isinstance(t, float) for t in tps)  # plain Python floats, not np


def test_make_timepoints_records_only_a_late_window():
    # The point of `start`: put every recorded point around a late event rather
    # than smearing them across the equilibration that precedes it.
    tps = simulations.make_timepoints(999_990, 1_000_110, 5)

    assert tps == [999_990.0, 1_000_020.0, 1_000_050.0, 1_000_080.0, 1_000_110.0]


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

    # Every input's snapshot value is written back under its EXACT name — and only
    # inputs: the non-input assignment quantity is never touched.
    assert stub_model_state == [
        ("params", {"name": "k", "exact": True, "initial_value": 2.0, "model": "MODEL"}),
        ("species", {"name": "X", "exact": True, "initial_concentration": 3.0, "model": "MODEL"}),
    ]


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
    timepoints = simulations.make_timepoints(0, 100, 50)

    pristine_wide, _ = simulations.run(model, {}, timepoints)
    simulations.run(model, {"X": [0.1, 1.0, 10.0]}, timepoints)  # mutates initial X
    after_wide, _ = simulations.run(model, {}, timepoints)

    pd.testing.assert_frame_equal(pristine_wide, after_wide)


# --- reading inputs from real models (no stubs) --------------------------------
# brusselator: species only · turing_base: parameters + species · array_1d: the
# same species in many compartments ("Calcium{compartment[0]}", …) — the case
# where basico's plain get_species names are wrong and every default went NaN.
def _example_model(stem: str):
    path = next(p for p in simulations.bsc.get_examples() if Path(p).stem == stem)
    return simulations.bsc.load_model(path)


@pytest.mark.parametrize("stem", ["brusselator", "turing_base", "array_1d"])
def test_real_model_inputs_match_the_package_and_all_have_defaults(stem):
    model = _example_model(stem)
    inputs = simulations.get_inputs(model)

    # Our copy of the package's input logic must agree with it exactly — names
    # AND order — so the app and the package always mean the same inputs.
    assert list(inputs) == list(simulations.qsb.get_model_inputs(model=model))
    assert not any(np.isnan(value) for value in inputs.values())
    assert list(simulations.get_input_kinds(model)) == list(inputs)


def test_real_model_reading_inputs_leaves_no_parameter_set_behind():
    model = _example_model("turing_base")
    before = [p["name"] for p in simulations.bsc.get_parameter_sets(model=model)]

    simulations.get_inputs(model)
    simulations.get_input_kinds(model)

    assert [p["name"] for p in simulations.bsc.get_parameter_sets(model=model)] == before


def test_real_model_kinds_label_parameters_and_species():
    kinds = simulations.get_input_kinds(_example_model("turing_base"))

    assert kinds["v1"] == simulations.KIND_PARAMETER
    assert kinds["S1"] == simulations.KIND_SPECIES


def test_run_leaves_every_multi_compartment_default_unchanged():
    # Regression guard for the multi-compartment leak. All 20 of array_1d's
    # compartments hold a species basico calls plain "Calcium"; a restore keyed by
    # those names kept ONE value and wrote it to all of them, flattening the 0.2
    # pulse in compartment 3 on every run — even a plain default one.
    model = _example_model("array_1d")
    before = simulations.get_inputs(model)
    assert before["Calcium{compartment[3]}"] == pytest.approx(0.2)  # the pulse
    timepoints = simulations.make_timepoints(0, 10, 5)

    simulations.run(model, {}, timepoints)  # a plain default run
    simulations.run(model, {"Calcium{compartment[3]}": [5.0, 50.0]}, timepoints)  # a scan

    assert simulations.get_inputs(model) == pytest.approx(before)


def test_run_applies_a_changed_compartment_qualified_species():
    # The package used to ignore species named like "Calcium{compartment[3]}"
    # (printing "not recognised"), so changing one silently did nothing. Fixed in
    # QuickSimsBio 2026-09-29; this guards it from the app's side.
    model = _example_model("array_1d")
    target = "Calcium{compartment[3]}"
    held = {name: [value] for name, value in simulations.get_inputs(model).items()}
    timepoints = simulations.make_timepoints(0, 1000, 50)

    wide, _ = simulations.run(model, {**held, target: [50.0]}, timepoints)

    column = next(c for c in wide.columns if target in str(c))
    assert wide[column].iloc[0] == pytest.approx(50.0)  # the run starts from the new value
