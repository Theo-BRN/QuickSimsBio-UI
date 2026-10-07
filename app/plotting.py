"""Adaptive plotting for QuickSimsBio-UI.

Turns a simulation's **long** result frame into the Plotly figure that best fits
the simulation the user ran — the view "morphs" to the number of *varying* input
dimensions (see CLAUDE.md > the adaptive-output principle):

    0 varying inputs  -> kinetics traces (every output species vs time)
    1 varying input   -> output vs that input (reduced to the final timepoint)
    2+ varying inputs -> coloured scatter / 3-D (inputs on the axes, output as
                         colour / z)

Like ``simulations`` and ``models``, this module is deliberately **Streamlit-free**:
every function here is a pure transform (result frame -> figure, or frame -> facts
about the frame) so it can be unit-tested without a running app. ``main.py`` reads
the widget selections and calls one builder.
"""

import re

import numpy as np
import plotly.express as px

# --- Long-format schema -------------------------------------------------------
# Column names the package's long format (``format_output="both"``/``"long"``, via
# ``auto_melt``) always produces. ``Time`` plus these two are the "reserved"
# columns; every *other* column in a long frame is a varied-input column.
COL_TIME = "Time"
COL_OUTPUT_TYPE = "Model_Output_Type"  # which output/species, e.g. "[X]"
COL_OUTPUT = "Model_Output"  # the numeric value
RESERVED = {COL_TIME, COL_OUTPUT_TYPE, COL_OUTPUT}

# The three ways to draw a kinetics / vs-input plot (a UI toggle picks one).
PLOT_TYPES = ["line", "scatter", "area"]

# The Scale label (from the inputs table) that means a logarithmic axis. Kept as a
# literal here — rather than importing ``simulations.SCALE_LOG`` — so this module
# stays free of the heavy simulations/basico import. Must match that constant.
SCALE_LOG = "Log"

# The three simulation "modes", chosen by how many inputs were scanned. Each maps
# to one figure builder (see ``mode`` and the builders below).
MODE_KINETICS = "kinetics"  # 0 varying -> kinetics_figure
MODE_VS_INPUT = "vs_input"  # 1 varying -> vs_input_figure
MODE_SCATTER = "scatter"  # 2+ varying -> scatter_figure

# Palette + symbols carried over from the design doc
# (Detecting results shape from input shape.md). A fixed, high-contrast order so a
# given species keeps the same colour across the different plot types.
CUSTOM_COLOURS = [
    "#0000FF",
    "#FF0000",
    "#00C000",
    "#AD07E3",
    "#FF8000",
    "#000000",
    "#94641F",
    "#000080",
    "#610051",
    "#A00000",
    "#005A00",
    "#F2B77C",
    "#00FF00",
    "#90BFF9",
    "#C0C0FF",
    "#606060",
    "#FFFF00",
    "#C06000",
    "#D4D4D4",
]
CUSTOM_SYMBOLS = [
    "circle",
    "square",
    "pentagon",
    "diamond",
    "cross",
    "star-square",
    "diamond-wide",
]


# --- Data-shape helpers (pure; "what did the user run / what varies?") ---------
def varying_inputs(scan_dict: dict) -> list[str]:
    """Input names that actually scan — their value list has length > 1.

    The *count* is the simulation "mode": 0 -> kinetics, 1 -> output-vs-input,
    >= 2 -> scatter. Single inputs are length-1 lists (held at a value) and so are
    excluded. Order follows the scan_dict (i.e. the inputs-table order).
    """
    return [name for name, values in scan_dict.items() if len(values) > 1]


def mode(scan_dict: dict) -> str:
    """Pick the plot mode for a run from how many inputs were scanned.

    The single source of truth for figure-builder selection: 0 varying inputs ->
    ``MODE_KINETICS``, 1 -> ``MODE_VS_INPUT``, 2+ -> ``MODE_SCATTER``. The UI branches
    on this to choose both the controls and the builder to call.
    """
    n = len(varying_inputs(scan_dict))
    if n == 0:
        return MODE_KINETICS
    if n == 1:
        return MODE_VS_INPUT
    return MODE_SCATTER


def axis_columns(long_df) -> list[str]:
    """Long-frame columns that vary across simulations — the candidate plot axes.

    A long frame's columns are the varied-input columns plus the reserved
    ``Time`` / ``Model_Output_Type`` / ``Model_Output``. Crucially, *held* (single)
    inputs still show up as **constant** columns (e.g. ``[Y]_0`` all equal), so we
    keep only non-reserved columns with more than one unique value. Order follows
    the frame.
    """
    return [
        col
        for col in long_df.columns
        if col not in RESERVED and long_df[col].nunique() > 1
    ]


