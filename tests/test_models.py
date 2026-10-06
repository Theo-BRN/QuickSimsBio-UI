"""Tests for the model registry and loading (app/models.py).

Pure-logic tests: basico is monkeypatched so they don't hit the network or load
COPASI. They check the *shape* of the registry and that ``load_model``
dispatches to the right basico call per source kind.
"""

import urllib.error
from pathlib import Path

import pytest

import models

# Captured at import, before the autouse stub below replaces it, for the few
# tests that need basico's real example list.
_REAL_GET_EXAMPLES = models.bsc.get_examples


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


def test_registry_tags_bundled_models_with_their_file_name():
    registry = models.get_model_registry()
    for name, file_name in models.BUNDLED_MODELS.items():
        assert registry[name] == {"kind": "bundled", "ref": file_name}


def test_default_model_never_depends_on_biomodels(monkeypatch):
    # A first visit must load with no network: the default has to ship with the
    # app (bundled) or with basico (a COPASI example) — never be a download.
    # Uses the real example list, not this module's stub.
    monkeypatch.setattr(models.bsc, "get_examples", _REAL_GET_EXAMPLES)
    registry = models.get_model_registry()
    assert models.DEFAULT_MODEL in registry
    assert registry[models.DEFAULT_MODEL]["kind"] in ("bundled", "example")


def test_every_model_description_names_a_real_model(monkeypatch):
    # A renamed or mistyped model would otherwise leave an orphaned description.
    monkeypatch.setattr(models.bsc, "get_examples", _REAL_GET_EXAMPLES)
    registry = models.get_model_registry()
    assert set(models.MODEL_DESCRIPTIONS) <= set(registry)


def test_registry_offers_only_described_examples(monkeypatch):
    # Developer test models (no description) stay out of a non-modeller's list;
    # bundled models are always offered.
    monkeypatch.setattr(
        models.bsc, "get_examples", lambda: ["/data/brusselator.cps", "/data/LM-test1.cps"]
    )
    registry = models.get_model_registry()

    assert "brusselator" in registry  # described
    assert "LM-test1" not in registry  # a fitting example, no description
    assert set(models.BUNDLED_MODELS) <= set(registry)


def test_default_model_has_a_description():
    assert models.MODEL_DESCRIPTIONS.get(models.DEFAULT_MODEL)


def test_model_descriptions_are_single_short_lines_that_fit_a_phone():
    for name, text in models.MODEL_DESCRIPTIONS.items():
        assert "\n" not in text, name
        assert len(text) <= 110, f"{name}: {len(text)} characters"


def test_every_bundled_model_file_is_actually_there():
    # A bundled file that never got committed would only show up as a broken
    # model on the deployed app — catch it here instead.
    for file_name in models.BUNDLED_MODELS.values():
        assert (models.MODEL_FILES_DIR / file_name).is_file(), file_name


def test_every_bundled_model_loads_with_no_network(monkeypatch):
    # Real load (no stubs), with BioModels made unreachable: bundled models must
    # never depend on EBI being up.
    def no_network(*args, **kwargs):
        raise AssertionError("a bundled model tried to reach BioModels")

    monkeypatch.setattr(models.bsc, "load_biomodel", no_network)
    for name, file_name in models.BUNDLED_MODELS.items():
        model = models.load_model({"kind": "bundled", "ref": file_name})
        assert models.bsc.get_species(model=model) is not None, name


def test_load_model_dispatches_bundled_to_its_file(monkeypatch):
    seen = {}

    def fake_load_model(ref):
        seen["ref"] = ref
        return "loaded-bundled"

    monkeypatch.setattr(models.bsc, "load_model", fake_load_model)
    result = models.load_model({"kind": "bundled", "ref": "CTCA.cps"})

    assert result == "loaded-bundled"
    assert seen["ref"] == str(models.MODEL_FILES_DIR / "CTCA.cps")


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


_BIOMODEL = {"kind": "biomodels", "ref": "MODEL2306220001"}


def _http_error(code):
    return urllib.error.HTTPError("https://biomodels", code, "msg", None, None)


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(504),  # gateway timeout — what EBI returned on 2026-09-28
        _http_error(503),
        urllib.error.URLError("no route to host"),
        TimeoutError(),
        ConnectionError(),
    ],
)
def test_describe_load_error_blames_biomodels_when_it_is_their_end(exc):
    message = models.describe_load_error(_BIOMODEL, exc)
    assert "BioModels" in message and "isn't responding" in message


def test_describe_load_error_says_when_a_biomodels_id_does_not_exist():
    message = models.describe_load_error(_BIOMODEL, _http_error(404))
    assert "no model with the ID" in message and "MODEL2306220001" in message


def test_describe_load_error_never_blames_biomodels_for_a_local_file():
    source = {"kind": "uploaded", "ref": "/tmp/x.cps"}
    message = models.describe_load_error(source, urllib.error.URLError("x"))
    assert "BioModels" not in message


def test_load_model_dispatches_sbml_to_import_sbml(monkeypatch):
    seen = {}

    def fake_import_sbml(ref):
        seen["ref"] = ref
        return "loaded-sbml"

    monkeypatch.setattr(models.bsc, "import_sbml", fake_import_sbml)
    result = models.load_model({"kind": "uploaded", "ref": "/tmp/x.sbml"})

    assert result == "loaded-sbml"
    assert seen["ref"] == "/tmp/x.sbml"
