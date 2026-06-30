"""Tests for the simulation adapter (app/simulations.py).

Pure-logic tests: the ``quicksimsbio`` package is monkeypatched so they don't
load COPASI or run a real simulation. They check that each wrapper forwards its
arguments correctly and returns what the package returns — i.e. that the adapter
is a faithful, thin seam over the package (mirrors how ``test_models`` patches
``basico``). ``make_timepoints`` is pure maths, so it's tested for real.
"""

import pandas as pd
import pytest

import simulations


def _species_df(values: dict) -> pd.DataFrame:
    """A stand-in for ``bsc.get_species`` output: index=name, initial_concentration col."""
    return pd.DataFrame({"initial_concentration": pd.Series(values)})


def _params_df(values: dict | None):
    """A stand-in for ``bsc.get_parameters`` (None when a model has no parameters)."""
    return None if values is None else pd.DataFrame({"initial_value": pd.Series(values)})


@pytest.fixture
def stub_model_state(monkeypatch):
    """Stub basico's get/set initial-state calls and record every restore write.

    Lets the hermetic ``run`` tests exercise the snapshot/restore guard without a
    real COPASI model. Returns the list that restore writes are appended to.
    """
    monkeypatch.setattr(simulations.bsc, "get_species", lambda model=None: _species_df({"X": 3.0}))
    monkeypatch.setattr(simulations.bsc, "get_parameters", lambda model=None: _params_df({"k": 2.0}))
    restored: list[tuple[str, dict]] = []
    monkeypatch.setattr(simulations.bsc, "set_species", lambda **kw: restored.append(("species", kw)))
    monkeypatch.setattr(simulations.bsc, "set_parameters", lambda **kw: restored.append(("params", kw)))
    return restored


def test_get_inputs_forwards_model_and_returns_skeleton(monkeypatch):
    seen = {}

    def fake_get_model_inputs(model=None):
        seen["model"] = model
        return {"drug": None, "k_off": None}

    monkeypatch.setattr(simulations.qsb, "get_model_inputs", fake_get_model_inputs)
    result = simulations.get_inputs("MODEL")

    assert result == {"drug": None, "k_off": None}
    assert seen["model"] == "MODEL"  # passed through explicitly, not via global


def test_make_timepoints_spans_zero_to_end_inclusive():
    tps = simulations.make_timepoints(10, 5)

    assert tps == [0.0, 2.5, 5.0, 7.5, 10.0]
    assert tps[0] == 0.0 and tps[-1] == 10.0
    assert len(tps) == 5
    assert all(isinstance(t, float) for t in tps)  # plain Python floats, not np


def test_parse_grid_values_parses_comma_separated_numbers():
    assert simulations.parse_grid_values("0.1, 1, 10") == [0.1, 1.0, 10.0]
    assert simulations.parse_grid_values("5") == [5.0]


def test_parse_grid_values_blank_means_hold():
    assert simulations.parse_grid_values("") is None
    assert simulations.parse_grid_values("   ") is None
    assert simulations.parse_grid_values(" , , ") is None  # only commas → hold


def test_parse_grid_values_tolerates_trailing_and_double_commas():
    assert simulations.parse_grid_values("1, 2,") == [1.0, 2.0]
    assert simulations.parse_grid_values("1,,2") == [1.0, 2.0]


def test_parse_grid_values_rejects_non_numbers_with_friendly_message():
    with pytest.raises(ValueError, match="isn't a number"):
        simulations.parse_grid_values("1, abc, 3")


def test_build_scan_dict_with_no_overrides_holds_everything():
    skeleton = {"drug": None, "k_off": None}
    assert simulations.build_scan_dict(skeleton, {}) == {"drug": None, "k_off": None}


def test_build_scan_dict_applies_overrides_and_holds_the_rest():
    skeleton = {"drug": None, "k_off": None, "Receptor_0": None}
    scan = simulations.build_scan_dict(skeleton, {"drug": [0.1, 1.0, 10.0]})

    assert scan == {"drug": [0.1, 1.0, 10.0], "k_off": None, "Receptor_0": None}


def test_build_scan_dict_treats_none_override_as_hold():
    skeleton = {"drug": None, "k_off": None}
    # e.g. user picked "scan" for drug but left the values box blank → None.
    scan = simulations.build_scan_dict(skeleton, {"drug": None})

    assert scan == {"drug": None, "k_off": None}


def test_build_scan_dict_rejects_unknown_inputs():
    skeleton = {"drug": None}
    with pytest.raises(ValueError, match="Unknown input"):
        simulations.build_scan_dict(skeleton, {"not_a_real_input": [1.0]})


def test_run_forwards_args_and_returns_package_result(monkeypatch, stub_model_state):
    seen = {}
    sentinel = object()  # stand-in for the DataFrame the package returns

    def fake_run_simulations(scan_dict, timepoints=None, model=None, **kwargs):
        seen.update(scan_dict=scan_dict, timepoints=timepoints, model=model)
        return sentinel

    monkeypatch.setattr(simulations.qsb, "run_simulations", fake_run_simulations)
    out = simulations.run("MODEL", {"drug": [1, 2]}, timepoints=[0, 1, 2])

    assert out is sentinel
    assert seen == {
        "scan_dict": {"drug": [1, 2]},
        "timepoints": [0, 1, 2],
        "model": "MODEL",
    }


def test_run_restores_initial_state_after_running(monkeypatch, stub_model_state):
    monkeypatch.setattr(simulations.qsb, "run_simulations", lambda *a, **k: "df")
    simulations.run("MODEL", {"X": [0.1, 1, 10]})

    # The snapshot ({"X": 3.0} species, {"k": 2.0} param) is written back verbatim.
    assert ("species", {"name": "X", "initial_concentration": 3.0, "model": "MODEL"}) in stub_model_state
    assert ("params", {"name": "k", "initial_value": 2.0, "model": "MODEL"}) in stub_model_state


def test_run_restores_initial_state_even_when_the_run_raises(monkeypatch, stub_model_state):
    def boom(*a, **k):
        raise RuntimeError("simulation failed")

    monkeypatch.setattr(simulations.qsb, "run_simulations", boom)

    with pytest.raises(RuntimeError, match="simulation failed"):
        simulations.run("MODEL", {"X": [0.1, 1, 10]})

    assert stub_model_state  # restore still happened via the finally block


def test_run_restores_model_state_so_held_inputs_stay_at_default():
    # Real integration (no stubs): a scan must not pollute a later hold-all run.
    # This is the regression guard for the shared-model state-leak bug.
    path = next(p for p in simulations.bsc.get_examples() if "brusselator" in p.lower())
    model = simulations.bsc.load_model(path)
    skeleton = simulations.get_inputs(model)
    timepoints = simulations.make_timepoints(100, 50)

    pristine = simulations.run(model, dict(skeleton), timepoints)
    simulations.run(model, {**skeleton, "X": [0.1, 1.0, 10.0]}, timepoints)  # mutates
    after = simulations.run(model, dict(skeleton), timepoints)

    pd.testing.assert_frame_equal(pristine, after)
