# QuickSimsBio-UI

**Run mechanistic (systems-biology) model simulations from your browser — no code required.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Built with Streamlit](https://img.shields.io/badge/built%20with-Streamlit-FF4B4B.svg?logo=streamlit&logoColor=white)
[![Live demo](https://img.shields.io/badge/%E2%96%B6-Live_demo-FF4B4B.svg)](https://quicksimsbio-ui.streamlit.app)

> **▶ [Try it live](https://quicksimsbio-ui.streamlit.app)** — pick a model, move a slider, see the result. Nothing to install.

---

## What it is

QuickSimsBio-UI is a small **Streamlit web app** that lets researchers run mechanistic (ODE / systems-biology) model simulations themselves — quickly and intuitively — without touching code or installing anything. It's aimed to convey the behaviour of models quickly and easily, before committing to serious modelling work elsewhere.

Underneath, it's a thin, deliberately-restrained UI over a stack of proper tools:

```
QuickSimsBio-UI  →  QuickSimsBio  →  basico  →  COPASI
   (this app)       (the engine)    (Py API)   (solver)
```

The app only "dips in and dips out" of [COPASI](http://copasi.org/) — it reads a model's inputs, sets them for a run, reads the outputs, and never modifies the underlying model files.

## What you can do

- **Pick a model** — COPASI's bundled examples and a few curated [BioModels](https://www.ebi.ac.uk/biomodels/), or your own by BioModels ID or by uploading a `.cps` / `.sbml` file (kept for your session only).
- **Change inputs with sliders** — add any species' initial concentration, parameter or rate constant from a searchable picker. Options allow you to set each slider's range and log scale. Everything else runs at the model's defaults, and the plot updates as you go.
- **Scan an input** — switch one input to several values across its range, and see one panel per value.
- **Choose the time window** — and see the model's events: what they do, when they fire, and whether your run reaches them.
- **Plot it your way** — line, scatter or stacked area, with a legend that remembers which outputs you've hidden.
- **Take the data with you** — the raw results table downloads as CSV.
- **Fails gracefully** — if a simulation fails, you get a plain explanation (and the raw data, where there is any) instead of a blank chart or a traceback.

## Design principles

QuickSimsBio-UI is designed with deliberate simplicity: every decision favours a simple, intuitive experience over a versatile but encumbered one. If you need serious, more detailed control, use COPASI or [basico](https://basico.readthedocs.io/) directly. The plot follows what you ran: a single run shows kinetics, and a scanned input shows one panel per value.

## Run it locally

Requires **Python ≥ 3.12** and [Poetry](https://python-poetry.org/).

```bash
poetry install                          # install deps (incl. the quicksimsbio engine from GitHub)
poetry run streamlit run app/main.py    # launch the app in your browser
poetry run pytest                       # run the test suite
```

## Contributing

- **[DEVELOPMENT.md](DEVELOPMENT.md)** — the design rules, how the code fits together, testing, deployment, and the Streamlit behaviour worth knowing first.
- **[IDEAS.md](IDEAS.md)** — possible future directions, some with a sketched design.

## Related projects

- **[QuickSimsBio](https://github.com/Theo-BRN/QuickSimsBio)** — the lightweight Python package this app uses.
- **[basico](https://basico.readthedocs.io/)** — the Python interface to COPASI.
- **[COPASI](http://copasi.org/)** — the simulation engine.
- **[BioModels](https://www.ebi.ac.uk/biomodels/)** — the model database several examples come from.

## Citation & license

If you use QuickSimsBio-UI in your research, please cite it — see [`CITATION.cff`](CITATION.cff).

Released under the **MIT License** — see [`LICENSE`](LICENSE).
