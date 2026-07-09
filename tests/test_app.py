"""UI smoke tests for the Streamlit entry point (app/main.py).

Uses Streamlit's AppTest to run the app headlessly. Note: ``st.data_editor``
renders as a read-only ``Dataframe`` element in AppTest — its cells can't be
edited programmatically — so the grid/error logic is covered by the pure-adapter
unit tests in ``test_simulations.py``. Here we check the table *renders* with the
right defaults and that a default (all-Single) run produces a results table.
"""

from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest


def _input_editor(at):
    """The inputs-table data_editor (its key starts with 'input_editor')."""
    return next(d for d in at.dataframe if d.key and d.key.startswith("input_editor"))


def _result_tables(at):
    """The st.dataframe result tables (everything that isn't the input editor)."""
    return [d for d in at.dataframe if not (d.key and d.key.startswith("input_editor"))]


def test_app_renders_model_picker_with_nothing_selected():
    at = AppTest.from_file("app/main.py").run()

    assert not at.exception
    assert at.selectbox[0].label == "Model"
    assert at.selectbox[0].value is None  # placeholder shown, no model loaded
    assert any(b.label == "Use custom model" for b in at.button)


def test_app_auto_selects_model_from_session_state():
    # Mimics what the "use custom model" dialog does on success: set the
    # selection in our own state, then the app should select AND load it.
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()

    assert not at.exception
    assert at.selectbox[0].value == "brusselator"


def test_app_renders_inputs_table_with_every_input_as_single():
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    table = _input_editor(at).value
    assert "X" in list(table["Param"])  # brusselator exposes its species as inputs
    assert set(table["Type"]) == {"Single"}  # every input starts held at its default


def test_app_runs_a_default_simulation_and_shows_a_results_table():
    # End-to-end slice: a loaded model + Run (all inputs Single at default) → a
    # results table. This actually runs COPASI via quicksimsbio, so we shrink the
    # run (few points) and give the click a generous timeout.
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    at.number_input[1].set_value(20).run()  # number_input[1] = "Number of points"

    next(b for b in at.button if b.label == "Run simulation").click().run(timeout=60)

    assert not at.exception
    results = _result_tables(at)
    assert len(results) == 1
    assert results[0].value["Sim_Num"].nunique() == 1  # one default run


def test_app_shows_a_friendly_error_when_a_run_fails():
    # A failing simulation must degrade to a message, never a traceback. We force
    # the failure by patching simulations.run to raise (clearing the cache first so
    # a previously-cached good result can't mask it).
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    with mock.patch("simulations.run", side_effect=RuntimeError("boom")):
        next(b for b in at.button if b.label == "Run simulation").click().run()

    assert not at.exception  # st.stop() is normal control flow, not a crash
    assert any("complete the simulation" in e.value.lower() for e in at.error)
    assert _result_tables(at) == []  # no data, so no results table


def test_app_keeps_the_raw_table_when_plotting_fails():
    # If the run succeeds but the figure builder throws, we keep the raw data and
    # warn — rather than blanking the view. Force it by patching kinetics_figure
    # (a default all-Single run is 0-D, so kinetics_figure is what gets called).
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    at.number_input[1].set_value(20).run()  # shrink the real COPASI run

    with mock.patch("plotting.kinetics_figure", side_effect=RuntimeError("bad fig")):
        next(b for b in at.button if b.label == "Run simulation").click().run(timeout=60)

    assert not at.exception
    assert any("raw data" in w.value.lower() for w in at.warning)
    assert len(_result_tables(at)) == 1  # raw results table still rendered
