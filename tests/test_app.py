"""UI smoke tests for the Streamlit entry point (app/main.py).

Uses Streamlit's AppTest to run the app headlessly: no browser, no server. It
can find widgets, set their values and rerun the script, but it can't see CSS or
layout on screen, so how things *look* is still checked by eye in a browser.
"""

import json
import urllib.error
from pathlib import Path
from unittest import mock

import events
import models
import simulations
import streamlit as st
from streamlit.testing.v1 import AppTest

# Absolute, built from this file's location. Streamlit 1.65 resolves a *relative*
# AppTest path against the calling test file (tests/), not the working directory,
# so "app/main.py" would point at tests/app/main.py.
APP_SCRIPT = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


def _result_tables(at):
    """The raw results tables: those inside the "Raw results table" expander.
    Found by where they sit, since AppTest doesn't report a dataframe's key."""
    return [
        table
        for expander in at.expander
        if expander.label == "Raw results table"
        for table in expander.dataframe
    ]


def _picker(at):
    """The "Add an input" picker. Found by label: at.selectbox[0] is the sidebar's
    Model picker, now that Time course isn't inside a tab of its own."""
    return next(s for s in at.selectbox if s.label == "Add an input")


def _number_input(at, label):
    """A number_input by its label — robust to sibling widgets being added."""
    return next(n for n in at.number_input if n.label == label)


def test_app_opens_with_the_default_model_loaded():
    # On a phone the sidebar starts hidden, so a first visit must land on a
    # working app — not "pick a model" with no picker in sight.
    at = AppTest.from_file(APP_SCRIPT).run()

    assert not at.exception
    assert at.sidebar.selectbox[0].label == "Model"
    assert at.sidebar.selectbox[0].value == models.DEFAULT_MODEL
    assert len(at.tabs) == 0  # one view, no tabs (since 7 Oct)
    assert len(at.get("plotly_chart")) == 1  # a result with nothing clicked
    assert any(b.label == "Use custom model" for b in at.button)

    # Named and described in the main area, with a pointer to change it.
    model_caption = next(c.value for c in at.caption if models.DEFAULT_MODEL in c.value)
    assert models.MODEL_DESCRIPTIONS[models.DEFAULT_MODEL] in model_caption
    assert "default model" in model_caption and "sidebar" in model_caption


def _species(name):
    """How a species' control is labelled: it's the *initial* concentration."""
    return f"{name} (initial concentration)"


def _slider(tab, label):
    return next(s for s in tab.select_slider if s.label == label)


def _keyed(elements, suffix):
    """The element whose key ends with ``suffix`` (e.g. '::remove::X')."""
    return next(e for e in elements if e.key and e.key.endswith(suffix))


def _brusselator():
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    return at.run()


def test_time_course_offers_an_input_picker_and_sliders_instead_of_the_table():
    at = _brusselator()
    assert not at.exception

    assert _picker(at).value is None
    # brusselator has 6 inputs, all species: more than 5, so the newcomer example
    # is the first 3 of that kind — each a slider sitting on its default value.
    request = at.session_state["last_run::time_course"]["scan_dict"]
    for name in ("X", "Y", "A"):
        assert _slider(at, _species(name)).value == request[name][0]
    assert "Run simulation" not in [b.label for b in at.button]  # it auto-runs


def test_time_window_sits_with_the_plot_and_inputs_in_the_narrow_column():
    # Layout: inputs on the left; the time window above the plot on the right,
    # since it decides what the plot's x-axis shows.
    at = _brusselator()
    inputs, results = at.columns[0], at.columns[1]  # the two top-level columns
    assert {"Start time", "End time"} <= {n.label for n in results.number_input}
    assert "End time" not in {n.label for n in inputs.number_input}
    assert inputs.selectbox[0].label == "Add an input"


def test_time_course_reruns_when_a_slider_moves_and_holds_the_rest_at_default():
    at = _brusselator()
    _number_input(at, "Number of points").set_value(20).run()
    before = at.session_state["last_run::time_course"]["scan_dict"]

    # Move X to the stop nearest twice its default.
    x_default = before["X"][0]
    stops = simulations.slider_options(
        simulations.default_slider_settings(x_default), x_default
    )
    target = simulations.nearest_option(stops, 2 * x_default, log=True)
    _slider(at, _species("X")).set_value(target).run()  # this alone re-runs

    assert not at.exception
    after = at.session_state["last_run::time_course"]["scan_dict"]
    assert after["X"] == [target]
    assert after["Y"] == before["Y"]  # a slider left alone
    assert len(after["B"]) == 1  # not chosen: still one run, held at default
    assert len(at.get("plotly_chart")) == 1


