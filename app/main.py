"""QuickSimsBio-UI — Streamlit entry point.

This file is the UI layer only: page config, widgets, and the glue that calls
the backend modules (see CLAUDE.md > Architecture). All model/registry logic
lives in ``models`` so it stays testable without a running Streamlit server.

Streamlit re-runs this whole script top-to-bottom on every interaction, so the
expensive work (listing examples, loading a model) is wrapped in caches below.
"""

from math import prod
from pathlib import Path

import streamlit as st

# app/ is on sys.path when Streamlit runs this file, so sibling modules import
# as top-level names (this mirrors how the tests are configured).
import models
import plotting
import simulations

st.set_page_config(page_title="QuickSimsBio", page_icon="🧪", layout="centered")


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
            st.error(f"Couldn't load **{name}**: {exc}")
            return
        st.session_state.user_models[name] = source
        st.session_state.selected_model = name  # auto-select it on the rerun
        st.toast(f"Added **{name}** to your menu for this session.", icon="✅")
        st.rerun()


st.title("QuickSimsBio")
st.caption("Run mechanistic model simulations — quickly.")

# Built-in registry, plus any custom models added this session.
st.session_state.setdefault("user_models", {})
registry = models.merge_user_models(get_registry(), st.session_state.user_models)
options = list(registry)

# Keep selection in our OWN state (not the selectbox's key) so the dialog can set
# it freely, and feed it back as the index. Feeding the index also restores the
# choice when the options list changes (e.g. a custom model was just added).
selected = st.session_state.get("selected_model")
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
    st.info("Pick a model to get started.")
    st.stop()

source = registry[choice]
try:
    load_model(source["kind"], source["ref"])
except Exception as exc:  # broad on purpose: show a friendly message, not a traceback
    st.error(f"Couldn't load **{choice}**: {exc}")
    st.stop()

st.success(f"Loaded **{choice}**.")

# --- Run a simulation ---------------------------------------------------------
# Inputs are an editable table: every input starts as "Single" at its model
# default, and the Type column decides what each row means. The result is shown
# as a raw table — adaptive plots come later (M4). main.py only renders widgets
# and wires them to the pure adapter functions in simulations.py; all the
# parsing/validation lives there so it stays testable.
st.subheader("Run a simulation")

# A fresh editor per model (key includes kind/ref) so switching models doesn't
# carry edits across; the cached default table is the stable baseline and the
# editor's `key` persists the user's edits, so we read the *return* value.
default_table = simulations.default_input_table(get_inputs_cached(source["kind"], source["ref"]))
edited = st.data_editor(
    default_table,
    key=f"input_editor::{source['kind']}::{source['ref']}",
    hide_index=True,
    use_container_width=True,
    column_config={
        simulations.COL_PARAM: st.column_config.TextColumn("Input", disabled=True),
        simulations.COL_VALUE: st.column_config.NumberColumn("Value", help="Used when Type is Single."),
        simulations.COL_LOWER: st.column_config.NumberColumn("Lower", help="Grid lower bound."),
        simulations.COL_UPPER: st.column_config.NumberColumn("Upper", help="Grid upper bound."),
        simulations.COL_TYPE: st.column_config.SelectboxColumn(
            "Type", options=simulations.INPUT_TYPES, required=True,
            help="Single = fixed value · Grid = sweep Lower→Upper · Random arrives in M4.",
        ),
        simulations.COL_SCALE: st.column_config.SelectboxColumn(
            "Scale", options=simulations.INPUT_SCALES, required=True,
            help="Linear or logarithmic spacing for a Grid scan.",
        ),
        simulations.COL_N: st.column_config.NumberColumn("n", step=1, help="Number of grid points."),
    },
)
scan_dict, input_errors = simulations.build_scan_dict_from_table(edited)

if input_errors:
    for name, message in input_errors.items():
        st.error(f"**{name}**: {message}")
else:
    n_sims = prod(len(values) for values in scan_dict.values())
    st.caption(f"This will run {n_sims} simulation{'s' if n_sims != 1 else ''}.")

col_time, col_points = st.columns(2)
end_time = col_time.number_input(
    "Simulation time",
    min_value=0.0,
    value=1000.0,
    step=100.0,
    help="Length of the time course, in the model's own time units.",
)
n_points = col_points.number_input(
    "Number of points",
    min_value=2,
    value=300,
    step=50,
    help="How many timepoints to record across the run.",
)

# Streamlit re-runs this whole script on *every* widget change. If we rendered the
# results inside the `if run_clicked:` block, the plot would vanish the moment the
# user touched a plot control (that rerun has run_clicked == False). So on click we
# only stash the *request* in session_state; the actual results + their controls are
# rendered below, outside the button block. run_cached makes the re-fetch free, so
# changing a plot control re-renders WITHOUT re-simulating.
if st.button("Run simulation", type="primary", disabled=bool(input_errors)):
    st.session_state["last_run"] = {
        "kind": source["kind"],
        "ref": source["ref"],
        "scan_dict": scan_dict,
        "timepoints": simulations.make_timepoints(end_time, int(n_points)),
        "scales": simulations.input_scales_from_table(edited),
    }

# --- Results (adaptive plot) --------------------------------------------------
# Only show a result for the model that's currently selected — switching models
# leaves the old run in session_state, but it isn't this model's, so we skip it.
last_run = st.session_state.get("last_run")
if last_run and (last_run["kind"], last_run["ref"]) == (source["kind"], source["ref"]):
    wide, long = run_cached(
        last_run["kind"], last_run["ref"], last_run["scan_dict"], last_run["timepoints"]
    )

    # The plot morphs to how many inputs were scanned (see plotting.mode):
    # 0 varying -> kinetics vs time, 1 -> output vs that input, 2+ -> scatter.
    varying = plotting.varying_inputs(last_run["scan_dict"])
    mode = plotting.mode(last_run["scan_dict"])
    if mode in (plotting.MODE_KINETICS, plotting.MODE_VS_INPUT):
        plot_type = st.radio(
            "Plot type", plotting.PLOT_TYPES, horizontal=True, key="plot_type"
        )
        if mode == plotting.MODE_KINETICS:
            fig = plotting.kinetics_figure(long, plot_type=plot_type)
        else:
            fig = plotting.vs_input_figure(
                long, last_run["scan_dict"], last_run["scales"], plot_type=plot_type
            )
        st.plotly_chart(fig, use_container_width=True)
    else:
        # 2+ scanned inputs: a scatter of one output (colour, or z+colour in 3-D)
        # over two chosen inputs. These pickers live outside the Run block, so
        # changing them re-renders from the cached result without re-simulating.
        outputs = sorted(long[plotting.COL_OUTPUT_TYPE].unique())
        col_out, col_x, col_y = st.columns(3)
        output_type = col_out.selectbox("Output", outputs, key="scatter_output")
        x_input = col_x.selectbox("X axis", varying, index=0, key="scatter_x")
        y_input = col_y.selectbox("Y axis", varying, index=1, key="scatter_y")
        three_d = st.toggle("3-D view", key="scatter_3d")
        st.plotly_chart(
            plotting.scatter_figure(
                long, last_run["scan_dict"], last_run["scales"],
                x_input, y_input, output_type, three_d=three_d,
            ),
            use_container_width=True,
        )

    with st.expander("Raw results table"):
        st.dataframe(wide, use_container_width=True)
