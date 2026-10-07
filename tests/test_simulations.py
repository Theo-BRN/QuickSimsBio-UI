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
    monkeypatch.setattr(
        simulations.bsc, "set_species", lambda **kw: restored.append(("species", kw))
    )
    monkeypatch.setattr(
        simulations.bsc, "set_parameters", lambda **kw: restored.append(("params", kw))
    )
    return restored


def _row(
    param,
    value=np.nan,
    lower=np.nan,
    upper=np.nan,
    type_=simulations.TYPE_SINGLE,
    scale=simulations.SCALE_LINEAR,
    n=np.nan,
):
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
        simulations.bsc,
        "add_parameter_set",
        lambda name, model=None: calls.append(("add", name)),
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


# --- slider settings & options ------------------------------------------------
def test_default_slider_settings_span_a_hundredfold_either_way_on_a_log_scale():
    assert simulations.default_slider_settings(90.0) == {
        "min": 0.9,
        "max": 9000.0,
        "log": True,
    }


def test_a_zero_default_starts_on_a_linear_zero_to_one_slider():
    settings = simulations.default_slider_settings(0.0)
    assert settings == {"min": 0.0, "max": 1.0, "log": False}
    options = simulations.slider_options(settings, default=0.0)
    assert options[0] == 0.0 and options[-1] == 1.0  # starts on 0, its default


def test_default_slider_bounds_are_round_numbers_that_survive_display():
    # Real defaults carry float noise; the bounds shouldn't (the settings boxes
    # show 6 significant figures and hand that rounded value back).
    settings = simulations.default_slider_settings(2.9999959316797846)
    assert (settings["min"], settings["max"]) == (0.03, 300.0)


def test_a_negative_default_starts_on_a_linear_scale():
    settings = simulations.default_slider_settings(-2.0)
    assert (settings["min"], settings["max"], settings["log"]) == (-200.0, -0.02, False)


def test_log_slider_has_evenly_spaced_stops_per_decade_and_includes_the_default():
    default = 90.0000110591902  # real defaults carry float noise like this
    options = simulations.slider_options(
        simulations.default_slider_settings(default), default
    )

    assert options[0] == pytest.approx(0.9) and options[-1] == pytest.approx(9000.0)
    assert default in options  # exactly, so the slider can start on it
    # One constant ratio between stops (10^(1/20) ≈ 1.1220): evenly spaced in log.
    # 4 decimals, because stops are rounded to 6 significant figures for display.
    ratios = {
        round(b / a, 4) for a, b in zip(options, options[1:]) if default not in (a, b)
    }
    assert ratios == {round(10 ** (1 / simulations.LOG_STOPS_PER_DECADE), 4)}


def test_linear_slider_has_a_hundred_equal_intervals():
    settings = {"min": 0.0, "max": 10.0, "log": False}
    options = simulations.slider_options(settings, default=5.0)
    assert len(options) == simulations.LINEAR_INTERVALS + 1
    assert options[:3] == [0.0, 0.1, 0.2]  # clean, evenly spaced values


def test_a_default_outside_the_range_is_not_added():
    settings = {"min": 10.0, "max": 20.0, "log": False}
    options = simulations.slider_options(settings, default=3.0)
    assert 3.0 not in options and (options[0], options[-1]) == (10.0, 20.0)


def test_an_extreme_log_range_is_capped_rather_than_crashing():
    # high / low = 1e600 overflows to infinity; this once crashed the app.
    options = simulations.slider_options(
        {"min": 1e-300, "max": 1e300, "log": True}, 1.0
    )
    assert (
        2 <= len(options) <= simulations.MAX_STOPS + 1
    )  # +1: the default, added exactly


_OK = {"min": 0.5, "max": 50.0, "log": True}


@pytest.mark.parametrize(
    "settings, message",
    [
        ({**_OK, "max": float("inf")}, "ordinary numbers"),
        ({**_OK, "min": float("nan")}, "ordinary numbers"),
        ({**_OK, "min": 50.0}, "Min must be below Max"),  # equal
        ({**_OK, "min": 80.0}, "Min must be below Max"),  # above
        (
            {**_OK, "min": 1.0, "max": 1.000000000001},
            "Min must be below Max",
        ),  # can't tell apart
    ],
)
def test_unusable_slider_settings_fall_back_to_the_default_range(settings, message):
    checked, problem = simulations.check_slider_settings(settings, default=5.0)
    assert message in problem
    assert checked == simulations.default_slider_settings(5.0)