def _plot_spec(tab) -> dict:
    """The tab's results plot, as Plotly's JSON spec."""
    return json.loads(tab.get("plotly_chart")[-1].proto.spec)


def _plotted_species(tab) -> list[str]:
    return [trace["name"] for trace in _plot_spec(tab)["data"]]


def _legend(tab, output):
    """The app's own legend entry for ``output``."""
    return next(b for b in tab.button if b.label == output and "::legend::" in b.key)


def test_hidden_outputs_stay_hidden_when_a_slider_moves():
    # Plotly's own legend clicks are lost on every redraw (Streamlit treats new
    # data as a brand-new chart), so the app's own legend holds that choice.
    at = _brusselator()
    assert _plot_spec(at)["layout"]["showlegend"] is False  # one legend: ours

    _legend(at, "[Y]").click().run()  # hide Y
    assert "[Y]" not in _plotted_species(at)

    x_default = at.session_state["last_run::time_course"]["scan_dict"]["X"][0]
    stops = simulations.slider_options(
        simulations.default_slider_settings(x_default), x_default
    )
    target = simulations.nearest_option(stops, 2 * x_default, log=True)
    _slider(at, _species("X")).set_value(target).run()  # a redraw

    assert not at.exception
    assert "[Y]" not in _plotted_species(at)  # still hidden after the redraw
    assert "[X]" in _plotted_species(at)

    _legend(at, "[Y]").click().run()  # and clicking again brings it back
    assert "[Y]" in _plotted_species(at)


def test_time_course_adds_and_removes_inputs():
    at = _brusselator()

    _picker(at).set_value("B").run()  # add B from the picker
    assert not at.exception
    assert _slider(at, _species("B"))
    assert _picker(at).value is None  # the picker clears itself

    _keyed(at.button, "::remove::X").click().run()  # the row's × button
    assert not at.exception
    assert _species("X") not in [s.label for s in at.select_slider]
    assert (
        "X · initial concentration" in _picker(at).options
    )  # back in the picker


def test_time_course_slider_settings_change_its_range_and_scale():
    at = _brusselator()

    _keyed(at.number_input, "::min::X").set_value(1.0).run()
    _keyed(at.number_input, "::max::X").set_value(5.0).run()
    _keyed(at.toggle, "::log::X").set_value(False).run()
    assert not at.exception
    stops = _slider(at, _species("X")).options  # as displayed
    assert (stops[0], stops[-1]) == ("1", "5")  # the new range, now linear

    _slider(at, _species("X")).set_value(4.0).run()  # and it still works
    assert not at.exception
    assert at.session_state["last_run::time_course"]["scan_dict"]["X"] == [4.0]


def test_time_course_slider_settings_that_make_no_sense_fall_back_safely():
    at = _brusselator()

    _keyed(at.number_input, "::min::X").set_value(1e6).run()  # above Max

    assert not at.exception
    assert any("Min must be below Max" in c.value for c in at.caption)


def test_time_course_gives_a_zero_default_a_linear_zero_to_one_slider():
    # A default of 0 gives no hint of a sensible range, so its slider starts at a
    # linear 0 – 1, sitting on 0, for the user to widen in ⚙.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "Cubic Ternary Complex Activation"
    at.run()
    assert not at.exception

    ligand = _slider(at, _species("L"))  # CTCA's ligand, default 0
    assert ligand.value == 0.0
    assert (ligand.options[0], ligand.options[-1]) == ("0", "1")


def test_app_describes_a_chosen_model_without_the_default_model_pointer():
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()

    assert not at.exception
    model_caption = next(c.value for c in at.caption if "brusselator" in c.value)
    assert models.MODEL_DESCRIPTIONS["brusselator"] in model_caption
    assert "default model" not in model_caption


def test_app_auto_selects_model_from_session_state():
    # Mimics what the "use custom model" dialog does on success: set the
    # selection in our own state, then the app should select AND load it.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()

    assert not at.exception
    assert at.sidebar.selectbox[0].value == "brusselator"


