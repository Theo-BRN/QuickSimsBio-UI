"""Tests for the adaptive plotting layer (app/plotting.py).

These are pure-logic tests: no Streamlit, no COPASI. We hand the functions small
synthetic **long** frames (the shape ``simulations.run`` returns as its second
element) and assert on the facts they extract and the Plotly figures they build —
structure (trace count, axis data/types), not pixels.
"""

import re

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
def test_mode_is_kinetics_with_nothing_scanned_and_facets_with_one_input():
    assert plotting.mode({"A": [1.0], "B": [2.0]}) == plotting.MODE_KINETICS
    assert plotting.mode({"A": [1.0], "B": [1.0, 2.0]}) == plotting.MODE_FACETS


def test_mode_refuses_two_scanned_inputs():
    # Only one input is scanned at a time, by design; two would be a bug.
    with pytest.raises(ValueError, match="one input"):
        plotting.mode({"A": [1.0, 2.0], "B": [1.0, 2.0]})


# --- axis_columns -------------------------------------------------------------
def test_axis_columns_keeps_only_non_reserved_that_actually_vary():
    long = _scan_long()
    # [X]_0 varies; [Y]_0 is constant; Time/Model_Output_Type/Model_Output reserved.
    assert plotting.axis_columns(long) == ["[X]_0"]


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


# --- facet_figure -------------------------------------------------------------
def _facet_long(x_values, species=("[Z]", "[W]")) -> pd.DataFrame:
    """A scan of input X (column ``[X]_0``): every species over time, per X value."""
    rows = [
        {
            "[X]_0": x,
            "Time": t,
            "Model_Output_Type": sp,
            "Model_Output": x * len(sp) + t,
        }
        for x in x_values
        for t in (0.0, 1.0, 2.0)
        for sp in species
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("n, columns", [(1, 1), (2, 2), (4, 2), (5, 3), (9, 3), (16, 4)])
def test_facet_columns_make_the_squarest_grid(n, columns):
    assert plotting.facet_columns(n) == columns


def test_facet_figure_draws_one_panel_per_scanned_value():
    x_values = [0.01, 0.1, 1.0, 10.0, 100.0]
    fig = plotting.facet_figure(_facet_long(x_values), {"X": x_values, "Y": [5.0]})

    # In reading order (top row first, left to right), not Plotly's list order.
    on_screen = sorted(fig.layout.annotations, key=lambda a: (-a.y, a.x))
    titles = [a.text for a in on_screen]
    assert titles == ["X = 0.01", "X = 0.1", "X = 1", "X = 10", "X = 100"]
    assert len(fig.data) == 5 * 2  # one trace per species, per panel
    assert all(list(trace.x) == [0.0, 1.0, 2.0] for trace in fig.data)  # vs time


def test_facet_figure_panels_share_their_axes():
    x_values = [1.0, 2.0, 3.0, 4.0]
    fig = plotting.facet_figure(_facet_long(x_values), {"X": x_values})
    # Plotly Express shares axes by tying every panel's axis to the first one.
    y_axes = [fig.layout[k] for k in fig.layout if k.startswith("yaxis")]
    assert all(axis.matches in (None, "y") for axis in y_axes)
    assert sum(axis.matches == "y" for axis in y_axes) == len(y_axes) - 1


def test_facet_figure_keeps_each_species_one_colour_across_panels():
    x_values = [1.0, 2.0, 3.0]
    fig = plotting.facet_figure(_facet_long(x_values), {"X": x_values})
    colours = plotting.colour_map(["[Z]", "[W]"])
    assert all(trace.line.color == colours[trace.name] for trace in fig.data)


def test_facet_figure_draws_only_the_shown_species():
    x_values = [1.0, 2.0]
    fig = plotting.facet_figure(_facet_long(x_values), {"X": x_values}, shown=["[W]"])
    assert {trace.name for trace in fig.data} == {"[W]"}


def test_facet_figure_grows_taller_with_more_rows():
    def height(n):
        x_values = [float(v) for v in range(1, n + 1)]
        return plotting.facet_figure(_facet_long(x_values), {"X": x_values}).layout.height

    assert height(3) < height(9) < height(16)  # 1, 3 and 4 rows


def test_facet_titles_tell_apart_values_that_look_alike_rounded():
    # Two values the same at 3 significant figures still get a panel each.
    x_values = [1.0001, 1.0002]
    fig = plotting.facet_figure(_facet_long(x_values), {"X": x_values})
    assert len(fig.layout.annotations) == 2


# --- scan_bar_html ------------------------------------------------------------
def _marker_positions(markup: str) -> list[float]:
    return [float(p) for p in re.findall(r"left:([\d.]+)%", markup)]


def test_scan_bar_places_a_marker_at_each_value_from_end_to_end():
    markup = plotting.scan_bar_html("bar", "k", [0.0, 0.5, 1.0], False, "#ff4b4b")
    assert _marker_positions(markup) == [0.0, 50.0, 100.0]
    assert "3 values · evenly spaced" in markup


def test_scan_bar_spaces_log_scans_evenly_by_factor_of_ten():
    markup = plotting.scan_bar_html("bar", "k", [0.01, 1.0, 100.0], True, "#ff4b4b")
    assert _marker_positions(markup) == [0.0, 50.0, 100.0]
    assert ">0.01<" in markup and ">100<" in markup  # the ends, as on a slider


def test_scan_bar_escapes_the_label():
    markup = plotting.scan_bar_html("bar", "<b>k</b>", [1.0, 2.0], False, "#ff4b4b")
    assert "<b>k</b>" not in markup and "&lt;b&gt;k&lt;/b&gt;" in markup
