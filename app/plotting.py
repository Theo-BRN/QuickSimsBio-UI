"""Adaptive plotting for QuickSimsBio-UI.

Turns a simulation's **long** result frame into a Plotly figure. Which figure
depends on what the user ran (see ``mode``):

    nothing scanned  -> kinetics traces (every output vs time)
    one input scanned -> the same traces, one small panel per scanned value

Like ``simulations`` and ``models``, this module is deliberately **Streamlit-free**:
every function here is a pure transform (result frame -> figure, or frame -> facts
about the frame) so it can be unit-tested without a running app. ``main.py`` reads
the widget selections and calls one builder.
"""

import html
import math
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

# The three ways to draw the traces (a UI toggle picks one).
PLOT_TYPES = ["line", "scatter", "area"]

# The two simulation "modes", chosen by how many inputs were scanned. Each maps
# to one figure builder (see ``mode`` and the builders below).
MODE_KINETICS = "kinetics"  # nothing scanned -> kinetics_figure
MODE_FACETS = "facets"  # one input scanned -> facet_figure

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

    Inputs held at one value are length-1 lists, and so are excluded. Order
    follows the scan_dict.
    """
    return [name for name, values in scan_dict.items() if len(values) > 1]


def mode(scan_dict: dict) -> str:
    """Pick the plot mode for a run from how many inputs were scanned.

    The single source of truth for figure-builder selection: nothing scanned ->
    ``MODE_KINETICS``, one input -> ``MODE_FACETS``. The app only ever scans one
    input at a time, by design, so more than one is a bug and raises.
    """
    n = len(varying_inputs(scan_dict))
    if n == 0:
        return MODE_KINETICS
    if n == 1:
        return MODE_FACETS
    raise ValueError(f"Only one input can be scanned at a time, got {n}.")


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
    their value-sets are identical, so both would grab the same column. So among the value-matches we prefer the column actually
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
    df,
    x: str,
    plot_type: str,
    *,
    shown: list[str] | None = None,
    facets: dict | None = None,
):
    """Shared line/scatter/area builder: ``Model_Output`` vs ``x``, per species.

    Backs both the kinetics plot and the faceted scan plot (``facets`` holds the
    extra Plotly Express facet arguments). Colour = ``Model_Output_Type`` so each
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
    facets = dict(facets or {})
    # The panels' order (if any) joins the outputs' order in one category_orders.
    category_orders = {COL_OUTPUT_TYPE: order, **facets.pop("category_orders", {})}
    return drawers[plot_type](
        df,
        x=x,
        y=COL_OUTPUT,
        color=COL_OUTPUT_TYPE,
        color_discrete_map=colours,
        category_orders=category_orders,
        labels=labels,
        template="plotly_white",
        **facets,
    )


def kinetics_figure(long_df, plot_type: str = "line", shown: list[str] | None = None):
    """0-D: output species vs time (a kinetics trace); ``shown`` limits which."""
    return _series_figure(long_df, COL_TIME, plot_type, shown=shown)


def facet_columns(n_panels: int) -> int:
    """Panels per row: as close to a square grid as possible (9 -> 3 x 3)."""
    return math.ceil(math.sqrt(n_panels))


def facet_figure(
    long_df, scan_dict: dict, plot_type: str = "line", shown: list[str] | None = None
):
    """One input scanned: the kinetics traces again, one small panel per value.

    Panels wrap onto rows of ``facet_columns`` and share both axes, so they
    compare at a glance; colours follow ``colour_map``, so they match the legend.
    Each panel is titled with its value, e.g. ``X = 0.316``. The titles are built
    from Plotly's own ("[X]_0=0.316228"), so two values that look alike at 3
    significant figures still get panels of their own.
    """
    name = varying_inputs(scan_dict)[0]
    column = column_for_input(long_df, scan_dict, name)
    values = sorted(long_df[column].unique())
    n_columns = facet_columns(len(values))
    n_rows = math.ceil(len(values) / n_columns)

    fig = _series_figure(
        long_df,
        COL_TIME,
        plot_type,
        shown=shown,
        facets=dict(
            facet_col=column,
            facet_col_wrap=n_columns,
            facet_col_spacing=0.04,
            facet_row_spacing=0.08 if n_rows > 1 else 0,
            category_orders={column: values},
        ),
    )
    fig.for_each_annotation(
        lambda a: a.update(text=f"{name} = {float(a.text.rsplit('=', 1)[-1]):.3g}")
    )
    fig.update_layout(height=120 + 230 * n_rows)
    return fig


def scan_bar_html(
    css_class: str, label: str, values: list[float], log: bool, colour: str
) -> str:
    """A frozen "slider" showing where a scan's values sit, as HTML.

    Drawn in place of the slider of the input being scanned: the same coloured
    track, with a square marker at each value (squares, so they don't read as a
    slider's round handle), and the range's ends written underneath, as a slider
    does. Markers are placed on a log scale when the scan is log-spaced. It's a
    picture, not a widget: the scan is set in the row's ⚙ settings.
    """
    low, high = values[0], values[-1]

    def position(value: float) -> float:
        if log:
            return (math.log10(value) - math.log10(low)) / (
                math.log10(high) - math.log10(low)
            )
        return (value - low) / (high - low)

    markers = "".join(
        f'<span class="mark" style="left:{100 * position(v):.2f}%"></span>'
        for v in values
    )
    spacing = "log-spaced" if log else "evenly spaced"
    return f"""
<style>
.{css_class} {{ padding: 0 0.5rem 0.25rem; font-size: 14px; }}
.{css_class} .track {{ position: relative; height: 4px; margin: 0.9rem 0 0.6rem;
  border-radius: 2px; background: {colour}; }}
.{css_class} .mark {{ position: absolute; top: 50%; width: 10px; height: 10px;
  transform: translate(-50%, -50%); background: {colour};
  border: 1.5px solid white; border-radius: 1px; }}
.{css_class} .ends {{ display: flex; justify-content: space-between;
  font-size: 12px; opacity: 0.7; }}
</style>
<div class="{css_class}">
  <div>{html.escape(label)}</div>
  <div class="track">{markers}</div>
  <div class="ends"><span>{low:.3g}</span>
    <span>{len(values)} values · {spacing}</span><span>{high:.3g}</span></div>
</div>
"""
