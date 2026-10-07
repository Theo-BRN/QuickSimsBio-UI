"""UI smoke tests for the Streamlit entry point (app/main.py).

Uses Streamlit's AppTest to run the app headlessly. Note: ``st.data_editor``
renders as a read-only ``Dataframe`` element in AppTest — its cells can't be
edited programmatically — so the grid/error logic is covered by the pure-adapter
unit tests in ``test_simulations.py``. Here we check the table *renders* with the
right defaults and that a default (all-Single) run produces a results table.
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


def _input_editor(at):
    """The inputs-table data_editor (its key starts with 'input_editor')."""
    return next(d for d in at.dataframe if d.key and d.key.startswith("input_editor"))


def _result_tables(at):
    """The st.dataframe result tables (everything that isn't the input editor)."""
    return [d for d in at.dataframe if not (d.key and d.key.startswith("input_editor"))]


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
    assert len(at.tabs) == 2  # straight into the analysis tabs
    assert len(at.tabs[0].get("plotly_chart")) == 1  # a result with nothing clicked
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

    time_course, vary_multiple = at.tabs[0], at.tabs[1]
    picker = time_course.selectbox[0]
    assert picker.label == "Add an input" and picker.value is None
    # brusselator has 6 inputs, all species: more than 5, so the newcomer example
    # is the first 3 of that kind — each a slider sitting on its default value.
    request = at.session_state["last_run::time_course"]["scan_dict"]
    for name in ("X", "Y", "A"):
        assert _slider(time_course, _species(name)).value == request[name][0]
    assert "Run simulation" not in [b.label for b in time_course.button]  # it auto-runs

    def has_table(tab):
        return any(d.key and d.key.startswith("input_editor") for d in tab.dataframe)

    assert not has_table(time_course)
    assert has_table(vary_multiple)  # the general table lives on there


def test_time_window_sits_with_the_plot_and_inputs_in_the_narrow_column():
    # Layout: inputs on the left; the time window above the plot on the right,
    # since it decides what the plot's x-axis shows. Same in both tabs.
    at = _brusselator()
    for tab in at.tabs:
        inputs, results = tab.columns[0], tab.columns[1]  # the two top-level columns
        assert {"Start time", "End time"} <= {n.label for n in results.number_input}
        assert "End time" not in {n.label for n in inputs.number_input}
    assert at.tabs[0].columns[0].selectbox[0].label == "Add an input"


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
    _slider(at.tabs[0], _species("X")).set_value(target).run()  # this alone re-runs

    assert not at.exception
    after = at.session_state["last_run::time_course"]["scan_dict"]
    assert after["X"] == [target]
    assert after["Y"] == before["Y"]  # a slider left alone
    assert len(after["B"]) == 1  # not chosen: still one run, held at default
    assert len(at.tabs[0].get("plotly_chart")) == 1


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
    assert _plot_spec(at.tabs[0])["layout"]["showlegend"] is False  # one legend: ours

    _legend(at.tabs[0], "[Y]").click().run()  # hide Y
    assert "[Y]" not in _plotted_species(at.tabs[0])

    x_default = at.session_state["last_run::time_course"]["scan_dict"]["X"][0]
    stops = simulations.slider_options(
        simulations.default_slider_settings(x_default), x_default
    )
    target = simulations.nearest_option(stops, 2 * x_default, log=True)
    _slider(at.tabs[0], _species("X")).set_value(target).run()  # a redraw

    assert not at.exception
    assert "[Y]" not in _plotted_species(at.tabs[0])  # still hidden after the redraw
    assert "[X]" in _plotted_species(at.tabs[0])

    _legend(at.tabs[0], "[Y]").click().run()  # and clicking again brings it back
    assert "[Y]" in _plotted_species(at.tabs[0])


def test_time_course_adds_and_removes_inputs():
    at = _brusselator()

    at.tabs[0].selectbox[0].set_value("B").run()  # add B from the picker
    assert not at.exception
    assert _slider(at.tabs[0], _species("B"))
    assert at.tabs[0].selectbox[0].value is None  # the picker clears itself

    _keyed(at.tabs[0].button, "::remove::X").click().run()  # the row's × button
    assert not at.exception
    assert _species("X") not in [s.label for s in at.tabs[0].select_slider]
    assert (
        "X · initial concentration" in at.tabs[0].selectbox[0].options
    )  # back in the picker


def test_time_course_slider_settings_change_its_range_and_scale():
    at = _brusselator()

    _keyed(at.tabs[0].number_input, "::min::X").set_value(1.0).run()
    _keyed(at.tabs[0].number_input, "::max::X").set_value(5.0).run()
    _keyed(at.tabs[0].toggle, "::log::X").set_value(False).run()
    assert not at.exception
    stops = _slider(at.tabs[0], _species("X")).options  # as displayed
    assert (stops[0], stops[-1]) == ("1", "5")  # the new range, now linear

    _slider(at.tabs[0], _species("X")).set_value(4.0).run()  # and it still works
    assert not at.exception
    assert at.session_state["last_run::time_course"]["scan_dict"]["X"] == [4.0]


def test_time_course_slider_settings_that_make_no_sense_fall_back_safely():
    at = _brusselator()

    _keyed(at.tabs[0].number_input, "::min::X").set_value(1e6).run()  # above Max

    assert not at.exception
    assert any("Min must be below Max" in c.value for c in at.tabs[0].caption)


def test_time_course_gives_a_zero_default_a_linear_zero_to_one_slider():
    # A default of 0 gives no hint of a sensible range, so its slider starts at a
    # linear 0 – 1, sitting on 0, for the user to widen in ⚙.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "Cubic Ternary Complex Activation"
    at.run()
    assert not at.exception

    ligand = _slider(at.tabs[0], _species("L"))  # CTCA's ligand, default 0
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


def test_app_renders_inputs_table_with_every_input_as_single():
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    table = _input_editor(at).value
    assert "X" in list(table["Param"])  # brusselator exposes its species as inputs
    assert set(table["Type"]) == {"Single"}  # every input starts held at its default


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
    assert any("complete the simulation" in e.value.lower() for e in at.tabs[0].error)
    assert _result_tables(at) == []  # no data, so no results table


def test_app_shows_one_tab_per_analysis_mode():
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    assert [tab.label for tab in at.tabs] == ["Time course", "Vary multiple inputs"]


def test_app_keeps_the_time_window_and_run_in_the_tabs_not_the_sidebar():
    # How to run lives in each tab, beside the inputs it runs, so it never needs
    # to know which tab is active. The sidebar is for choosing a model only.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    time_course, vary_multiple = at.tabs[0], at.tabs[1]
    assert {"Start time", "End time", "Number of points"} <= {
        n.label for n in time_course.number_input
    }
    assert "Run simulation" in [b.label for b in vary_multiple.button]
    sidebar_buttons = [b.label for b in at.sidebar.button]
    assert "Use custom model" in sidebar_buttons  # proves the sidebar query sees things
    assert "Run simulation" not in sidebar_buttons
    assert list(at.sidebar.number_input) == []


def test_app_a_failed_run_in_one_tab_leaves_every_other_tab_intact():
    # A failed run must end only its own tab's results panel. st.stop() would
    # halt the whole script and blank every tab drawn after it — nothing visible
    # would flag that, so this pins it. Time course (first tab) fails; the tabs
    # after it must still be fully drawn.
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("simulations.run", side_effect=RuntimeError("boom")):
        at.run()  # Time course runs on load, and fails

    assert not at.exception
    assert any("complete the simulation" in e.value.lower() for e in at.tabs[0].error)
    vary_multiple = at.tabs[1]  # drawn after the failure, and still complete
    assert "Run simulation" in [b.label for b in vary_multiple.button]
    assert any(
        d.key and d.key.startswith("input_editor") for d in vary_multiple.dataframe
    )


def test_app_keeps_each_tabs_results_to_itself():
    # Each tab stores its own run request, so a run in Time course must not show
    # up as results in Vary multiple inputs.
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"
    at.run()

    _number_input(at, "Number of points").set_value(20).run(timeout=60)  # Time course's

    assert not at.exception
    time_course, vary_multiple = at.tabs[0], at.tabs[1]
    assert len(time_course.get("plotly_chart")) == 1
    assert len(vary_multiple.get("plotly_chart")) == 0
    assert any("will appear here" in c.value for c in vary_multiple.caption)


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


def test_app_warns_when_the_run_window_misses_a_models_events():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    # Default End time is 1000; the event fires at 1e6, so the run never reaches it.
    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()

    assert not at.exception
    assert any("add drug" in w.value for w in at.warning)


def test_app_clears_the_miss_warning_once_the_window_reaches_the_event():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[_late_event()]):
        at.run()
        _number_input(at, "End time").set_value(2_000_000.0).run()  # Time course's

    assert not at.exception
    time_course, vary_multiple = at.tabs[0], at.tabs[1]
    assert not any("add drug" in w.value for w in time_course.warning)
    # Each tab's time window is its own: Vary multiple still ends at 1000, so it
    # still (correctly) warns.
    assert any("add drug" in w.value for w in vary_multiple.warning)


def test_app_shows_no_events_section_for_a_model_without_events():
    st.cache_data.clear()
    at = AppTest.from_file(APP_SCRIPT)
    at.session_state["selected_model"] = "brusselator"

    with mock.patch("events.read_events", return_value=[]):
        at.run()

    assert not at.exception
    assert not any("End time" in w.value for w in at.warning)
    assert not any(e.label == "Events in this model" for e in at.expander)
