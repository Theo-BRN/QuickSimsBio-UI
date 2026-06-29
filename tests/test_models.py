"""Tests for the model registry and loading (app/models.py).

Pure-logic tests: basico is monkeypatched so they don't hit the network or load
COPASI. They check the *shape* of the registry and that ``load_model``
dispatches to the right basico call per source kind.
"""

from pathlib import Path

import pytest

import models


@pytest.fixture(autouse=True)
def stub_examples(monkeypatch):
    """Replace the bundled-example listing with a fixed, hermetic one."""
    monkeypatch.setattr(
        models.bsc,
        "get_examples",
        lambda: ["/data/brusselator.cps", "/data/YeastGlycolysis.cps"],
    )


def test_registry_tags_examples_with_their_path():
    registry = models.get_model_registry()
    assert registry["brusselator"] == {
        "kind": "example",
        "ref": "/data/brusselator.cps",
    }


def test_registry_tags_curated_biomodels_with_their_id():
    registry = models.get_model_registry()
    for name, model_id in models.CURATED_BIOMODELS.items():
        assert registry[name] == {"kind": "biomodels", "ref": model_id}


def test_load_model_dispatches_example_to_load_model(monkeypatch):
    seen = {}

    def fake_load_model(ref):
        seen["ref"] = ref
        return "loaded-example"

    monkeypatch.setattr(models.bsc, "load_model", fake_load_model)
    result = models.load_model({"kind": "example", "ref": "/data/brusselator.cps"})

    assert result == "loaded-example"
    assert seen["ref"] == "/data/brusselator.cps"


def test_load_model_dispatches_biomodels_to_load_biomodel(monkeypatch):
    seen = {}

    def fake_load_biomodel(ref):
        seen["ref"] = ref
        return "loaded-biomodel"

    monkeypatch.setattr(models.bsc, "load_biomodel", fake_load_biomodel)
    result = models.load_model({"kind": "biomodels", "ref": "MODEL2306220001"})

    assert result == "loaded-biomodel"
    assert seen["ref"] == "MODEL2306220001"


def test_load_model_rejects_unknown_kind():
    with pytest.raises(ValueError):
        models.load_model({"kind": "nonsense", "ref": "x"})


def test_merge_user_models_lets_user_models_win_on_collision():
    base = {
        "A": {"kind": "example", "ref": "/a"},
        "B": {"kind": "example", "ref": "/b"},
    }
    user = {
        "B": {"kind": "biomodels", "ref": "ID"},
        "C": {"kind": "uploaded", "ref": "/c"},
    }
    merged = models.merge_user_models(base, user)

    assert merged["A"] == {"kind": "example", "ref": "/a"}
    assert merged["B"] == {"kind": "biomodels", "ref": "ID"}  # user wins
    assert merged["C"] == {"kind": "uploaded", "ref": "/c"}


def test_make_uploaded_source_writes_bytes_to_a_temp_file():
    source = models.make_uploaded_source("my_model.cps", b"<COPASI/>")

    assert source["kind"] == "uploaded"
    assert source["ref"].endswith(".cps")
    assert Path(source["ref"]).read_bytes() == b"<COPASI/>"


def test_load_model_dispatches_uploaded_cps_to_load_model(monkeypatch):
    seen = {}

    def fake_load_model(ref):
        seen["ref"] = ref
        return "loaded-file"

    monkeypatch.setattr(models.bsc, "load_model", fake_load_model)
    result = models.load_model({"kind": "uploaded", "ref": "/tmp/x.cps"})

    assert result == "loaded-file"
    assert seen["ref"] == "/tmp/x.cps"


def test_load_model_dispatches_sbml_to_import_sbml(monkeypatch):
    seen = {}

    def fake_import_sbml(ref):
        seen["ref"] = ref
        return "loaded-sbml"

    monkeypatch.setattr(models.bsc, "import_sbml", fake_import_sbml)
    result = models.load_model({"kind": "uploaded", "ref": "/tmp/x.sbml"})

    assert result == "loaded-sbml"
    assert seen["ref"] == "/tmp/x.sbml"
