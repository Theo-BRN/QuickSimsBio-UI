"""Tests for the adaptive plotting layer (app/plotting.py).

These are pure-logic tests: no Streamlit, no COPASI. We hand the functions small
synthetic **long** frames (the shape ``simulations.run`` returns as its second
element) and assert on the facts they extract and the Plotly figures they build —
structure (trace count, axis data/types), not pixels.
"""

import pandas as pd
import plotly.graph_objects as go
import plotting
import pytest


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
                {
                    "[X]_0": x,
                    "[Y]_0": 5.0,
                    "Time": t,
                    "Model_Output_Type": "[Z]",
                    "Model_Output": x + t,
                }
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
    assert plotting.mode({"A": [1.0], "B": [2.0]}) == plotting.MODE_KINETICS  # 0
    assert plotting.mode({"A": [1.0], "B": [1.0, 2.0]}) == plotting.MODE_VS_INPUT  # 1
    assert (
        plotting.mode({"A": [1.0, 2.0], "B": [1.0, 2.0]}) == plotting.MODE_SCATTER
    )  # 2
    assert (
        plotting.mode({"A": [1, 2], "B": [1, 2], "C": [1, 2]}) == plotting.MODE_SCATTER
    )  # 3+


# --- axis_columns -------------------------------------------------------------
def test_axis_columns_keeps_only_non_reserved_that_actually_vary():
    long = _scan_long()
    # [X]_0 varies; [Y]_0 is constant; Time/Model_Output_Type/Model_Output reserved.
    assert plotting.axis_columns(long) == ["[X]_0"]


# --- reduce_final_timepoint ---------------------------------------------------
def test_reduce_final_timepoint_keeps_only_the_last_time():
    reduced = plotting.reduce_final_timepoint(_scan_long())

    assert set(reduced["Time"]) == {1.0}  # only the max time survives
    assert len(reduced) == 3  # one row per X value


# --- column_for_input ---------------------------------------------------------
def test_column_for_input_matches_a_species_column_by_value():
    long = _scan_long()
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}
    assert plotting.column_for_input(long, scan, "X") == "[X]_0"


def test_column_for_input_matches_a_parameter_column_by_value():
    # Global parameters land in a differently-named column ('Values[P]'); the
    # value-match makes column_for_input naming-agnostic.
    long = pd.DataFrame(
        {
            "Values[P]": [2.0, 4.0],
            "Time": [1.0, 1.0],
            "Model_Output_Type": ["[Z]", "[Z]"],
            "Model_Output": [0.1, 0.2],
        }
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
        {
            "Values[kL+]": v,
            "Values[Initial for kL+]": v,
            "Time": t,
            "Model_Output_Type": "[Z]",
            "Model_Output": v + t,
        }
        for v in (0.5, 1.0, 2.0)
        for t in (0.0, 1.0)
    ]
    long = pd.DataFrame(rows)
    assert (
        plotting.column_for_input(long, {"kL+": [0.5, 1.0, 2.0]}, "kL+")
        == "Values[kL+]"
    )


def test_column_for_input_disambiguates_two_inputs_over_identical_ranges():
    # Two inputs scanned over the SAME range have identical value-sets, so a
    # value-match alone can't tell their columns apart (both would grab the first,
    # collapsing x and y onto one axis). We disambiguate by name, so each input
    # resolves to its own column.
    rows = [
        {
            "Values[kG+]": a,
            "Values[kG-]": b,
            "Time": t,
            "Model_Output_Type": "[Z]",
            "Model_Output": a + b + t,
        }
        for a in (0.5, 1.0, 2.0)
        for b in (0.5, 1.0, 2.0)
        for t in (0.0, 1.0)
    ]
    long = pd.DataFrame(rows)
    scan = {"kG+": [0.5, 1.0, 2.0], "kG-": [0.5, 1.0, 2.0]}

    assert plotting.column_for_input(long, scan, "kG+") == "Values[kG+]"
    assert (
        plotting.column_for_input(long, scan, "kG-") == "Values[kG-]"
    )  # distinct axis


# --- kinetics_figure ----------------------------------------------------------
def test_kinetics_figure_line_has_one_trace_per_species_vs_time():
    long = _kinetics_long(species=("[X]", "[Y]"), times=(0.0, 1.0, 2.0))
    fig = plotting.kinetics_figure(long, plot_type="line")

    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2  # one trace per species
    assert {tr.name for tr in fig.data} == {"[X]", "[Y]"}
    assert list(fig.data[0].x) == [0.0, 1.0, 2.0]  # x is Time


def test_kinetics_figure_area_still_builds_all_series():
    fig = plotting.kinetics_figure(_kinetics_long(), plot_type="area")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


def test_kinetics_figure_rejects_unknown_plot_type():
    with pytest.raises(ValueError, match="plot_type must be"):
        plotting.kinetics_figure(_kinetics_long(), plot_type="bar")


# --- colours & showing some outputs --------------------------------------------
def _colours(fig) -> dict[str, str]:
    """Each trace's colour by species (a line's colour, or a marker's)."""
    return {tr.name: tr.line.color or tr.marker.color for tr in fig.data}


def test_colour_map_assigns_colours_by_name_and_wraps_the_palette():
    names = [f"[S{i}]" for i in range(len(plotting.CUSTOM_COLOURS) + 1)]
    colours = plotting.colour_map(names)
    assert colours["[S0]"] == plotting.CUSTOM_COLOURS[0]
    assert colours[names[-1]] == plotting.CUSTOM_COLOURS[0]  # wraps around