def test_app_blames_biomodels_when_its_server_fails():
    # EBI returned 504s on 2026-09-28. The app must say it's BioModels' end, not
    # show a raw error. The download is mocked, so this never touches the network.
    st.cache_resource.clear()  # a cached real load must not mask the failure
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["user_models"] = {
        "Some BioModel": {"kind": "biomodels", "ref": "MODEL0000000001"}
    }
    at.session_state["selected_model"] = "Some BioModel"

    gateway_timeout = urllib.error.HTTPError(
        "https://biomodels", 504, "Gateway Timeout", None, None
    )
    with mock.patch("models.bsc.load_biomodel", side_effect=gateway_timeout):
        at.run()

    assert not at.exception
    assert any(
        "BioModels" in e.value and "isn't responding" in e.value
        for e in at.sidebar.error
    )


def test_app_runs_a_default_simulation_and_shows_a_results_table():
    # End-to-end slice: a loaded model → Time course runs by itself (all inputs at
    # default) → a results table. This actually runs COPASI via quicksimsbio, so
    # we shrink the run (few points) and give it a generous timeout.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    _number_input(at, "Number of points").set_value(20).run(timeout=60)

    assert not at.exception
    results = _result_tables(at)
    assert len(results) == 1
    assert results[0].value["Sim_Num"].nunique() == 1  # one default run


def test_app_shows_a_friendly_error_when_a_run_fails():
    # A failing simulation must degrade to a message, never a traceback. Time
    # course runs on load, so the failure is patched in *before* the first run
    # (and the cache cleared so an earlier good result can't mask it).
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("simulations.run", side_effect=RuntimeError("boom")):
        at.run()

    assert not at.exception
    assert any("complete the simulation" in e.value.lower() for e in at.error)
    assert _result_tables(at) == []  # no data, so no results table


def test_app_keeps_only_the_model_choice_in_the_sidebar():
    # The sidebar is for choosing *what* to simulate; how to run it (the time
    # window) sits beside the plot it shapes.
    at = _brusselator()
    assert not at.exception

    assert {"Start time", "End time", "Number of points"} <= {
        n.label for n in at.number_input
    }
    sidebar_buttons = [b.label for b in at.sidebar.button]
    assert "Use custom model" in sidebar_buttons  # proves the sidebar query sees things
    assert list(at.sidebar.number_input) == []


def test_app_a_failed_run_still_shows_the_inputs():
    # A failed run must end only the results panel, so the user can change an
    # input and try again. st.stop() would halt the whole script instead.
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("simulations.run", side_effect=RuntimeError("boom")):
        at.run()  # Time course runs on load, and fails

    assert not at.exception
    assert any("complete the simulation" in e.value.lower() for e in at.error)
    assert _picker(at)  # the inputs are still there to change
    assert _slider(at, _species("X"))


def test_app_keeps_the_raw_table_when_plotting_fails():
    # If the run succeeds but the figure builder throws, we keep the raw data and
    # warn — rather than blanking the view. Force it by patching kinetics_figure
    # (a default all-Single run is 0-D, so kinetics_figure is what gets called).
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("plotting.kinetics_figure", side_effect=RuntimeError("bad fig")):
        at.run(timeout=60)  # Time course runs on load; drawing its plot fails

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


def _events_status(at) -> list[str]:
    """Captions carrying the missed-events status line ('Runs to … later')."""
    return [c.value for c in at.caption if c.value.startswith("Runs to")]


def test_app_notes_when_the_run_window_misses_a_models_events():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    # Default End time is 1000; the event fires at 1e6, so the run never reaches it.
    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()

    assert not at.exception
    assert len(_events_status(at)) == 1
    assert "1 of 1 event happens later" in _events_status(at)[0]
    assert list(at.warning) == []  # a neutral note now, not a yellow warning


def test_app_clears_the_miss_note_once_the_window_reaches_the_event():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()
        _number_input(at, "End time").set_value(2_000_000.0).run()

    assert not at.exception
    assert _events_status(at) == []


def test_app_shows_no_events_section_for_a_model_without_events():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[]):
        at.run()

    assert not at.exception
    assert _events_status(at) == []
    assert not any(e.label == "Events in this model" for e in at.expander)
