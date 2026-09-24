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

import events


def _input_editor(at):
    """The inputs-table data_editor (its key starts with 'input_editor')."""
    return next(d for d in at.dataframe if d.key and d.key.startswith("input_editor"))


def _result_tables(at):
    """The st.dataframe result tables (everything that isn't the input editor)."""
    return [d for d in at.dataframe if not (d.key and d.key.startswith("input_editor"))]


def _number_input(at, label):
    """A number_input by its label — robust to sibling widgets being added."""
    return next(n for n in at.number_input if n.label == label)


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

    _number_input(at, "Number of points").set_value(20).run()

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


def test_app_shows_one_tab_per_analysis_mode():
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    assert [tab.label for tab in at.tabs] == [
        "Time course",
        "Vary single input",
        "Vary two inputs",
        "Vary multiple inputs",
    ]


def test_app_keeps_placeholder_tabs_filled_when_a_run_fails():
    # Time course calls st.stop() on a failed run, which halts the script there.
    # The other tabs are filled *before* it in main.py so that can't blank them;
    # this pins that ordering, since nothing else would notice it breaking.
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()

    with mock.patch("simulations.run", side_effect=RuntimeError("boom")):
        next(b for b in at.button if b.label == "Run simulation").click().run()

    assert not at.exception
    for placeholder_tab in at.tabs[1:]:
        assert any("coming soon" in c.value.lower() for c in placeholder_tab.caption)


def test_app_keeps_the_raw_table_when_plotting_fails():
    # If the run succeeds but the figure builder throws, we keep the raw data and
    # warn — rather than blanking the view. Force it by patching kinetics_figure
    # (a default all-Single run is 0-D, so kinetics_figure is what gets called).
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    _number_input(at, "Number of points").set_value(20).run()  # shrink the real run

    with mock.patch("plotting.kinetics_figure", side_effect=RuntimeError("bad fig")):
        next(b for b in at.button if b.label == "Run simulation").click().run(timeout=60)

    assert not at.exception
    assert any("raw data" in w.value.lower() for w in at.warning)
    assert len(_result_tables(at)) == 1  # raw results table still rendered


# --- events section -----------------------------------------------------------
# events.read_events is patched so these stay hermetic (no BioModels download).
# brusselator is the loaded model; only its event list is faked.
def _late_event():
    return events.Event(
        name="add drug", trigger="Time > 1000000", time=1_000_000.0, assignments=()
    )


def test_app_warns_when_the_run_window_misses_a_models_events():
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"

    # Default End time is 1000; the event fires at 1e6, so the run never reaches it.
    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()

    assert not at.exception
    assert any("add drug" in w.value for w in at.warning)


def test_app_clears_the_miss_warning_once_the_window_reaches_the_event():
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()
        _number_input(at, "End time").set_value(2_000_000.0).run()

    assert not at.exception
    assert not any("add drug" in w.value for w in at.warning)


def test_app_shows_no_events_section_for_a_model_without_events():
    st.cache_data.clear()
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[]):
        at.run()

    assert not at.exception
    assert not any("End time" in w.value for w in at.warning)
    assert _result_tables(at) == []  # no events table, no results
