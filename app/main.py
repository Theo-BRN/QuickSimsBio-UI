"""QuickSimsBio-UI — Streamlit entry point.

This file is the UI layer only: page config, widgets, and the glue that calls
the backend modules (see CLAUDE.md > Architecture). All model/registry logic
lives in ``models`` so it stays testable without a running Streamlit server.

Streamlit re-runs this whole script top-to-bottom on every interaction, so the
expensive work (listing examples, loading a model) is wrapped in caches below.
"""

from pathlib import Path

# app/ is on sys.path when Streamlit runs this file, so sibling modules import
# as top-level names (this mirrors how the tests are configured).
import events
import models
import plotting
import simulations
import streamlit as st

st.set_page_config(page_title="QuickSimsBio", page_icon="🧪", layout="wide")


@st.cache_data(show_spinner=False)
def get_registry() -> dict[str, dict]:
    """Model registry, cached so we don't re-list examples on every rerun.

    cache_data is for serialisable *data* (here: plain dicts/strings).
    """
    return models.get_model_registry()


@st.cache_resource(show_spinner="Loading model…")
def load_model(kind: str, ref: str):
    """Load and cache a model, keyed on (kind, ref) so each loads once.

    cache_resource is for non-serialisable *resources* (a live COPASI model).
    It's a process-wide cache shared across user sessions — fine here because we
    only read the model. Once we start *setting* parameters for a run, we'll
    need to be careful not to mutate this shared object.
    """
    return models.load_model({"kind": kind, "ref": ref})


@st.cache_data(show_spinner=False)
def get_inputs_cached(kind: str, ref: str) -> dict:
    """The model's inputs mapped to their defaults (``{name: value}``), per model.

    Same pattern as ``run_cached``: keyed on the hashable ``(kind, ref)`` and the
    live model handle re-fetched from the cache_resource loader inside. The
    result is a plain dict, so cache_data stores it happily. Used to seed the
    inputs table (every input starts as a "Single" at its default value).
    """
    model = load_model(kind, ref)
    return simulations.get_inputs(model)


@st.cache_data(show_spinner=False)
def get_input_kinds_cached(kind: str, ref: str) -> dict:
    """Each input's kind (parameter, species or reaction parameter), per model.

    Same cache pattern as ``get_inputs_cached``. Labels the chip inputs and picks
    the newcomer example (a few of each kind).
    """
    model = load_model(kind, ref)
    return simulations.get_input_kinds(model)


@st.cache_data(show_spinner=False)
def get_events_cached(kind: str, ref: str):
    """The model's events (resolved) and its time unit, per model.

    Same cache pattern as ``get_inputs_cached``. ``events.Event`` is a plain frozen
    dataclass, so the list stores fine in cache_data. Read-only: we only ever
    display these (see CLAUDE.md > Events).
    """
    model = load_model(kind, ref)
    return events.read_events(model), events.time_unit(model)


@st.cache_data(show_spinner="Running simulation…")
def run_cached(kind: str, ref: str, scan_dict: dict, timepoints: list[float]):
    """Run a simulation, cached so identical runs return instantly.

    Keyed on the *hashable* inputs — the source tags ``(kind, ref)``, the
    ``scan_dict``, and the ``timepoints`` (cache_data hashes dicts/lists by
    value). We deliberately do NOT take the live model handle as an argument:
    a COPASI model isn't hashable, so we re-fetch it from the cache_resource
    loader inside instead (that call is itself cached, so it's free).
    """
    model = load_model(kind, ref)
    return simulations.run(model, scan_dict, timepoints)


