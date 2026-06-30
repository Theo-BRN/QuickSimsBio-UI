"""QuickSimsBio-UI — Streamlit entry point.

This file is the UI layer only: page config, widgets, and the glue that calls
the backend modules (see CLAUDE.md > Architecture). All model/registry logic
lives in ``models`` so it stays testable without a running Streamlit server.

Streamlit re-runs this whole script top-to-bottom on every interaction, so the
expensive work (listing examples, loading a model) is wrapped in caches below.
"""

from pathlib import Path

import streamlit as st

# app/ is on sys.path when Streamlit runs this file, so sibling modules import
# as top-level names (this mirrors how the tests are configured).
import models
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
    """The model's input skeleton (``{input_name: None}``), cached per model.

    Same pattern as ``run_cached``: keyed on the hashable ``(kind, ref)`` and the
    live model handle re-fetched from the cache_resource loader inside. The
    result is a plain dict, so cache_data stores it happily.
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
# Inputs default to "Hold" (model default). Switch one to "Scan" and give a list
# of values to sweep it. The result is shown as a raw table — adaptive plots come
# later (M4). main.py only wires widgets to the pure adapter functions in
# simulations.py; the parsing/validation lives there so it stays testable.
st.subheader("Run a simulation")

inputs = get_inputs_cached(source["kind"], source["ref"])

# Collect the inputs the user chose to scan. parse_grid_values turns the typed
# text into a list (or None = hold); a bad entry raises, which we surface inline.
overrides: dict[str, list[float] | None] = {}
input_errors: dict[str, str] = {}
with st.expander(f"Inputs ({len(inputs)})", expanded=False):
    st.caption(
        "Anything left on **Hold** stays at the model's default. Switch an input "
        "to **Scan** and enter values to sweep it (e.g. `0.1, 1, 10`)."
    )
    for name in inputs:
        col_mode, col_vals = st.columns([1, 2])
        mode = col_mode.selectbox(name, ["Hold", "Scan"], key=f"mode_{name}")
        if mode == "Scan":
            text = col_vals.text_input(
                name,
                key=f"vals_{name}",
                placeholder="e.g. 0.1, 1, 10",
                label_visibility="collapsed",
            )
            try:
                overrides[name] = simulations.parse_grid_values(text)
            except ValueError as exc:
                input_errors[name] = str(exc)

for name, message in input_errors.items():
    st.error(f"**{name}**: {message}")

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

if st.button("Run simulation", type="primary", disabled=bool(input_errors)):
    scan_dict = simulations.build_scan_dict(inputs, overrides)
    timepoints = simulations.make_timepoints(end_time, int(n_points))
    result = run_cached(source["kind"], source["ref"], scan_dict, timepoints)
    st.dataframe(result, use_container_width=True)
