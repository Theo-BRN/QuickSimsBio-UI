# CLAUDE.md

Guidance for Claude Code in this repository. The project's design rules, architecture and
gotchas are in DEVELOPMENT.md, imported here:

@DEVELOPMENT.md

## Working here

- **Simplicity first.** If a request adds power at the cost of simplicity, say so and flag it
  as possible feature creep before building it.
- **Keep domain logic out of `app/main.py`.** It holds widgets and wiring only; everything
  else goes in a Streamlit-free module with tests.
- **Only `app/simulations.py` talks to `quicksimsbio`**, and only through its public API (the
  names exported from its `__init__.py`).
- **Run `poetry run pytest` before calling a change done.** Add or update tests with every
  change; for anything visual, say what to check in a browser, since AppTest can't see it.