@st.dialog("Use a custom model")
def use_custom_model_dialog():
    """Modal to use your own model — by BioModels ID or by uploading a file.

    st.dialog inherits fragment behaviour: typing in a field only re-runs this
    function, so the modal stays open. We validate by actually loading the model
    before accepting it, then auto-select it and close with st.rerun(). Custom
    models join the menu for this session (persistence was deliberately deferred).
    """
    st.caption(
        "Use your own model — give a BioModels ID, or upload a COPASI (.cps) / "
        "SBML (.sbml) file. It joins your menu for this session."
    )
    method = st.radio("Source", ["BioModels ID", "Upload file"], horizontal=True)

    model_id, upload, default_name = "", None, ""
    if method == "BioModels ID":
        model_id = st.text_input(
            "BioModels ID", placeholder="e.g. MODEL2306220001"
        ).strip()
        default_name = model_id
    else:
        upload = st.file_uploader("Model file", type=["cps", "sbml", "xml"])
        if upload is not None:
            default_name = Path(upload.name).stem

    name = st.text_input("Display name", value=default_name)
    ready = bool(name) and (bool(model_id) or upload is not None)

    if st.button("Use this model", type="primary", disabled=not ready):
        if method == "BioModels ID":
            source = {"kind": "biomodels", "ref": model_id}
        else:
            source = models.make_uploaded_source(upload.name, upload.getvalue())
        try:
            load_model(source["kind"], source["ref"])  # validate by loading
        except Exception as exc:  # broad: friendly message, not a traceback
            st.error(
                f"Couldn't load **{name}**. {models.describe_load_error(source, exc)}"
            )
            return
        st.session_state.user_models[name] = source
        st.session_state.selected_model = name  # auto-select it on the rerun
        st.toast(f"Added **{name}** to your menu for this session.", icon="✅")
        st.rerun()


# --- Building blocks ----------------------------------------------------------


def render_time_window():
    """Start / end / number-of-points controls. Returns (start, end, n, error).

    Start/end bound the window we *record*, not what COPASI integrates — it
    always runs from 0, so a later start keeps the model's history (and its
    events) intact and simply focuses every recorded point on the stretch you
    care about. See simulations.make_timepoints.
    """
    st.markdown("**Time window**")
    col_start, col_end, col_points = st.columns(3)
    start = col_start.number_input(
        "Start time",
        min_value=0.0,
        value=0.0,
        step=100.0,
        key="start",
        help=(
            "When to start recording. The model always runs from 0, so raising "
            "this skips nothing — it just puts your points where the action is."
        ),
    )
    end = col_end.number_input(
        "End time",
        min_value=0.0,
        value=1000.0,
        step=100.0,
        key="end",
        help="When to stop, in the model's own time units.",
    )
    n_points = col_points.number_input(
        "Number of points",
        min_value=2,
        value=300,
        step=50,
        key="n_points",
        help="How many timepoints to record across the window.",
    )

    error = "Start time must be less than End time." if start >= end else ""
    if error:
        st.error(error)
    return start, end, n_points, error


def render_events_panel(source: dict, end_time: float):
    """The model's events (read-only), beside the time window they inform.

    Its whole job is to inform the time-window choice: many models only *do*
    anything after an event that fires far later than the default window reaches.
    Read-only — we surface events, never edit them (see CLAUDE.md > Events).
    Wrapped broadly on purpose: an uncaught error reading a model's events would
    blank the Run button and results below it. A cosmetic panel must never do
    that, so on failure we simply show nothing.
    """
    try:
        model_events, time_unit = get_events_cached(source["kind"], source["ref"])
    except Exception:  # broad on purpose: a missing panel beats a broken page
        return
    if not model_events:
        return

    # The sharp edge this feature exists to catch: a run that stops before its
    # events ever fire (e.g. CTCA's events at ~1e6 vs the default end of 1000).
    # Directly under End time, where you'd act on it. A plain caption, not a
    # warning: stopping early is often deliberate (see events.missed_status).
    status = events.missed_status(model_events, end_time, time_unit)
    if status:
        st.caption(status)

    with st.expander("Events in this model", expanded=False, key="events"):
        st.dataframe(
            events.events_table(model_events),
            hide_index=True,
            width="stretch",
            key="events_table",
        )
        timeline = events.segments(model_events)
        if timeline:
            st.plotly_chart(
                events.timeline_figure(timeline, end=end_time, unit=time_unit),
                width="stretch",
                key="events_timeline",
            )
        else:
            st.caption(
                "These events are state-based — when they fire depends on the "
                "simulation, so they can't be placed on a timeline in advance."
            )


def _toggle_output(hidden_key: str, output: str):
    """Legend-button callback: hide the output if shown, show it if hidden."""
    hidden = st.session_state[hidden_key]
    if output in hidden:
        hidden.remove(output)
    else:
        hidden.add(output)