@pytest.mark.parametrize("low", [0.0, -5.0])
def test_a_log_scale_without_a_positive_min_switches_to_linear(low):
    checked, problem = simulations.check_slider_settings(
        {**_OK, "min": low}, default=5.0
    )
    assert checked["log"] is False and checked["min"] == low  # their range is kept
    assert "needs Min above 0" in problem


def test_sensible_slider_settings_pass_unchanged():
    assert simulations.check_slider_settings(_OK, default=5.0) == (_OK, "")


def test_nearest_option_uses_a_log_distance_on_log_sliders_only():
    options = [1.0, 10.0, 100.0]
    assert simulations.nearest_option(options, 4.0, log=True) == 10.0  # ×2.5 vs ×4
    assert simulations.nearest_option(options, 4.0, log=False) == 1.0  # 3 vs 6 away


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
        _row(
            "C",
            lower=0.01,
            upper=100,
            type_=simulations.TYPE_GRID,
            scale=simulations.SCALE_LOG,
            n=5,
        ),
    )
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert errors == {}
    assert scan["B"] == [1.0, 2.0, 3.0, 4.0]
    assert scan["C"] == pytest.approx([0.01, 0.1, 1.0, 10.0, 100.0])


def test_build_scan_dict_rejects_an_unknown_type():
    # The UI can only emit Single/Grid (fixed dropdown), but the adapter still
    # guards its `else` branch so a stray type surfaces as a friendly error.
    scan, errors = simulations.build_scan_dict_from_table(
        _table(_row("A", type_="Bogus"))
    )

    assert "A" not in scan
    assert "Unknown type" in errors["A"]


def test_build_scan_dict_single_without_a_value_errors():
    scan, errors = simulations.build_scan_dict_from_table(
        _table(_row("A", value=np.nan))
    )

    assert "A" not in scan
    assert "Value" in errors["A"]


def test_build_scan_dict_collects_errors_without_dropping_good_rows():
    table = _table(
        _row("good", value=1.0),
        _row(
            "bad", lower=10, upper=1, type_=simulations.TYPE_GRID, n=3
        ),  # lower >= upper
    )
    scan, errors = simulations.build_scan_dict_from_table(table)

    assert scan == {"good": [1.0]}
    assert "less than Upper" in errors["bad"]


# --- input_scales_from_table --------------------------------------------------
def test_input_scales_from_table_maps_each_input_to_its_scale():
    table = _table(
        _row("A", value=1.0),  # Single ⇒ Linear (the default)
        _row(
            "B",
            lower=0.1,
            upper=10,
            type_=simulations.TYPE_GRID,
            scale=simulations.SCALE_LOG,
            n=5,
        ),
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
            scan_dict=scan_dict,
            timepoints=timepoints,
            format_output=format_output,
            model=model,
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
    monkeypatch.setattr(
        simulations.qsb, "run_simulations", lambda *a, **k: ["wide", "long"]
    )
    simulations.run("MODEL", {"X": [0.1, 1, 10]})

    # Every input's snapshot value is written back under its EXACT name — and only
    # inputs: the non-input assignment quantity is never touched.
    assert stub_model_state == [
        (
            "params",
            {"name": "k", "exact": True, "initial_value": 2.0, "model": "MODEL"},
        ),
        (
            "species",
            {
                "name": "X",
                "exact": True,
                "initial_concentration": 3.0,
                "model": "MODEL",
            },
        ),
    ]


def test_run_restores_initial_state_even_when_the_run_raises(
    monkeypatch, stub_model_state
):
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

    assert [
        p["name"] for p in simulations.bsc.get_parameter_sets(model=model)
    ] == before


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
    simulations.run(
        model, {"Calcium{compartment[3]}": [5.0, 50.0]}, timepoints
    )  # a scan

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
    assert wide[column].iloc[0] == pytest.approx(
        50.0
    )  # the run starts from the new value
