"""UI smoke test for the Streamlit entry point (app/main.py).

Uses Streamlit's AppTest to run the app headlessly and check the model picker
renders with nothing selected (so no model is loaded on first paint).
"""

from streamlit.testing.v1 import AppTest


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


def test_app_runs_a_simulation_and_shows_a_results_table():
    # End-to-end slice: a loaded model + Run button → a results table appears.
    # This actually runs COPASI via quicksimsbio, so we shrink the run (few
    # points) and give the click a generous timeout.
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    at.number_input[1].set_value(20).run()  # number_input[1] = "Number of points"

    run_btn = next(b for b in at.button if b.label == "Run simulation")
    run_btn.click().run(timeout=60)

    assert not at.exception
    assert len(at.dataframe) == 1  # the results table rendered


def test_app_scans_an_input_and_runs_one_simulation_per_grid_value():
    # Switch one input (brusselator's species "X") to Scan with three values:
    # the run should produce three simulations (three Sim_Num blocks).
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()
    assert not at.exception

    at.number_input[1].set_value(20).run()  # shrink the run for speed

    next(s for s in at.selectbox if s.key == "mode_X").set_value("Scan").run()
    next(t for t in at.text_input if t.key == "vals_X").set_value("0.1, 1, 10").run()

    next(b for b in at.button if b.label == "Run simulation").click().run(timeout=60)

    assert not at.exception
    result = at.dataframe[0].value
    assert result["Sim_Num"].nunique() == 3


def test_app_blocks_run_on_a_bad_scan_value():
    # A non-numeric scan entry shows an inline error and disables Run.
    at = AppTest.from_file("app/main.py")
    at.session_state["selected_model"] = "brusselator"
    at.run()

    next(s for s in at.selectbox if s.key == "mode_X").set_value("Scan").run()
    next(t for t in at.text_input if t.key == "vals_X").set_value("oops").run()

    assert not at.exception
    assert any("isn't a number" in e.value for e in at.error)
    run_btn = next(b for b in at.button if b.label == "Run simulation")
    assert run_btn.disabled