def reduce_final_timepoint(long_df):
    """Slice a long frame to its final timepoint.

    For 1-D / 2-D / n-D plots the quantity of interest is one scalar per parameter
    set (not the whole trace), so we take each series' value at the last simulated
    time — one ``Model_Output`` per (varying-input combo x ``Model_Output_Type``).
    (AUC and an arbitrary-timepoint picker are deferred — see TODO M4.)
    """
    last = long_df[COL_TIME].max()
    return long_df[long_df[COL_TIME] == last]


def _column_names_input(col: str, name: str) -> bool:
    """True if a long-frame column is the one COPASI named after input ``name``.

    COPASI names an input's column after the input itself — a species ``X`` →
    ``[X]_0``, a global parameter ``P`` → ``Values[P]`` — so the input name is the
    text inside the column's brackets. We pull out every bracketed token and check
    for an exact match. This is what tells apart two inputs scanned over *identical*
    ranges (e.g. ``kG+`` vs ``kG-``), which a value-match alone cannot.
    """
    return name in re.findall(r"\[([^\[\]]*)\]", col)


def column_for_input(long_df, scan_dict: dict, name: str) -> str:
    """Find the long-frame column that carries a varying input's values.

    The column name is model/type-dependent — a species input ``X`` lands in
    ``[X]_0`` while a global parameter ``P`` lands in ``Values[P]`` — so we first
    collect every axis column whose set of unique values equals the input's scanned
    values (compared with a float tolerance).

    Value-match alone is ambiguous when two inputs are scanned over the *same* range:
    their value-sets are identical, so both would grab the same column and collapse x
    and y onto one axis. So among the value-matches we prefer the column actually
    **named** after this input (``_column_names_input``); only if none is named do we
    fall back to the raw value-matches (keeps working for oddly-named columns).

    Some COPASI models also drive a parameter from an "Initial for <p>" helper
    quantity, so scanning ``p`` moves *both* ``Values[p]`` and ``Values[Initial for
    p]`` — two columns with identical values. They plot to the same x-positions, but
    we prefer the real quantity (no "Initial for") for the axis label.
    """
    wanted = np.sort(np.unique(np.asarray(scan_dict[name], dtype=float)))
    matches = [
        col
        for col in axis_columns(long_df)
        if (got := np.sort(np.unique(long_df[col].to_numpy(dtype=float)))).shape
        == wanted.shape
        and np.allclose(got, wanted)
    ]
    if not matches:
        raise KeyError(f"No varying column in the result matches input {name!r}.")
    named = [col for col in matches if _column_names_input(col, name)]
    candidates = named or matches
    real = [col for col in candidates if "Initial for" not in col]
    return (real or candidates)[0]


# --- Figure builders (pure; long frame -> plotly Figure) ----------------------
def colour_map(outputs: list[str]) -> dict[str, str]:
    """Each output's colour, by *name*, from the model's full list of outputs.

    Colouring by name (not by position in the plotted data) keeps a species'
    colour fixed when others are hidden, and the same across line, scatter and
    area plots. More outputs than colours wrap around the palette.
    """
    return {
        output: CUSTOM_COLOURS[i % len(CUSTOM_COLOURS)]
        for i, output in enumerate(outputs)
    }


def value_labels(outputs) -> dict[str, str]:
    """Plain names for the value and series columns, for axes and hover text.

    COPASI writes a species' concentration as "[name]". When every output is one,
    say so ("Species concentration", "Species"); anything else gets a neutral name
    rather than a wrong one.
    """
    names = [str(name) for name in outputs]
    if names and all(name.startswith("[") and name.endswith("]") for name in names):
        return {COL_OUTPUT: "Species concentration", COL_OUTPUT_TYPE: "Species"}
    return {COL_OUTPUT: "Output value", COL_OUTPUT_TYPE: "Output"}


def text_colour_on(background: str) -> str:
    """Black or white text, whichever reads better on ``background`` ("#RRGGBB").

    Uses the WCAG relative-luminance formula. Above 0.179, black text has more
    contrast than white, so light fills (yellow, light grey, pale blue) get black
    text and dark fills get white.
    """

    def linear(channel: float) -> float:
        return (
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )

    r, g, b = (int(background[i : i + 2], 16) / 255 for i in (1, 3, 5))
    luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
    return "#000000" if luminance > 0.179 else "#FFFFFF"


