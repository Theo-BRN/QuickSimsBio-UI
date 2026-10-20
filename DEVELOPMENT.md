# Developing QuickSimsBio-UI

A handover guide: what the app is for, the rules it is designed by, how the code fits
together, and the Streamlit behaviour that has caught us out. Ideas for future work live in
[IDEAS.md](IDEAS.md).

## Who it's for

Researchers, often non-technical, who want to _get a feel_ for a mechanistic model before
serious modelling work happens elsewhere. They use the app occasionally and should never need to read instructions.

## The guiding principle: deliberate simplicity

**The app is not attempting to be COPASI.** Every decision favours a simple, intuitive, fast-feeling experience over versatility. Fewer options is a feature: if a researcher needs serious control, they should use [COPASI](http://copasi.org/) or [basico](https://basico.readthedocs.io/) directly. When a feature would add power at the cost of simplicity, treat it as feature creep and argue it out first.

**Code follows the same rule.** Elegant and efficient where it's free, but readability wins
every trade-off. Someone newer to Python should be able to follow it. Clear names and obvious
flow beat clever abstractions.

## Design rules

Rules to argue from, so design decisions can be reviewed rather than settled by taste. Each
names the established principle behind it; most are Nielsen's usability heuristics, as
explained by the Nielsen Norman Group (NN/g). They're a starting position, not an authority:
when a rule and a preference collide, say so.

1. **Say what the app is doing.** _Visibility of system status._ Users who can't see the
   app's state can't tell whether their action worked. The loaded model is named in the main
   area, and the events status line says when a run stops before its events. For waits over
   about 10 seconds, a bare spinner isn't enough: say what stage it's at.
2. **Show, don't make them remember.** _Recognition rather than recall._ Prefer visible
   controls (radio, segmented control, pills) for short fixed sets; use a dropdown only when
   the list is long, such as a model's inputs, where type-ahead is the better answer.
3. **Closeness means "these belong together".** _Gestalt proximity and common region._ Put a
   control next to what it acts on. Each input's 'Options' and an '×' sit on that input's row.
4. **Two levels of disclosure, never three.** _Progressive disclosure._ Show the important
   options first and reveal specialised ones on request. Designs that go beyond two levels
   lose people; needing a third is a sign to simplify. Only things that _hide content until
   asked_ count as a level (an expander, a popover); a dropdown doesn't.
5. **Default simple; let experts go further.** _Flexibility and efficiency of use._ Keep the
   simple path untouched and layer extra power behind it (options holds range, log scale and scan).
6. **Make going back cheap.** _User control and freedom._ People explore more when a wrong
   turn isn't punished. Changing a model never edits it; removing an input returns it to its default.
7. **Minimalist means staged, not few.** _Aesthetic and minimalist design._ Simplicity is
   about what greets you, not a cap on what the app can do.
8. **Familiar beats clever.** _Jakob's law._ People arrive expecting the app to work like the others they use. Eg, the sidebar for temporary interface and choosing the model.

Sources: [10 usability heuristics applied to complex applications](https://www.nngroup.com/articles/usability-heuristics-complex-applications/),
[visibility of system status](https://www.nngroup.com/articles/visibility-system-status/),
[proximity](https://www.nngroup.com/articles/gestalt-proximity/),
[common region](https://www.nngroup.com/articles/common-region/),
[progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/),
[Jakob's law](https://www.nngroup.com/videos/jakobs-law-internet-ux/).

### Hard lines

- **Time courses only.** `run_time_course` is the only COPASI task the app runs.
- **Never modify the model.** We read inputs, set values for a run, read outputs, and put
  everything back. No persistent edits, and no editing of events.
- **Events are shown, never edited.** Models run exactly as defined; the app explains when
  their events fire.

## Architecture

```
QuickSimsBio-UI  →  QuickSimsBio  →  basico  →  COPASI
   (this app)       (the engine)    (Py API)   (solver)
```

| Module               | Responsibility                                                                                                                                                       |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/main.py`        | The Streamlit page: layout, widgets and wiring only. No domain logic.                                                                                                |
| `app/simulations.py` | The **only** module that talks to `quicksimsbio`. Reads a model's inputs, builds the `scan_dict`, runs it, returns plain DataFrames. Also the slider and scan maths. |
| `app/models.py`      | The model registry (bundled files, COPASI examples, BioModels, uploads) and loading via basico.                                                                      |
| `app/events.py`      | Reads a model's events, works out when they fire, and builds the events table and timeline.                                                                          |
| `app/plotting.py`    | Plotly figures: kinetics, or one panel per value when an input is scanned.                                                                                           |
| `app/model_files/`   | Models shipped with the app, and a one-line description of every registry model.                                                                                     |

Only `main.py` imports Streamlit, so the rest is unit-tested without a running app.

### The `scan_dict` contract

`quicksimsbio.run_simulations` takes a `scan_dict` mapping each input name to:

- `None`: hold at the model's default;
- a `list`: grid values to run, one simulation each.

(The package also accepts a `{"lower", "upper", "log"}` range for quasi-random sampling; the
app doesn't use it yet.) The app sends a one-item list for each input on screen, a list of
up to 16 values for the one scanned input, and `None` for everything else.

### Which inputs exist

The package decides (`quicksimsbio.get_model_inputs`): fixed parameters, species' initial
concentrations, and reaction parameters (such as `(R1).k1`) that aren't mapped to a global
quantity. The app only looks up each one's kind and default.

### The restore guard

`run_simulations` sets each input on the model and never puts it back. The app keeps one
cached COPASI model per model and reuses it across runs, so `simulations.run` snapshots every
input before a run and restores it afterwards, by exact name. Without this, a later run's
"default" would silently be the last scanned value. Tests check that every default is
unchanged after a run, on several real models.

The cached model is shared by everyone using the server (`st.cache_resource`), so two runs at
the same moment could in principle see each other's values. This hasn't been seen in use.

### The plot follows what was run

`plotting.mode` picks the figure from the `scan_dict`: nothing scanned gives one kinetics
plot; one scanned input gives the same traces in one panel per value.

## Running and testing

Poetry, Python 3.12 or later.

```bash
poetry install                          # dependencies, incl. quicksimsbio from GitHub
poetry run streamlit run app/main.py    # the app, in your browser
poetry run pytest                       # the whole suite (about 45 s)
```

- **Backend tests** (`test_simulations`, `test_models`, `test_events`, `test_plotting`) use
  stubs where they can, and real COPASI models where behaviour depends on them.
- **UI tests** (`test_app`) use Streamlit's [AppTest](https://docs.streamlit.io/develop/api-reference/app-testing),
  which runs the script headlessly and can set widgets and rerun. It can't see CSS or layout,
  so how things _look_ is still checked by eye in a browser.

**Working on the package at the same time:** `poetry run pip install -e ../QuickSimsBio`
uses your local copy live; `poetry sync` returns to the GitHub version.

## Streamlit patterns and gotchas

Streamlit reruns the whole script, top to bottom, on every interaction. Most of what follows
comes from that.

- **Keys include the model.** Input widgets are keyed `<kind>::<ref>::<field>::<input>`, so
  each model keeps its own choices and switching model starts afresh.
- **Seed widgets through session state, not `value=`.** Streamlit warns when a widget gets a
  value both ways, and callbacks (such as switching to log scale) need to change values. Seed
  with session state, then write the value back on _every_ run: only a value set from the
  script is sent to the browser, and without it a freshly drawn ⚙ panel showed Min and Max
  as 0.
- **A widget that isn't drawn loses its state.** Streamlit cleans up the state of any widget
  not rendered on a run. Read values with `st.session_state.get(key, default)`.
- **Unkeyed elements are identified by position.** When rows reorder (a scanned input moves
  to the top), an unkeyed popover stayed open on whichever row moved into its place. Key
  anything whose position can change.
- **Settings are drawn after the slider they control,** so the slider reads them from the
  previous run (or their defaults on the first).
- **Styling by key.** A keyed container gets the CSS class `st-key-<key>`
  ([documented](https://docs.streamlit.io/develop/api-reference/layout/st.container)). The
  legend colours and the sticky inputs column use it. The sticky column also relies on
  `data-testid="stColumn"`, which isn't a documented API: if an upgrade renames it, the column
  simply stops sticking.
- **Our own legend.** Streamlit treats a chart with new data as a brand-new chart, so Plotly's
  legend clicks and zoom are lost on every rerun. The app draws its own legend buttons and
  keeps the hidden outputs in session state. Zoom is still lost.
- **`return`, not `st.stop()`,** to end a panel early: `st.stop()` halts the whole script.
- **Caching.** `st.cache_resource` holds live COPASI models (one per model, shared by all
  users); `st.cache_data` holds plain results, keyed on hashable `(kind, ref)` tags rather
  than the unhashable model. A repeated run returns instantly.
- **Fail with a message, not a traceback.** Runs, plots and the events panel catch broad
  exceptions on purpose: a failed run explains itself, a failed plot still shows the raw
  data, and a broken events panel simply disappears.

## Deployment

The app runs on [Streamlit Community Cloud](https://share.streamlit.io) from this public repo,
with `app/main.py` as the main file and Python 3.12.

- **The cloud installs with pip from `requirements.txt`**, not Poetry. Keep its versions in
  step with `pyproject.toml`.
- **Reboot after some changes.** After pushing a QuickSimsBio change, or a change to an
  `app/` module other than `main.py`, reboot the app from the Community Cloud dashboard. In
  our experience the running app doesn't pick these up on its own.
