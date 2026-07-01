"""Tests for the adaptive plotting layer (app/plotting.py).

These are pure-logic tests: no Streamlit, no COPASI. We hand the functions small
synthetic **long** frames (the shape ``simulations.run`` returns as its second
element) and assert on the facts they extract and the Plotly figures they build —
structure (trace count, axis data/types), not pixels.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

import plotting


def _kinetics_long(species=("[X]", "[Y]"), times=(0.0, 1.0, 2.0)) -> pd.DataFrame:
    """A 0-D long frame: each species sampled over time (no varying inputs)."""
    rows = [
        {"Time": t, "Model_Output_Type": sp, "Model_Output": float(len(sp) + t)}
        for sp in species
        for t in times
    ]
    return pd.DataFrame(rows)


def _scan_long() -> pd.DataFrame:
    """A 1-D long frame: species '[Z]' over a 3-point scan of input X (a species,
    column ``[X]_0``), with a held input Y as a *constant* column ``[Y]_0``."""
    rows = []
    for x in (0.1, 1.0, 10.0):
        for t in (0.0, 1.0):
            rows.append(
                {"[X]_0": x, "[Y]_0": 5.0, "Time": t,
                 "Model_Output_Type": "[Z]", "Model_Output": x + t}
            )
    return pd.DataFrame(rows)


# --- varying_inputs -----------------------------------------------------------
def test_varying_inputs_picks_only_multi_value_lists():
    scan = {"A": [1.0], "B": [1.0, 2.0, 3.0], "C": [0.1, 1.0]}
    assert plotting.varying_inputs(scan) == ["B", "C"]  # order preserved, A held


def test_varying_inputs_empty_when_all_single():
    assert plotting.varying_inputs({"A": [1.0], "B": [2.0]}) == []


# --- mode (mode -> figure-builder selection) ----------------------------------
def test_mode_maps_varying_count_to_the_right_plot():
    assert plotting.mode({"A": [1.0], "B": [2.0]}) == plotting.MODE_KINETICS   # 0
    assert plotting.mode({"A": [1.0], "B": [1.0, 2.0]}) == plotting.MODE_VS_INPUT  # 1
    assert plotting.mode({"A": [1.0, 2.0], "B": [1.0, 2.0]}) == plotting.MODE_SCATTER  # 2
    assert plotting.mode({"A": [1, 2], "B": [1, 2], "C": [1, 2]}) == plotting.MODE_SCATTER  # 3+


# --- axis_columns -------------------------------------------------------------
def test_axis_columns_keeps_only_non_reserved_that_actually_vary():
    long = _scan_long()
    # [X]_0 varies; [Y]_0 is constant; Time/Model_Output_Type/Model_Output reserved.
    assert plotting.axis_columns(long) == ["[X]_0"]


# --- reduce_final_timepoint ---------------------------------------------------
def test_reduce_final_timepoint_keeps_only_the_last_time():
    reduced = plotting.reduce_final_timepoint(_scan_long())

    assert set(reduced["Time"]) == {1.0}          # only the max time survives
    assert len(reduced) == 3                        # one row per X value


# --- column_for_input ---------------------------------------------------------
def test_column_for_input_matches_a_species_column_by_value():
    long = _scan_long()
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}
    assert plotting.column_for_input(long, scan, "X") == "[X]_0"


def test_column_for_input_matches_a_parameter_column_by_value():
    # Global parameters land in a differently-named column ('Values[P]'); the
    # value-match makes column_for_input naming-agnostic.
    long = pd.DataFrame(
        {"Values[P]": [2.0, 4.0], "Time": [1.0, 1.0],
         "Model_Output_Type": ["[Z]", "[Z]"], "Model_Output": [0.1, 0.2]}
    )
    assert plotting.column_for_input(long, {"P": [2.0, 4.0]}, "P") == "Values[P]"


def test_column_for_input_raises_when_nothing_matches():
    with pytest.raises(KeyError, match="matches input 'Q'"):
        plotting.column_for_input(_scan_long(), {"Q": [7.0, 8.0]}, "Q")


def test_column_for_input_prefers_real_quantity_over_initial_for_shadow():
    # Some COPASI models (e.g. CTCA) drive a parameter from an "Initial for <p>"
    # helper, so scanning it moves BOTH columns with identical values. We want the
    # real one for the axis label.
    rows = [
        {"Values[kL+]": v, "Values[Initial for kL+]": v, "Time": t,
         "Model_Output_Type": "[Z]", "Model_Output": v + t}
        for v in (0.5, 1.0, 2.0)
        for t in (0.0, 1.0)
    ]
    long = pd.DataFrame(rows)
    assert plotting.column_for_input(long, {"kL+": [0.5, 1.0, 2.0]}, "kL+") == "Values[kL+]"


# --- kinetics_figure ----------------------------------------------------------
def test_kinetics_figure_line_has_one_trace_per_species_vs_time():
    long = _kinetics_long(species=("[X]", "[Y]"), times=(0.0, 1.0, 2.0))
    fig = plotting.kinetics_figure(long, plot_type="line")

    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2                       # one trace per species
    assert {tr.name for tr in fig.data} == {"[X]", "[Y]"}
    assert list(fig.data[0].x) == [0.0, 1.0, 2.0]   # x is Time


def test_kinetics_figure_area_still_builds_all_series():
    fig = plotting.kinetics_figure(_kinetics_long(), plot_type="area")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


def test_kinetics_figure_rejects_unknown_plot_type():
    with pytest.raises(ValueError, match="plot_type must be"):
        plotting.kinetics_figure(_kinetics_long(), plot_type="bar")


# --- vs_input_figure ----------------------------------------------------------
def test_vs_input_figure_plots_output_against_the_scanned_column():
    long = _scan_long()  # species '[Z]' over a 3-point scan of X (column '[X]_0')
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}  # X varies, Y held
    fig = plotting.vs_input_figure(long, scan, {"X": "Linear", "Y": "Linear"})

    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1                        # one species
    assert list(fig.data[0].x) == [0.1, 1.0, 10.0]   # x = the scanned input column
    assert fig.layout.xaxis.type != "log"            # linear unless Scale is Log


def test_vs_input_figure_uses_log_x_when_scale_is_log():
    long = _scan_long()
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}
    fig = plotting.vs_input_figure(long, scan, {"X": "Log", "Y": "Linear"})

    assert fig.layout.xaxis.type == "log"            # Log scale ⇒ log x-axis


# --- scatter_figure -----------------------------------------------------------
def _scan2d_long(species=("[Z]",)) -> pd.DataFrame:
    """A 2-D long frame: two scanned inputs (X, Y), sampled over time per species."""
    rows = [
        {"[X]_0": x, "[Y]_0": y, "Time": t,
         "Model_Output_Type": sp, "Model_Output": x * y + t}
        for x in (0.1, 1.0, 10.0)
        for y in (2.0, 4.0)
        for t in (0.0, 1.0)
        for sp in species
    ]
    return pd.DataFrame(rows)


def test_scatter_figure_2d_is_a_flat_scatter_with_a_point_per_combo():
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        _scan2d_long(), scan, {"X": "Linear", "Y": "Linear"}, "X", "Y", "[Z]"
    )
    assert isinstance(fig, go.Figure)
    assert fig.data[0].type == "scatter"             # 2-D (no z)
    assert len(fig.data[0].x) == 6                    # 3 X values × 2 Y values


def test_scatter_figure_3d_adds_a_z_axis():
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        _scan2d_long(), scan, {"X": "Linear", "Y": "Linear"}, "X", "Y", "[Z]",
        three_d=True,
    )
    assert fig.data[0].type == "scatter3d"
    assert fig.data[0].z is not None                  # output on the z-axis too


def test_scatter_figure_logs_each_axis_per_input_scale():
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        _scan2d_long(), scan, {"X": "Log", "Y": "Linear"}, "X", "Y", "[Z]"
    )
    assert fig.layout.xaxis.type == "log"             # X is Log
    assert fig.layout.yaxis.type != "log"             # Y stays linear


def test_scatter_figure_filters_to_the_chosen_output():
    # Two species present; choosing '[Z]' must not also plot '[W]' points.
    long = _scan2d_long(species=("[Z]", "[W]"))
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        long, scan, {"X": "Linear", "Y": "Linear"}, "X", "Y", "[Z]"
    )
    assert len(fig.data[0].x) == 6                    # one species' 6 combos, not 12
