# Ideas

Possible directions for QuickSimsBio-UI. These are ideas, not commitments or a roadmap: each
one should be weighed against the [guiding principle](DEVELOPMENT.md#the-guiding-principle-deliberate-simplicity)
before it's built, and some may be better left out. Where an idea is well understood, a short
sketch of how it could work is included as a starting point, not a specification.

## Sketched ideas

### Reset to defaults

One click puts the inputs back to the newcomer example at the model's defaults (design rule 6,
_make going back cheap_).

- Sketch: a button beside "Add an input" whose callback sets the model's `…::chosen` list back
  to `simulations.example_inputs(kinds)` and deletes that model's row keys
  (`value`, `min`, `max`, `log`, `scan`, `n`), so every row is seeded afresh.

### Before/after comparison and snapshots

The most requested feature from users: an experimentalist changes a condition and currently has
to _remember_ what the previous plot looked like (design rule 2).

- Sketch: a "Save snapshot" button stores the current `last_run` request (model, `scan_dict`,
  timepoints) under a name in session state. Snapshots are listed in the sidebar; choosing one
  restores its inputs, or overlays its traces (dashed) on the current plot.
- Results are cached (`run_cached`), so showing a saved snapshot is free.
- A richer "Compare" view could follow: two runs side by side, or the second shown as a fold
  change or difference from the first.

### Export and import a run specification

Save a model and its settings as a small JSON file, so a particular fit can be recreated or
shared, and load one back.

- Sketch: `{"model": {"kind", "ref"}, "inputs": {name: {value, min, max, log, scan, n}},
"time": {start, end, n_points}}`, with `st.download_button` to save and `st.file_uploader`
  to load.
- BioModels and bundled models have portable refs; COPASI examples could be saved by name;
  uploaded models can't be referenced, so would need the file alongside.

### Summaries across a scan

With an input scanned, plot an output's value at one time point (or its area under the curve)
against the scanned value: the classic dose-response view.

- Sketch: a reduction in `plotting.py` from the long frame (`Time`, scanned-input column,
  outputs) to one value per scan value, offered only when an input is scanned.
- Open question: how to let the user choose the time point or AUC without adding overly complex controls.

### Scanning two inputs

Two scanned inputs as a grid of panels: one input across the columns, the other down the rows.

- Sketch: `plotting.mode` already refuses two scanned inputs; Plotly's `facet_row` and
  `facet_col` give the grid. The panel count multiplies, so each input would need a smaller
  cap (for example 4 × 4).

### Quasi-random sampling

Sample a larger input space with Sobol, Halton or Latin hypercube sampling, which the package
already supports through a `{"lower", "upper", "log"}` range and `n_rand_samples`.

- Open question: how to show many time courses from many inputs at once while staying simple
  (an envelope? one output against two inputs?).

### Link each model to its source

Show where a model comes from (paper, BioModels entry) without leaving the app.

- Sketch: a `source` URL beside each entry in `app/model_files/descriptions.json`, linked from
  the model caption.

### Neater display names

Make names like `Values[k1]`, `[X]_0` and `Model_Output` read naturally in plots and tables.

### More curated models

Add more well-described models to the registry (`models.py`), each with a one-line description.

## Open ideas

- Drag to reorder input rows.
- Keep the plot's zoom across reruns (would need a custom chart component; see DEVELOPMENT.md).
- A legend that copes with 25 or more outputs (type-ahead filter, or grouping).
- Guided first steps for newcomers (choose a model → change an input → read the plot).
- Optional explanations of the concepts behind the controls.
- Shift or scale the time axis (e.g. "time since the drug was added").
- A clearer progress message for long runs (design rule 1).
- Show when state-based events actually fired, after a run.
- A history of the session's runs.