def render_legend(hidden_key: str, outputs: list[str]):
    """The app's own legend: one button per output, in its line's colour.

    Clicking an entry hides or shows that output. This replaces Plotly's legend,
    whose clicks can't be remembered here: Streamlit identifies a chart by its full
    contents, so new data (a slider move) means a brand-new chart and Plotly's
    legend and zoom state are lost. Which outputs are hidden lives in our own
    state instead (the set at ``hidden_key``, one per model), so it survives every
    redraw — and because the plot reads that set too, the legend can be drawn
    *under* the plot.

    Colouring: each button sits in a container whose key Streamlit turns into the
    CSS class ``st-key-<key>`` (documented behaviour), which the stylesheet below
    targets with that output's exact colour (see ``plotting.legend_entry_css``).
    """
    hidden = st.session_state[hidden_key]
    colours = plotting.colour_map(outputs)

    css = []
    with st.container(horizontal=True, gap="small"):
        for i, output in enumerate(outputs):
            box_key = f"legend-{i}"
            with st.container(key=box_key, width="content"):
                st.button(
                    output,
                    key=f"legend::{i}",
                    on_click=_toggle_output,
                    args=(hidden_key, output),
                )
            css.append(
                plotting.legend_entry_css(
                    f"st-key-{box_key}", colours[output], shown=output not in hidden
                )
            )
    st.html(f"<style>{' '.join(css)}</style>")


def render_results(source: dict):
    """The latest results, from the run request stored under ``last_run``.

    Only a result for the currently selected model is shown — switching models
    leaves the old request behind, but it isn't this model's.
    """
    last_run = st.session_state.get("last_run")
    if not last_run or (last_run["kind"], last_run["ref"]) != (
        source["kind"],
        source["ref"],
    ):
        st.caption("Your results will appear here once you run a simulation.")
        return

    # Some models / parameter combos fail inside COPASI, and the long-format path
    # can throw for others. Non-technical users can't read a traceback, so we
    # degrade gracefully: a failed *run* shows a friendly message; a failed *plot*
    # still keeps the raw data (shown below) so nothing is lost.
    try:
        wide, long = run_cached(
            last_run["kind"],
            last_run["ref"],
            last_run["scan_dict"],
            last_run["timepoints"],
        )
    except Exception as exc:  # broad on purpose: a message, not a traceback
        st.error(
            "This model couldn't complete the simulation for those settings. "
            "Try different inputs, or another model."
        )
        with st.expander("What went wrong?", key="run_error"):
            st.write(str(exc))
        # `return`, not st.stop(): st.stop() halts the WHOLE script, so anything
        # drawn after the results would vanish. return only ends this panel.
        return

    # The plot follows what was run (see plotting.mode): nothing scanned -> one
    # kinetics plot; one input scanned -> the same traces, one panel per value.
    scan_dict = last_run["scan_dict"]
    try:
        plot_type = st.radio(
            "Plot type",
            plotting.PLOT_TYPES,
            # Only the label changes: "area" stacks its series, so say so.
            format_func=lambda kind: "stacked area" if kind == "area" else kind,
            horizontal=True,
            key="plot_type",
        )
        outputs = long[plotting.COL_OUTPUT_TYPE].unique().tolist()
        hidden_key = f"hidden::{last_run['kind']}::{last_run['ref']}"
        hidden = st.session_state.setdefault(hidden_key, set())
        shown = [output for output in outputs if output not in hidden]
        if plotting.mode(scan_dict) == plotting.MODE_KINETICS:
            fig = plotting.kinetics_figure(long, plot_type=plot_type, shown=shown)
            fig.update_layout(height=500)  # the facet figure sets its own height
        else:
            fig = plotting.facet_figure(
                long, scan_dict, plot_type=plot_type, shown=shown
            )
        # Our legend (under the plot) replaces Plotly's.
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, width="stretch", key="plot")
        render_legend(hidden_key, outputs)
    except Exception as exc:  # broad on purpose: keep the data, explain the plot
        st.warning(
            "Couldn't draw a plot for this result — here's the raw data instead."
        )
        with st.expander("What went wrong?", key="plot_error"):
            st.write(str(exc))

    with st.expander("Raw results table", key="raw"):
        st.dataframe(wide, width="stretch", key="raw_results")


