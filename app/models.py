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


def load_model(source: dict):
    """Load the model described by a tagged ``source`` dict and return it.

    Returns the loaded COPASI model handle (``COPASI.CDataModel``), which also
    becomes basico's current model. Raises ``ValueError`` for an unknown kind.
    """
    kind = source["kind"]
    if kind == "example":
        return bsc.load_model(source["ref"])
    if kind == "biomodels":
        return bsc.load_biomodel(source["ref"])
    raise ValueError(f"Unknown model source kind: {kind!r}")
