# QuickSimsBio-UI

**Run mechanistic (systems-biology) model simulations from your browser — no code, no COPASI install.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Built with Streamlit](https://img.shields.io/badge/built%20with-Streamlit-FF4B4B.svg?logo=streamlit&logoColor=white)
[![Live demo](https://img.shields.io/badge/%E2%96%B6-Live_demo-FF4B4B.svg)](https://quicksimsbio-ui.streamlit.app)

> **▶ [Try it live](https://quicksimsbio-ui.streamlit.app)** — pick a model, run a simulation, see the result. Nothing to install.

---

## What it is
QuickSimsBio-UI is a small **Streamlit web app** that lets researchers run mechanistic (ODE / systems-biology) model simulations themselves — quickly and intuitively — without touching code or installing anything. It's aimed to convey the behaviour of models quickly and easily, before committing to serious modelling work elsewhere.

Underneath, it's a thin, deliberately-restrained UI over a stack of proper tools:
```
QuickSimsBio-UI  →  QuickSimsBio  →  basico  →  COPASI
   (this app)       (the engine)    (Py API)   (solver)
```

The app only "dips in and dips out" of [COPASI](http://copasi.org/) — it reads a model's inputs, sets parameters for a run, reads the outputs, but never modifies the underlying model files.

## Design principles
QuickSimsBio-UI was designed with deliberate simplicity. design decisions favour a simple and intuitive experience over versatile but encumbered one. If you need serious, more detailed control, you should be using COPASI or [basico](https://basico.readthedocs.io/) directly. 

  | What you run | What you see |
  | --- | --- |
  | Single parameter set | Kinetics traces (each output vs time) |
  | Grid scan over one input (e.g. `[drug]`) | That output vs the scanned input (endpoint) |
  | Scan over two or more inputs | Coloured scatter / 3-D surface (inputs on the axes, output as colour/height) |

## Features
- **Model picker** — choose from COPASI's bundled example models and a small set of curated [BioModels](https://www.ebi.ac.uk/biomodels/), or bring your own by BioModels ID or by uploading a `.cps` / `.sbml` file (session-scoped).
- **Editable inputs table** — every model input is a row: change a **Single** value, or scan it as a **Grid** (Lower → Upper, Linear or Log spacing, choose the number of points). Combine grids across inputs to explore a space.
- **Adaptive Plotly charts** — kinetics, output-vs-input, or scatter/3-D, chosen automatically, with log axes and output/axis pickers as appropriate.
- **Download CSV** — export full results tables to plot or analyse yourself.
- **Fails gracefully** — simulations sometimes fail due to the model or parameter combination this still provides the raw data, instead of a blank chart or a traceback error.

## Run it locally

Requires **Python ≥ 3.12** and [Poetry](https://python-poetry.org/).
```bash
poetry install                          # install deps (incl. the quicksimsbio engine)
poetry run streamlit run app/main.py    # launch the app in your browser
poetry run pytest                       # run the test suite
```
The [`quicksimsbio`](https://github.com/Theo-BRN/QuickSimsBio) engine is fetched from GitHub for deployment (see `requirements.txt`) and used as a local path dependency for development.

## How it fits together
The app is split into a **declarative UI** and a **testable backend** — domain logic never lives in the Streamlit script:
| Module | Responsibility |
| --- | --- |
| `app/main.py` | Streamlit page — layout, widgets, and wiring only (no domain logic). |
| `app/simulations.py` | The *only* module that talks to `quicksimsbio`: builds the scan, runs it, returns plain DataFrames. |
| `app/models.py` | The model registry (COPASI examples + curated BioModels) and loading via basico. |
| `app/plotting.py` | Plotly figure builders, selected by the type of simulation. |
| `tests/` | pytest for the backend (Streamlit-free) + Streamlit `AppTest` for UI flows. |

Keeping the backend free of Streamlit is what makes it unit-testable without a live server.

## Roadmap
QuickSimsBio-UI is in an early but working version (**v0.1**) — and is currently actively developed. Next priorities are:

- **Read-only events visualisation** — surface COPASI events (what events fire, when/why).
- **Random / quasi-random parameter sampling** — Sobol/Halton/LHS exploration of larger input spaces.
- **Polished `.cps` / `.sbml` upload** for user-supplied models.
- **More outputs** — parameters and derived quantities alongside species.

## Related projects
- **[QuickSimsBio](https://github.com/Theo-BRN/QuickSimsBio)** — the lightweight Python package this app uses.
- **[basico](https://basico.readthedocs.io/)** — the Python interface to COPASI.
- **[COPASI](http://copasi.org/)** — the simulation engine.
- **[BioModels](https://www.ebi.ac.uk/biomodels/)** — the model database several examples come from.

## Citation & license
If you use QuickSimsBio-UI in your research, please cite it — see [`CITATION.cff`](CITATION.cff).

Released under the **MIT License** — see [`LICENSE`](LICENSE).