def _add_input(chosen_key: str, picker_key: str):
    """Picker callback: add the picked input as a row, then clear the picker.

    Callbacks run *before* the next script run — the one moment Streamlit lets code
    reset a widget's own value (here, the picker back to empty).
    """
    name = st.session_state[picker_key]
    if name and name not in st.session_state[chosen_key]:
        st.session_state[chosen_key].append(name)
    st.session_state[picker_key] = None


def _remove_input(chosen_key: str, name: str, row_keys: list[str]):
    """Remove-button callback: drop the row and forget its settings, so adding the
    same input again starts back at its default."""
    st.session_state[chosen_key].remove(name)
    for key in row_keys:
        st.session_state.pop(key, None)


def _row_key(model_key: str, field: str, name: str) -> str:
    """The session-state key for one setting of one input's row."""
    return f"{model_key}::{field}::{name}"


def _slider_settings(keys: dict, default: float) -> tuple[dict, str]:
    """This row's slider settings, checked. Returns (settings, problem message or "").

    The settings widgets sit in the ⚙ panel, drawn *after* the slider, so their
    values are read from the previous run — the defaults on the first run. The
    checking itself lives in simulations.check_slider_settings, where it's tested.
    """
    base = simulations.default_slider_settings(default)
    settings = {
        field: st.session_state.get(keys[field], base[field])
        for field in ("min", "max", "log")
    }
    return simulations.check_slider_settings(settings, default)


def _switch_to_log(min_key: str, max_key: str, log_key: str):
    """Log-scale callback: a log scale can't start at 0 (or below), so turning it
    on moves Min up to a millionth of Max, rather than refusing the switch."""
    low, high = st.session_state[min_key], st.session_state[max_key]
    if st.session_state[log_key] and low <= 0 < high:
        st.session_state[min_key] = float(f"{high * simulations.LOG_MIN_FRACTION:.6g}")


