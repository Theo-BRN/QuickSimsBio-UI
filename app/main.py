"""QuickSimsBio-UI — Streamlit entry point.

This file is the UI layer only: page config, widgets, and the glue that calls
the backend modules (see CLAUDE.md > Architecture). All model/registry logic
lives in ``models`` so it stays testable without a running Streamlit server.

Streamlit re-runs this whole script top-to-bottom on every interaction, so the
expensive work (listing examples, loading a model) is wrapped in caches below.
"""

from math import prod
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
    """Each input's kind (``"parameter"`` / ``"species"``), per model.

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


# --- Tab building blocks ------------------------------------------------------
# Every tab's code runs on every rerun — hidden tabs included — so anything drawn
# in more than one tab needs a key unique to that tab, or Streamlit raises a
# duplicate-ID error. Each helper takes the tab's short name and prefixes its keys
# with it ("time_course::end", "vary_multiple::end", …). The *code* is shared;
# each tab's *values* are its own.


def render_time_window(tab: str):
    """Start / end / number-of-points controls. Returns (start, end, n, error).

    Start/end bound the window we *record*, not what COPASI integrates — it
    always runs from 0, so a later start keeps the model's history (and its
    events) intact and simply focuses every recorded point on the stretch you
    care about. See simulations.make_timepoints.

    Shared by the tabs that record a time course today. Scan tabs are expected to
    get their own controls (e.g. "measure at time t") rather than options here.
    """
    st.markdown("**Time window**")
    col_start, col_end, col_points = st.columns(3)
    start = col_start.number_input(
        "Start time",
        min_value=0.0,
        value=0.0,
        step=100.0,
        key=f"{tab}::start",
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
        key=f"{tab}::end",
        help="When to stop, in the model's own time units.",
    )
    n_points = col_points.number_input(
        "Number of points",
        min_value=2,
        value=300,
        step=50,
        key=f"{tab}::n_points",
        help="How many timepoints to record across the window.",
    )

    error = "Start time must be less than End time." if start >= end else ""
    if error:
        st.error(error)
    return start, end, n_points, error


def render_events_panel(tab: str, source: dict, end_time: float):
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
    # Directly under End time, where you'd act on it.
    missed = events.missed_events(model_events, end_time)
    if missed:
        plural = len(missed) > 1
        names = ", ".join(event.name for event in missed)
        st.warning(
            f"Your run will not include the event{'s' if plural else ''} {names}."
        )

    with st.expander("Events in this model", expanded=False, key=f"{tab}::events"):
        st.dataframe(
            events.events_table(model_events),
            hide_index=True,
            width="stretch",
            key=f"{tab}::events_table",
        )
        timeline = events.segments(model_events)
        if timeline:
            st.plotly_chart(
                events.timeline_figure(timeline, end=end_time, unit=time_unit),
                width="stretch",
                key=f"{tab}::events_timeline",
            )
        else:
            st.caption(
                "These events are state-based — when they fire depends on the "
                "simulation, so they can't be placed on a timeline in advance."
            )


def render_results(tab: str, source: dict):
    """This tab's latest results, from the run request the tab stored.

    Each tab keeps its own request under ``last_run::<tab>``, so running a scan
    in one tab never shows up in another. Only a result for the currently
    selected model is shown — switching models leaves the old request behind,
    but it isn't this model's.
    """
    last_run = st.session_state.get(f"last_run::{tab}")
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
        with st.expander("What went wrong?", key=f"{tab}::run_error"):
            st.write(str(exc))
        # `return`, not st.stop(): st.stop() halts the WHOLE script, which would
        # blank every tab drawn after this one. return only ends this tab's panel.
        return

    # The plot morphs to how many inputs were scanned (see plotting.mode):
    # 0 varying -> kinetics vs time, 1 -> output vs that input, 2+ -> scatter.
    varying = plotting.varying_inputs(last_run["scan_dict"])
    mode = plotting.mode(last_run["scan_dict"])
    try:
        if mode in (plotting.MODE_KINETICS, plotting.MODE_VS_INPUT):
            plot_type = st.radio(
                "Plot type",
                plotting.PLOT_TYPES,
                horizontal=True,
                key=f"{tab}::plot_type",
            )
            if mode == plotting.MODE_KINETICS:
                fig = plotting.kinetics_figure(long, plot_type=plot_type)
            else:
                fig = plotting.vs_input_figure(
                    long,
                    last_run["scan_dict"],
                    last_run["scales"],
                    plot_type=plot_type,
                )
            st.plotly_chart(fig, width="stretch", key=f"{tab}::plot")
        else:
            # 2+ scanned inputs: a scatter of one output (colour, or z+colour in
            # 3-D) over two chosen inputs. These pickers live outside the Run
            # block, so changing them re-renders from the cached result without
            # re-simulating.
            outputs = sorted(long[plotting.COL_OUTPUT_TYPE].unique())
            col_out, col_x, col_y = st.columns(3)
            output_type = col_out.selectbox(
                "Output", outputs, key=f"{tab}::scatter_output"
            )
            x_input = col_x.selectbox(
                "X axis", varying, index=0, key=f"{tab}::scatter_x"
            )
            y_input = col_y.selectbox(
                "Y axis", varying, index=1, key=f"{tab}::scatter_y"
            )
            three_d = st.toggle("3-D view", key=f"{tab}::scatter_3d")
            st.plotly_chart(
                plotting.scatter_figure(
                    long,
                    last_run["scan_dict"],
                    last_run["scales"],
                    x_input,
                    y_input,
                    output_type,
                    three_d=three_d,
                ),
                width="stretch",
                key=f"{tab}::plot",
            )
    except Exception as exc:  # broad on purpose: keep the data, explain the plot
        st.warning(
            "Couldn't draw a plot for this result — here's the raw data instead."
        )
        with st.expander("What went wrong?", key=f"{tab}::plot_error"):
            st.write(str(exc))

    with st.expander("Raw results table", key=f"{tab}::raw"):
        st.dataframe(wide, width="stretch", key=f"{tab}::raw_results")


def render_table_mode(tab: str, source: dict):
    """A whole tab driven by the general inputs table: Inputs | Outputs.

    Inputs are an editable table: every input starts as "Single" at its model
    default, and the Type column decides what each row means. All the
    parsing/validation lives in simulations.py so it stays testable. The editor's
    key includes the tab and the model, so switching models starts a fresh editor
    and two tabs never share one.

    Splitting into columns puts the plot beside the controls that shape it.
    """
    col_inputs, col_outputs = st.columns(2, gap="large")

    with col_inputs:
        st.subheader("Inputs")
        default_table = simulations.default_input_table(
            get_inputs_cached(source["kind"], source["ref"])
        )
        edited = st.data_editor(
            default_table,
            key=f"input_editor::{tab}::{source['kind']}::{source['ref']}",
            hide_index=True,
            width="stretch",
            column_config={
                simulations.COL_PARAM: st.column_config.TextColumn(
                    "Input", disabled=True
                ),
                simulations.COL_VALUE: st.column_config.NumberColumn(
                    "Value", help="Used when Type is Single."
                ),
                simulations.COL_LOWER: st.column_config.NumberColumn(
                    "Lower", help="Grid lower bound."
                ),
                simulations.COL_UPPER: st.column_config.NumberColumn(
                    "Upper", help="Grid upper bound."
                ),
                simulations.COL_TYPE: st.column_config.SelectboxColumn(
                    "Type",
                    options=simulations.INPUT_TYPES,
                    required=True,
                    help="Single = fixed value · Grid = sweep Lower→Upper.",
                ),
                simulations.COL_SCALE: st.column_config.SelectboxColumn(
                    "Scale",
                    options=simulations.INPUT_SCALES,
                    required=True,
                    help="Linear or logarithmic spacing for a Grid scan.",
                ),
                simulations.COL_N: st.column_config.NumberColumn(
                    "n", step=1, help="Number of grid points."
                ),
            },
        )
        scan_dict, input_errors = simulations.build_scan_dict_from_table(edited)

        if input_errors:
            for name, message in input_errors.items():
                st.error(f"**{name}**: {message}")
        else:
            n_sims = prod(len(values) for values in scan_dict.values())
            st.caption(
                f"This will run {n_sims} simulation{'s' if n_sims != 1 else ''}."
            )

        start, end, n_points, time_error = render_time_window(tab)
        render_events_panel(tab, source, end)

        # Streamlit re-runs this whole script on *every* widget change. If we
        # rendered results inside the `if clicked:` block, the plot would vanish
        # the moment the user touched a plot control (that rerun has clicked ==
        # False). So on click we only stash this tab's *request* in session_state;
        # render_results draws from it on every rerun, and run_cached makes that
        # free. The button sits beside the inputs it runs, so it never needs to
        # know which tab is active.
        if st.button(
            "Run simulation",
            type="primary",
            width="stretch",
            key=f"{tab}::run",
            disabled=bool(input_errors) or bool(time_error),
        ):
            st.session_state[f"last_run::{tab}"] = {
                "kind": source["kind"],
                "ref": source["ref"],
                "scan_dict": scan_dict,
                "timepoints": simulations.make_timepoints(start, end, int(n_points)),
                "scales": simulations.input_scales_from_table(edited),
            }

    with col_outputs:
        st.subheader("Outputs")
        render_results(tab, source)


def render_time_course_mode(tab: str, source: dict):
    """Time course: change a few inputs, and everything else runs at its default.

    Inputs are picked as "chips" in a type-to-search box (``st.multiselect``); each
    chip reveals a value box, and removing a chip puts that input back to its
    default. It starts with a newcomer's example (``simulations.example_inputs``).
    Keys include the model, so switching model starts afresh.
    """
    defaults = get_inputs_cached(source["kind"], source["ref"])
    kinds = get_input_kinds_cached(source["kind"], source["ref"])
    model_key = f"{tab}::{source['kind']}::{source['ref']}"

    col_inputs, col_outputs = st.columns(2, gap="large")

    with col_inputs:
        st.subheader("Inputs")
        chosen = st.multiselect(
            "Change inputs",
            options=list(defaults),
            default=simulations.example_inputs(kinds),
            format_func=lambda name: f"{name} · {kinds[name]}",
            placeholder="Type to find an input…",
            key=f"{model_key}::chips",
        )
        changed = {}
        for name in chosen:
            # "%g" keeps tiny and huge values readable (1e-12, not 0.00), and the
            # +/− step scales with the value rather than a fixed 0.01.
            changed[name] = st.number_input(
                name,
                value=defaults[name],
                step=simulations.input_step(defaults[name]),
                format="%g",
                key=f"{model_key}::value::{name}",
            )
        st.caption("Everything else runs at the model's default.")
        scan_dict = simulations.build_scan_dict_from_values(defaults, changed)

        start, end, n_points, time_error = render_time_window(tab)
        render_events_panel(tab, source, end)

        # On click, only stash this tab's request; render_results draws from it on
        # every rerun (see render_table_mode for why).
        if st.button(
            "Run simulation",
            type="primary",
            width="stretch",
            key=f"{tab}::run",
            disabled=bool(time_error),
        ):
            st.session_state[f"last_run::{tab}"] = {
                "kind": source["kind"],
                "ref": source["ref"],
                "scan_dict": scan_dict,
                "timepoints": simulations.make_timepoints(start, end, int(n_points)),
                "scales": {},  # a time course scans nothing, so no axis is logged
            }

    with col_outputs:
        st.subheader("Outputs")
        render_results(tab, source)


# --- Page ---------------------------------------------------------------------
st.title("QuickSimsBio")

# Built-in registry, plus any custom models added this session.
st.session_state.setdefault("user_models", {})
registry = models.merge_user_models(get_registry(), st.session_state.user_models)
options = list(registry)

# The sidebar is for choosing *what* to simulate — a familiar, always-visible
# home that leaves the content area for inputs and results (snapshots will join
# it later). Everything about *how* to run lives in the tabs.
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

# --- Analysis tabs ------------------------------------------------------------
# One tab per analysis mode, mirroring the 0-D / 1-D / 2-D / n-D structure that
# plotting.mode already implements. Tab bodies never call st.stop() (see
# render_results), so the order they are filled in doesn't matter.
tab_time, tab_single, tab_two, tab_multi = st.tabs(
    ["Time course", "Vary single input", "Vary two inputs", "Vary multiple inputs"]
)

with tab_time:
    render_time_course_mode("time_course", source)

with tab_single:
    st.caption(
        "Coming soon — vary one input across a range and see how each output responds."
    )

with tab_two:
    st.caption(
        "Coming soon — vary two inputs together and see how an output changes "
        "across both."
    )

with tab_multi:
    render_table_mode("vary_multiple", source)