def test_shown_limits_which_species_are_drawn():
    long = _kinetics_long(species=("[X]", "[Y]", "[Z]"))
    fig = plotting.kinetics_figure(long, plot_type="line", shown=["[X]", "[Z]"])
    assert {tr.name for tr in fig.data} == {"[X]", "[Z]"}


@pytest.mark.parametrize("plot_type", ["line", "scatter", "area"])
def test_hiding_a_species_never_recolours_the_others(plot_type):
    long = _kinetics_long(species=("[X]", "[Y]", "[Z]"))
    everything = _colours(plotting.kinetics_figure(long, plot_type=plot_type))
    without_x = _colours(
        plotting.kinetics_figure(long, plot_type=plot_type, shown=["[Y]", "[Z]"])
    )
    assert without_x == {name: everything[name] for name in ("[Y]", "[Z]")}


@pytest.mark.parametrize(
    "background, text",
    [
        ("#FFFF00", "#000000"),  # yellow: black reads better
        ("#D4D4D4", "#000000"),  # light grey
        ("#90BFF9", "#000000"),  # pale blue
        ("#0000FF", "#FFFFFF"),  # blue: white reads better
        ("#000000", "#FFFFFF"),
        ("#610051", "#FFFFFF"),  # dark purple
    ],
)
def test_text_colour_on_picks_whichever_reads_better(background, text):
    assert plotting.text_colour_on(background) == text


def test_every_palette_colour_gets_a_readable_text_colour():
    for colour in plotting.CUSTOM_COLOURS:
        assert plotting.text_colour_on(colour) in ("#000000", "#FFFFFF")


def test_legend_entry_is_filled_when_shown_and_a_faded_outline_when_hidden():
    shown = plotting.legend_entry_css("st-key-legend-0", "#0000FF", shown=True)
    hidden = plotting.legend_entry_css("st-key-legend-0", "#0000FF", shown=False)

    assert ".st-key-legend-0 button" in shown and "background: #0000FF" in shown
    assert "color: #FFFFFF" in shown  # white on blue
    assert "dashed #0000FF" in hidden and "opacity" in hidden
    assert ".st-key-legend-0 button:hover" in hidden  # no flicker on hover


def test_species_outputs_are_labelled_as_species_concentration():
    fig = plotting.kinetics_figure(_kinetics_long(species=("[X]", "[Y]")))
    assert fig.layout.yaxis.title.text == "Species concentration"


def test_non_species_outputs_get_a_neutral_label_not_a_wrong_one():
    labels = plotting.value_labels(["[X]", "Values[k1]"])
    assert labels[plotting.COL_OUTPUT] == "Output value"


def test_a_species_has_the_same_colour_in_line_and_area_plots():
    long = _kinetics_long(species=("[X]", "[Y]", "[Z]"))
    assert _colours(plotting.kinetics_figure(long, plot_type="line")) == _colours(
        plotting.kinetics_figure(long, plot_type="area")
    )


# --- vs_input_figure ----------------------------------------------------------
def test_vs_input_figure_plots_output_against_the_scanned_column():
    long = _scan_long()  # species '[Z]' over a 3-point scan of X (column '[X]_0')
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}  # X varies, Y held
    fig = plotting.vs_input_figure(long, scan, {"X": "Linear", "Y": "Linear"})

    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1  # one species
    assert list(fig.data[0].x) == [0.1, 1.0, 10.0]  # x = the scanned input column
    assert fig.layout.xaxis.type != "log"  # linear unless Scale is Log


def test_vs_input_figure_uses_log_x_when_scale_is_log():
    long = _scan_long()
    scan = {"X": [0.1, 1.0, 10.0], "Y": [5.0]}
    fig = plotting.vs_input_figure(long, scan, {"X": "Log", "Y": "Linear"})

    assert fig.layout.xaxis.type == "log"  # Log scale ⇒ log x-axis


# --- scatter_figure -----------------------------------------------------------
def _scan2d_long(species=("[Z]",)) -> pd.DataFrame:
    """A 2-D long frame: two scanned inputs (X, Y), sampled over time per species."""
    rows = [
        {
            "[X]_0": x,
            "[Y]_0": y,
            "Time": t,
            "Model_Output_Type": sp,
            "Model_Output": x * y + t,
        }
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
    assert fig.data[0].type == "scatter"  # 2-D (no z)
    assert len(fig.data[0].x) == 6  # 3 X values × 2 Y values


def test_scatter_figure_3d_adds_a_z_axis():
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        _scan2d_long(),
        scan,
        {"X": "Linear", "Y": "Linear"},
        "X",
        "Y",
        "[Z]",
        three_d=True,
    )
    assert fig.data[0].type == "scatter3d"
    assert fig.data[0].z is not None  # output on the z-axis too


def test_scatter_figure_logs_each_axis_per_input_scale():
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        _scan2d_long(), scan, {"X": "Log", "Y": "Linear"}, "X", "Y", "[Z]"
    )
    assert fig.layout.xaxis.type == "log"  # X is Log
    assert fig.layout.yaxis.type != "log"  # Y stays linear


def test_scatter_figure_filters_to_the_chosen_output():
    # Two species present; choosing '[Z]' must not also plot '[W]' points.
    long = _scan2d_long(species=("[Z]", "[W]"))
    scan = {"X": [0.1, 1.0, 10.0], "Y": [2.0, 4.0]}
    fig = plotting.scatter_figure(
        long, scan, {"X": "Linear", "Y": "Linear"}, "X", "Y", "[Z]"
    )
    assert len(fig.data[0].x) == 6  # one species' 6 combos, not 12