def legend_entry_css(css_class: str, colour: str, shown: bool) -> str:
    """CSS for one entry of the app's own legend (a button inside ``css_class``).

    Shown: filled with its line's colour, like a legend swatch. Hidden: a faded
    dashed outline, the way Plotly greys out a hidden trace. Hover and focus get
    the same look, so the colours don't flicker to the theme's when clicked. Kept
    small (about the size of the plot's other labels) so it reads as a legend
    rather than a bank of buttons.
    """
    target = ", ".join(
        f".{css_class} button{state}" for state in ("", ":hover", ":focus", ":active")
    )
    size = "min-height: 0; padding: 0.05rem 0.5rem; border-radius: 0.4rem;"
    if shown:
        style = (
            f"background: {colour}; border: 1px solid {colour}; "
            f"color: {text_colour_on(colour)}; {size}"
        )
    else:
        style = (
            f"background: transparent; border: 1px dashed {colour}; "
            f"color: {colour}; opacity: 0.55; {size}"
        )
    return (
        f"{target} {{ {style} }} "
        f".{css_class} button p {{ color: inherit; font-size: 0.8rem; }}"
    )


def _series_figure(
    df, x: str, plot_type: str, *, log_x: bool = False, shown: list[str] | None = None
):
    """Shared line/scatter/area builder: ``Model_Output`` vs ``x``, per species.

    Backs both the 0-D kinetics plot (``x = Time``) and the 1-D vs-input plot
    (``x`` = the scanned input's column). Colour = ``Model_Output_Type`` so each
    species is one series. ``shown`` limits which species are drawn (``None`` =
    all); colours come from ``colour_map`` over *all* species, so hiding one never
    recolours the rest.

    The **area** case needs a tweak: ``px.area`` stacks the first category at the
    *bottom* but lists it at the *top* of the legend, so legend order reads upside
    down versus the stack. Reversing the category order fixes that; colours follow
    names, so they need no reversing.
    """
    drawers = {"line": px.line, "scatter": px.scatter, "area": px.area}
    if plot_type not in drawers:
        raise ValueError(f"plot_type must be one of {PLOT_TYPES}, got {plot_type!r}.")

    outputs = df[COL_OUTPUT_TYPE].unique().tolist()
    colours = colour_map(outputs)
    labels = value_labels(outputs)
    if shown is not None:
        df = df[df[COL_OUTPUT_TYPE].isin(shown)]
        outputs = [output for output in outputs if output in shown]

    order = list(reversed(outputs)) if plot_type == "area" else outputs
    return drawers[plot_type](
        df,
        x=x,
        y=COL_OUTPUT,
        color=COL_OUTPUT_TYPE,
        color_discrete_map=colours,
        category_orders={COL_OUTPUT_TYPE: order},
        labels=labels,
        template="plotly_white",
        log_x=log_x,
    )


def kinetics_figure(long_df, plot_type: str = "line", shown: list[str] | None = None):
    """0-D: output species vs time (a kinetics trace); ``shown`` limits which."""
    return _series_figure(long_df, COL_TIME, plot_type, shown=shown)


def vs_input_figure(
    long_df,
    scan_dict: dict,
    scales: dict,
    plot_type: str = "line",
    shown: list[str] | None = None,
):
    """1-D: an output summary vs the single scanned input.

    Reduces each trace to its final timepoint (one value per input value x
    species), then plots ``Model_Output`` (y) against the scanned input's column
    (x), coloured by species. The x-axis is logarithmic when that input's Scale is
    ``Log`` (``scales`` maps input name -> ``"Linear"``/``"Log"``, from the table).
    """
    name = varying_inputs(scan_dict)[0]
    xcol = column_for_input(long_df, scan_dict, name)
    reduced = reduce_final_timepoint(long_df)
    return _series_figure(
        reduced, xcol, plot_type, log_x=scales.get(name) == SCALE_LOG, shown=shown
    )


def scatter_figure(
    long_df,
    scan_dict: dict,
    scales: dict,
    x_input: str,
    y_input: str,
    output_type: str,
    *,
    three_d: bool = False,
):
    """2-D / n-D: one output across two scanned inputs.

    Reduces to the final timepoint and filters to a single output species
    (``output_type`` — a scatter needs one value per point, not all species at once).
    The two chosen inputs go on the x/y axes (their columns found by value-match);
    the output value shows as **colour** (2-D ``px.scatter``) or as a **z-axis +
    colour** (``px.scatter_3d`` when ``three_d``). Each axis is logarithmic where its
    input's Scale is ``Log``. Any *further* varying inputs (n-D) aren't axis-mapped —
    their variation simply spreads the points (matches the doc's n-D note).
    """
    one_output = reduce_final_timepoint(long_df)
    one_output = one_output[one_output[COL_OUTPUT_TYPE] == output_type]

    common = dict(
        x=column_for_input(long_df, scan_dict, x_input),
        y=column_for_input(long_df, scan_dict, y_input),
        color=COL_OUTPUT,
        labels=value_labels([output_type]),
        template="plotly_white",
        log_x=scales.get(x_input) == SCALE_LOG,
        log_y=scales.get(y_input) == SCALE_LOG,
    )
    if three_d:
        return px.scatter_3d(one_output, z=COL_OUTPUT, **common)
    return px.scatter(one_output, **common)