def render_input_row(
    model_key: str,
    chosen_key: str,
    name: str,
    default: float,
    kind: str,
    scan_locked: bool,
) -> list[float]:
    """One chosen input as a compact row: [slider] [⚙] [×]. Returns its values.

    The slider moves through real values: by default 1/100 to 100× the default on
    a log scale, or a linear 0 – 1 for an input that starts at 0 (nothing to base
    a range on, so the user widens it). ⚙ sets Min, Max and Log scale; × removes
    the row. A horizontal container (not columns) keeps the row together on a
    phone, where columns stack.

    **Scanning:** ⚙ can also switch the row to a scan — several evenly spaced
    values across the same Min / Max — which swaps the slider for a frozen bar
    marking each value (``plotting.scan_bar_html``). Only one input scans at a
    time, so ``scan_locked`` greys out the switch while another input is
    scanning. Returns one value for a slider, or the scan's values.
    """
    keys = {
        field: _row_key(model_key, field, name)
        for field in ("value", "min", "max", "log", "scan", "n")
    }
    label = (
        f"{name} (initial concentration)" if kind == simulations.KIND_SPECIES else name
    )
    scanning = st.session_state.get(keys["scan"], False)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        settings, problem = _slider_settings(keys, default)
        if scanning:
            n = st.session_state.get(keys["n"], simulations.DEFAULT_SCAN_VALUES)
            values = simulations.scan_values(settings, n)
            colour = st.get_option("theme.primaryColor") or "#ff4b4b"
            with st.container(width="stretch"):
                st.html(
                    plotting.scan_bar_html(
                        "qsb-scan-bar", label, values, settings["log"], colour
                    )
                )
        else:
            options = simulations.slider_options(settings, default)
            # Keep the slider on a valid stop if its settings have just changed.
            current = st.session_state.get(keys["value"], default)
            st.session_state[keys["value"]] = simulations.nearest_option(
                options, current, settings["log"]
            )
            value = st.select_slider(
                label,
                options=options,
                format_func=lambda v: f"{v:.3g}",
                key=keys["value"],
                width="stretch",
            )
            values = [value]

        # Seeded through session state, not value=, so _switch_to_log can change
        # Min: Streamlit warns if a widget gets both a value= and a state value.
        # Written back on *every* run, not just the first: only a value set from
        # the script is sent to the browser, so otherwise a later run sends just
        # the widget's built-in default (0) and the panel showed Min = Max = 0.
        seeds = simulations.default_slider_settings(default) | {
            "scan": False,
            "n": simulations.DEFAULT_SCAN_VALUES,
        }
        for field, seed in seeds.items():
            st.session_state[keys[field]] = st.session_state.get(keys[field], seed)
        # Keyed so the open panel stays with *this* input when the rows reorder
        # (a scanned input jumps to the top). Unkeyed, Streamlit tells elements
        # apart by position, so the panel stayed open on whichever row moved in.
        with st.popover(
            "",
            icon=":material/tune:",
            help="Slider settings",
            key=_row_key(model_key, "settings", name),
        ):
            st.number_input("Min", format="%g", key=keys["min"])
            st.number_input("Max", format="%g", key=keys["max"])
            st.toggle(
                "Log scale",
                key=keys["log"],
                on_change=_switch_to_log,
                args=(keys["min"], keys["max"], keys["log"]),
            )
            st.toggle(
                "Scan across several values",
                key=keys["scan"],
                disabled=scan_locked,
                help=(
                    "Only one input can be scanned at a time."
                    if scan_locked
                    else "Run once for each of several evenly spaced values "
                    "between Min and Max, and show each as its own panel."
                ),
            )
            if scanning:
                st.number_input(
                    "Number of values",
                    min_value=2,
                    max_value=simulations.MAX_SCAN_VALUES,
                    step=1,
                    key=keys["n"],
                )
            if problem:
                st.caption(problem)

        st.button(
            "",
            icon=":material/close:",
            help="Remove",
            key=f"{model_key}::remove::{name}",
            on_click=_remove_input,
            args=(chosen_key, name, list(keys.values())),
        )
    return values


# The inputs column stays in view while the results column scrolls (a 4 x 4 scan
# is far taller than the screen), and scrolls on its own if it has many rows.
# CSS "position: sticky" pins it below Streamlit's header bar
# (https://developer.mozilla.org/en-US/docs/Web/CSS/position#sticky).
# The column is found through the keyed container inside it (the documented
# st-key-<key> class) and Streamlit's data-testid="stColumn", which isn't a
# documented API: if an upgrade renames it, the column simply stops sticking.
# Only on wide screens: below 640 px Streamlit stacks the columns anyway.
INPUTS_COLUMN_CSS = """
@media (min-width: 640px) {
  [data-testid="stColumn"]:has(.st-key-time-course-inputs) {
    position: sticky;
    top: 4rem;
    align-self: flex-start;
    max-height: calc(100vh - 5rem);
    overflow-y: auto;
    padding-right: 0.5rem;
  }
}
"""


