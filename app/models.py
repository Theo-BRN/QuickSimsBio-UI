"""Model registry and loading for QuickSimsBio-UI.

This module owns the *registry* of models a user can choose from, and the logic
to load a chosen one via basico (COPASI). It deliberately does **not** import
Streamlit: keeping it Streamlit-free means the registry/loading logic can be
unit-tested without a running app (see CLAUDE.md > Architecture).

A model in the registry is described by a small "tagged" source dict::

    {"kind": "bundled",   "ref": "CTCA.cps"}             # a file shipped in model_files/
    {"kind": "example",   "ref": "/path/to/model.cps"}   # a COPASI example (via basico)
    {"kind": "biomodels", "ref": "MODEL2306220001"}      # a BioModels database id
    {"kind": "uploaded",  "ref": "/tmp/…/model.cps"}     # a user's upload, this session

``kind`` is the single source of truth for *how* to load the model. It's also
the seam for future persistence: ``bundled`` and ``biomodels`` refs are short,
portable strings we can save, while ``example`` and ``uploaded`` refs are paths
tied to one machine.
"""

import json
import tempfile
import urllib.error
from pathlib import Path

import basico as bsc

# Models shipped inside the app, so they load instantly and never depend on a
# network. {display name: file name in MODEL_FILES_DIR}. Adding one is dropping
# the file in model_files/ and adding a line here.
MODEL_FILES_DIR = Path(__file__).parent / "model_files"
BUNDLED_MODELS: dict[str, str] = {
    "Cubic Ternary Complex Activation": "CTCA.cps",
}

# One-line, plain-language descriptions shown under the title, keyed by display
# name. Drafted from each model file's own notes; it's plain text in a JSON file,
# so it can be edited without touching code. A model with no entry just shows its
# name.
MODEL_DESCRIPTIONS: dict[str, str] = json.loads(
    (MODEL_FILES_DIR / "descriptions.json").read_text(encoding="utf-8")
)

# What a first visit opens with. On a phone the sidebar (where models are picked)
# starts hidden, so the app must be usable before anyone finds it. BIOM10-fit is a
# MAPK cascade — a widely recognised example — and ships with basico, so it loads
# instantly and needs no network.
DEFAULT_MODEL = "BIOM10-fit"


def get_model_registry() -> dict[str, dict]:
    """Return ``{display_name: source}`` for every selectable model.

    Combines COPASI's example models (discovered via basico) with the models
    bundled in ``model_files/``. Each value is a tagged source dict (see the
    module docstring) describing how to load that model.
    """
    examples = {
        Path(path).stem: {"kind": "example", "ref": path} for path in bsc.get_examples()
    }
    bundled = {
        name: {"kind": "bundled", "ref": file_name}
        for name, file_name in BUNDLED_MODELS.items()
    }
    # Bundled entries win on any name collision (explicit beats discovered).
    return {**examples, **bundled}


def merge_user_models(base: dict, user: dict) -> dict:
    """Combine the built-in registry with models the user added this session.

    User-added models win on any name collision (they're the more specific,
    intentional choice).
    """
    return {**base, **user}


def make_uploaded_source(filename: str, data: bytes) -> dict:
    """Write uploaded model bytes to a temp file and return a tagged source.

    Returns an ``"uploaded"`` source pointing at the temp file. The file lives
    for the server process's lifetime (session-scoped in practice) — uploaded
    models are intentionally not remembered across sessions (see the persistence
    decision in TODO/memory). The original suffix is preserved so basico can
    detect the format (.cps vs .sbml).
    """
    suffix = Path(filename).suffix or ".cps"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        path = tmp.name
    return {"kind": "uploaded", "ref": path}


def _load_file(path: str):
    """Load a model file, letting basico detect COPASI vs SBML format.

    ``.sbml`` goes straight to ``import_sbml``; everything else tries
    ``load_model`` first and falls back to ``import_sbml`` for an ``.xml`` file
    that turns out to be SBML.
    """
    if Path(path).suffix.lower() == ".sbml":
        return bsc.import_sbml(path)
    try:
        return bsc.load_model(path)
    except Exception:
        return bsc.import_sbml(path)


def load_model(source: dict):
    """Load the model described by a tagged ``source`` dict and return it.

    Returns the loaded COPASI model handle (``COPASI.CDataModel``), which also
    becomes basico's current model. Raises ``ValueError`` for an unknown kind.
    """
    kind = source["kind"]
    if kind == "bundled":
        return _load_file(str(MODEL_FILES_DIR / source["ref"]))
    if kind in ("example", "uploaded"):
        return _load_file(source["ref"])
    if kind == "biomodels":
        return bsc.load_biomodel(source["ref"])
    raise ValueError(f"Unknown model source kind: {kind!r}")


def describe_load_error(source: dict, exc: Exception) -> str:
    """A plain-language reason a model failed to load, for the person using the app.

    A BioModels model is downloaded from EBI's servers when it's first loaded, so a
    slow or failing server looks exactly like a broken app. When the failure is on
    BioModels' side, say so plainly, so nobody blames the model or themselves.
    """
    if source["kind"] == "biomodels":
        # HTTPError is a kind of URLError, so check the specific case first.
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
            return (
                f"BioModels has no model with the ID **{source['ref']}**. "
                "Check the ID and try again."
            )
        if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
            return (
                "BioModels (EBI's online model database) isn't responding right now, "
                "so this model can't be downloaded. Please "
                "try again later, or pick one of the models listed here."
            )
    return f"Something went wrong loading it: {exc}"
