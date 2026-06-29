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