def render_time_course(source: dict):
    """Time course: change a few inputs, and everything else runs at its default.

    An "Add an input…" picker adds inputs as compact rows (``render_input_row``),
    starting from a newcomer's example (``simulations.example_inputs``). Keys
    include the model, so switching model starts afresh.

    There is no Run button: every change reruns the script, the request below is
    refreshed each time, and ``run_cached`` only simulates when the inputs actually
    changed — so the plot always matches what's on screen, a first visit shows a
    result immediately, and number boxes update the moment you press Enter.
    """
    defaults = get_inputs_cached(source["kind"], source["ref"])
    kinds = get_input_kinds_cached(source["kind"], source["ref"])
    model_key = f"{source['kind']}::{source['ref']}"
    chosen_key = f"{model_key}::chosen"
    picker_key = f"{model_key}::picker"
    st.session_state.setdefault(chosen_key, simulations.example_inputs(kinds))
    chosen = st.session_state[chosen_key]

    # Narrow inputs, wide results with the time window above the plot — it decides
    # the plot's x-axis. That column's top is filled first because the request
    # below needs the times; a column's place on screen is fixed when st.columns()
    # creates it, not by the order it's filled in.
    col_inputs, col_outputs = st.columns([1, 2], gap="large")
    st.html(f"<style>{INPUTS_COLUMN_CSS}</style>")

    with col_outputs:
        start, end, n_points, time_error = render_time_window()
        render_events_panel(source, end)

    with col_inputs, st.container(key="time-course-inputs"):
        st.selectbox(
            "Add an input",
            options=[name for name in defaults if name not in chosen],
            index=None,
            placeholder="Add an input…",
            format_func=lambda name: (
                f"{name} · "
                + (
                    "initial concentration"
                    if kinds[name] == simulations.KIND_SPECIES
                    else kinds[name]  # "parameter" or "reaction parameter"
                )
            ),
            label_visibility="collapsed",
            key=picker_key,
            on_change=_add_input,
            args=(chosen_key, picker_key),
        )
        # The input being scanned (at most one) gets the top row, so the panels'
        # variable sits first; every other input keeps its slider below it.
        scanned = next(
            (
                name
                for name in chosen
                if st.session_state.get(_row_key(model_key, "scan", name))
            ),
            None,
        )
        rows = ([scanned] if scanned else []) + [n for n in chosen if n != scanned]
        chosen_values = {
            name: render_input_row(
                model_key,
                chosen_key,
                name,
                defaults[name],
                kinds[name],
                scan_locked=scanned is not None and name != scanned,
            )
            for name in rows
        }
        scan_dict = simulations.build_scan_dict_from_values(defaults, chosen_values)

        if not time_error:
            st.session_state["last_run"] = {
                "kind": source["kind"],
                "ref": source["ref"],
                "scan_dict": scan_dict,
                "timepoints": simulations.make_timepoints(start, end, int(n_points)),
            }

    with col_outputs:
        render_results(source)


# --- Page ---------------------------------------------------------------------
st.title("QuickSimsBio")

# Built-in registry, plus any custom models added this session.
st.session_state.setdefault("user_models", {})
registry = models.merge_user_models(get_registry(), st.session_state.user_models)
options = list(registry)

# The sidebar is for choosing *what* to simulate — a familiar, always-visible
# home that leaves the content area for inputs and results (snapshots will join
# it later). Everything about *how* to run lives in the main area.
with st.sidebar:
    st.header("Setup")

    # Keep selection in our OWN state (not the selectbox's key) so the dialog can
    # set it freely, and feed it back as the index. Feeding the index also restores
    # the choice when the options list changes (e.g. a custom model was just added).
    # A first visit starts on models.DEFAULT_MODEL rather than an empty picker.
    selected = st.session_state.get("selected_model", models.DEFAULT_MODEL)
    index = options.index(selected) if selected in options else None

    # index=None gives the greyed "Choose a model…" placeholder (nothing selected).
    choice = st.selectbox(
        "Model",
        options,
        index=index,
        placeholder="Choose a model…",
    )
    st.session_state.selected_model = choice  # remember manual selections too

    if st.button("Use custom model"):
        use_custom_model_dialog()

if choice is None:
    st.info("Pick a model via the sidebar to get started.")
    st.stop()

source = registry[choice]
try:
    load_model(source["kind"], source["ref"])
except Exception as exc:  # broad on purpose: show a friendly message, not a traceback
    st.sidebar.error(
        f"Couldn't load **{choice}**. {models.describe_load_error(source, exc)}"
    )
    st.stop()

st.sidebar.success(f"Loaded **{choice}**.")

# Say in the main area which model is loaded and what it is (UX rule 1:
# visibility of system status). On the default model, also say how to change it:
# the sidebar starts hidden on a phone, so a first-time visitor can't see it.
description = models.MODEL_DESCRIPTIONS.get(choice)
model_line = f"**{choice}** — {description}" if description else f"**{choice}**"
if choice == models.DEFAULT_MODEL:
    model_line += (
        "  \nThis is the default model. Use the sidebar to choose a different model."
    )
st.caption(model_line)

# --- Time course --------------------------------------------------------------
# One view: a visitor meets one clear experience. Scanning one input
# is a setting on that input's row (⚙), not a separate mode.
render_time_course(source)
