"""QuickSimsBio-UI — Streamlit entry point.

This file is the UI layer only: page config, widgets, and the glue that calls
the backend modules (see CLAUDE.md > Architecture). All model/registry logic
lives in ``models`` so it stays testable without a running Streamlit server.

Streamlit re-runs this whole script top-to-bottom on every interaction, so the
expensive work (listing examples, loading a model) is wrapped in caches below.
"""

import streamlit as st

# app/ is on sys.path when Streamlit runs this file, so sibling modules import
# as top-level names (this mirrors how the tests are configured).
import models

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


st.title("QuickSimsBio")
st.caption("Run mechanistic model simulations — quickly.")

registry = get_registry()

# index=None + placeholder gives the greyed "Choose a model…" prompt and means
# nothing is selected on first load, so we don't auto-load a model.
choice = st.selectbox(
    "Model",
    options=list(registry),
    index=None,
    placeholder="Choose a model…",
)

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
