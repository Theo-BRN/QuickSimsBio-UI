"""Model registry and loading for QuickSimsBio-UI.

This module owns the *registry* of models a user can choose from, and the logic
to load a chosen one via basico (COPASI). It deliberately does **not** import
Streamlit: keeping it Streamlit-free means the registry/loading logic can be
unit-tested without a running app (see CLAUDE.md > Architecture).

A model in the registry is described by a small "tagged" source dict::

    {"kind": "example",   "ref": "/path/to/model.cps"}   # a bundled COPASI example
    {"kind": "biomodels", "ref": "MODEL2306220001"}      # a BioModels database id

``kind`` is the single source of truth for *how* to load the model. It's also
the seam for future persistence: ``biomodels`` entries are cheap id strings we
can remember across sessions, while ``example`` (and, later, uploaded-file)
entries are file-backed and session-only.
"""

import tempfile
from pathlib import Path

import basico as bsc


# Hand-curated BioModels to offer alongside COPASI's bundled examples.
# Adding one is intentionally a one-liner: {display name: BioModels id}.
CURATED_BIOMODELS: dict[str, str] = {
    "Cubic Ternary Complex Activation": "MODEL2306220001",
}


def get_model_registry() -> dict[str, dict]:
    """Return ``{display_name: source}`` for every selectable model.

    Combines COPASI's bundled example models (discovered via basico) with the
    hand-curated BioModels in ``CURATED_BIOMODELS``. Each value is a tagged
    source dict (see the module docstring) describing how to load that model.
    """
    examples = {
        Path(path).stem: {"kind": "example", "ref": path}
        for path in bsc.get_examples()
    }
    biomodels = {
        name: {"kind": "biomodels", "ref": model_id}
        for name, model_id in CURATED_BIOMODELS.items()
    }
    # Curated entries win on any name collision (explicit beats implicit).
    return {**examples, **biomodels}


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
    if kind in ("example", "uploaded"):
        return _load_file(source["ref"])
    if kind == "biomodels":
        return bsc.load_biomodel(source["ref"])
    raise ValueError(f"Unknown model source kind: {kind!r}")
