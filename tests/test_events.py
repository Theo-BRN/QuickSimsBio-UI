"""Tests for reading and classifying COPASI events (app/events.py).

Pure-logic tests: basico is monkeypatched so they don't hit the network or load
COPASI, the same way ``test_models`` does.

The trigger strings here are **not invented** — they're what basico actually
hands back, captured by running against the real models:

- ``'Time gt1000000'`` and ``'Time gt1000000 - 10'`` come from the CTCA biomodel
  (``MODEL2306220001``). COPASI's infix uses the word operator ``gt``, and the
  display step eats the following space.
- ``'Time > 10'`` is how a trigger written with ``>`` round-trips.
- ``'Time > 10and [A] > 0.5'`` is a real compound trigger — note ``10and``, where
  the space vanished again.

Keeping these exact is the point: they're the strings the parser has to survive.
"""

import pandas as pd
import pytest

import events


def _event(name="E", time=0.0, trigger="Time > 0", assignments=()):
    """A ready-made Event, for the period/table tests that don't parse anything."""
    return events.Event(name=name, trigger=trigger, time=time, assignments=assignments)


def _times(evts):
    return [e.time for e in evts]


# --- normalise_trigger --------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Time gt1000000", "Time > 1000000"),        # real CTCA string
        ("Time gt1000000 - 10", "Time > 1000000 - 10"),  # real CTCA string
        ("Time > 10", "Time > 10"),                  # already symbolic
        ("Time ge 500", "Time >= 500"),
        ("[A] lt Values[thresh]", "[A] < Values[thresh]"),
    ],
)
def test_normalise_trigger_reconciles_word_and_symbol_operators(raw, expected):
    assert events.normalise_trigger(raw) == expected


def test_normalise_trigger_leaves_names_containing_operator_letters_alone():
    # The word-operator pattern must not chew through a species called e.g. 'gtp'.
    assert events.normalise_trigger("[gtp] > 5") == "[gtp] > 5"


# --- resolve_trigger_time: the placeable cases --------------------------------
@pytest.mark.parametrize(
    "trigger, expected",
    [
        ("Time gt1000000", 1_000_000.0),      # real: Add Ligand
        ("Time gt1000000 - 10", 999_990.0),   # real: Set [G]tot at L
        ("Time > 10", 10.0),
        ("Time ge 500", 500.0),
        ("Time > 2 * 50", 100.0),
    ],
)
def test_resolve_trigger_time_resolves_plain_time_triggers(trigger, expected):
    assert events.resolve_trigger_time(trigger, "", {}) == expected


def test_resolve_trigger_time_resolves_a_parameter_encoded_time():
    # 'Time > Drug addition time' — placeable, because the parameter is constant.
    assert events.resolve_trigger_time(
        "Time > Values[dose_time]", "", {"dose_time": 50.0}
    ) == 50.0


def test_resolve_trigger_time_resolves_a_parameter_name_containing_brackets():
    # COPASI parameter names carry brackets of their own ('[G]tot'), which a
    # regex-extracted name would truncate at the first ']'.
    assert events.resolve_trigger_time(
        "Time > Values[[G]tot] + 5", "", {"[G]tot": 100.0}
    ) == 105.0


def test_resolve_trigger_time_adds_a_constant_delay():
    assert events.resolve_trigger_time("Time > 20", "5", {}) == 25.0


# --- resolve_trigger_time: everything unplaceable -----------------------------
@pytest.mark.parametrize(
    "trigger",
    [
        "[A] > Values[thresh]",       # state-based
        "Time > 10and [A] > 0.5",     # real compound string
        "[A] > 5",
        "Time > [A]",                 # time compared to model state
        "Values[p] > 3",              # not a time trigger at all
    ],
)
def test_resolve_trigger_time_refuses_anything_state_dependent(trigger):
    assert events.resolve_trigger_time(trigger, "", {"thresh": 0.5, "p": 1.0}) is None


def test_resolve_trigger_time_refuses_an_assignment_driven_parameter():
    # The distinction the user cares about: a *fixed* parameter is knowable now, an
    # *assignment* one is computed from model state. Only constants are passed in,
    # so an assignment-driven parameter is simply absent -> unresolvable.
    assert events.resolve_trigger_time("Time > Values[computed]", "", {}) is None


def test_resolve_trigger_time_refuses_an_unresolvable_delay():
    # The trigger time is known but the delay isn't, so the firing time isn't.
    assert events.resolve_trigger_time("Time > 20", "Values[computed]", {}) is None


# --- fixed_parameter_values ---------------------------------------------------
def test_fixed_parameter_values_keeps_only_constants(monkeypatch):
    frame = pd.DataFrame(
        {"type": ["fixed", "assignment", "fixed"], "initial_value": [1.0, 2.0, 3.0]},
        index=["a", "b", "c"],
    )
    monkeypatch.setattr(events.bsc, "get_parameters", lambda **kw: frame)
    assert events.fixed_parameter_values(None) == {"a": 1.0, "c": 3.0}


def test_fixed_parameter_values_handles_a_model_with_no_parameters(monkeypatch):
    # basico returns None for a model with no global quantities (e.g. brusselator).
    monkeypatch.setattr(events.bsc, "get_parameters", lambda **kw: None)
    assert events.fixed_parameter_values(None) == {}


# --- read_events --------------------------------------------------------------
def _fake_basico(monkeypatch, rows, parameters=None):
    """Stub basico with an events frame shaped exactly like get_events' output."""
    frame = pd.DataFrame(rows).set_index("name") if rows else None
    monkeypatch.setattr(events.bsc, "get_events", lambda **kw: frame)
    monkeypatch.setattr(
        events.bsc,
        "get_parameters",
        lambda **kw: parameters if parameters is not None else None,
    )


def test_read_events_returns_empty_for_a_model_without_events(monkeypatch):
    # basico returns None here — true of all 14 bundled COPASI examples.
    _fake_basico(monkeypatch, rows=[])
    assert events.read_events(None) == []


def test_read_events_resolves_the_real_ctca_events(monkeypatch):
    # The two events of MODEL2306220001, exactly as basico reports them.
    _fake_basico(
        monkeypatch,
        rows=[
            {
                "name": "Add Ligand at Ligand Addition Time",
                "trigger": "Time gt1000000",
                "delay": "",
                "assignments": [
                    {"target": "[L]", "expression": "{Values[Initial for X]}"}
                ],
            },
            {
                "name": "Set [G]tot at L",
                "trigger": "Time gt1000000 - 10",
                "delay": "",
                "assignments": [
                    {"target": "Values[LigandConcAdded]", "expression": "Values[[G]tot]"}
                ],
            },
        ],
    )
    result = events.read_events(None)

    # Sorted into firing order, not the model's declaration order.
    assert [e.name for e in result] == [
        "Set [G]tot at L",
        "Add Ligand at Ligand Addition Time",
    ]
    assert _times(result) == [999_990.0, 1_000_000.0]
    # basico's braces around a referenced quantity are stripped for display.
    assert result[1].assignments == (("[L]", "Values[Initial for X]"),)


def test_read_events_keeps_state_based_events_last_and_unplaced(monkeypatch):
    _fake_basico(
        monkeypatch,
        rows=[
            {"name": "threshold", "trigger": "[A] gt 5", "delay": "", "assignments": []},
            {"name": "dose", "trigger": "Time > 10", "delay": "", "assignments": []},
        ],
    )
    result = events.read_events(None)

    assert [e.name for e in result] == ["dose", "threshold"]
    assert _times(result) == [10.0, None]
    # The state-based event still carries a readable condition for the table.
    assert result[1].trigger == "[A] > 5"


# --- segments -----------------------------------------------------------------
# One equal-width segment per placeable event: the run-up TO each firing. Unlike a
# period model there's no trailing "after the last event" segment (the bar ends at
# the final firing) and simultaneous events aren't merged (each keeps its marker).
def test_segments_splits_the_users_double_equilibration_example():
    # The case that motivated the design: two tight pairs separated by huge gaps.
    evts = [
        _event("record 1", 1e6),
        _event("reset", 1e6 + 1),
        _event("record 2", 2e6),
        _event("drug", 2e6 + 1),
    ]
    result = events.segments(evts)

    assert [s.start for s in result] == [0.0, 1e6, 1e6 + 1, 2e6]
    assert [s.duration for s in result] == [1e6, 1.0, 2e6 - (1e6 + 1), 1.0]
    assert [s.event.name for s in result] == ["record 1", "reset", "record 2", "drug"]
    assert [s.from_label for s in result] == ["Start", "record 1", "reset", "record 2"]


def test_segments_is_empty_when_nothing_is_placeable():
    # A purely state-based model gets no timeline at all, rather than an empty bar.
    assert events.segments([_event("threshold", time=None)]) == []


def test_segments_gives_a_single_event_one_segment():
    result = events.segments([_event("dose", 100.0)])
    assert [(s.start, s.duration, s.from_label) for s in result] == [(0.0, 100.0, "Start")]


def test_segments_keeps_simultaneous_events_as_separate_zero_duration_segments():
    # Each event keeps its own marker; the second segment simply has duration 0.
    result = events.segments([_event("a", 500.0), _event("b", 500.0)])

    assert [(s.duration, s.event.name) for s in result] == [(500.0, "a"), (0.0, "b")]
    assert [s.from_label for s in result] == ["Start", "a"]


def test_segments_handles_an_event_at_time_zero():
    result = events.segments([_event("init", 0.0), _event("dose", 100.0)])

    assert [(s.start, s.duration) for s in result] == [(0.0, 0.0), (0.0, 100.0)]


def test_segments_sorts_events_into_firing_order():
    result = events.segments([_event("late", 300.0), _event("early", 100.0)])
    assert [s.event.name for s in result] == ["early", "late"]


def test_segments_handles_evenly_spaced_repeat_dosing():
    result = events.segments([_event(f"dose {t}", float(t)) for t in (100, 200, 300)])
    assert [s.duration for s in result] == [100.0, 100.0, 100.0]


# --- missed_events ------------------------------------------------------------
def test_missed_events_flags_the_ctca_default_window():
    # The app's default run ends at 1000; CTCA's events fire at ~1e6, so by
    # default a user watches a simulation in which nothing ever happens.
    evts = [_event("set", 999_990.0), _event("ligand", 1_000_000.0)]
    assert [e.name for e in events.missed_events(evts, 1000.0)] == ["set", "ligand"]


def test_missed_events_is_empty_once_the_run_reaches_them():
    evts = [_event("set", 999_990.0), _event("ligand", 1_000_000.0)]
    assert events.missed_events(evts, 1_000_120.0) == []


def test_missed_events_ignores_events_before_the_recorded_window():
    # Only the end matters: COPASI integrates from 0 regardless of where recording
    # starts, so an earlier event *did* fire and its effect is in the results.
    evts = [_event("set", 999_990.0)]
    assert events.missed_events(evts, 1_000_120.0) == []


def test_missed_events_ignores_state_based_events():
    assert events.missed_events([_event("threshold", time=None)], 10.0) == []


# --- events_table -------------------------------------------------------------
def test_events_table_lists_times_and_effects_in_plain_words():
    evts = [
        _event("set", 999_990.0, assignments=(("Values[x]", "1"),)),
        _event("ligand", 1_000_000.0, assignments=(("[L]", "1e-6"),)),
    ]
    table = events.events_table(evts)

    assert list(table.columns) == ["Event", "When", "Effects"]  # no 'Since previous'
    assert list(table["When"]) == ["999,990", "1,000,000"]  # not '1e+06'
    # Plain "set to", not ":=" — the audience is non-technical.
    assert list(table["Effects"]) == ["Values[x] set to 1", "[L] set to 1e-6"]


def test_events_table_shows_the_condition_for_a_state_based_event():
    # A state-based event can't be placed on a timeline, so the table is its home:
    # its 'When' is the condition itself, which tells a modeller what to watch for.
    evts = [_event("threshold", time=None, trigger="[A] > Values[thresh]")]
    table = events.events_table(evts)

    assert list(table["When"]) == ["[A] > Values[thresh]"]


def test_events_table_lists_every_event_including_unplaceable_ones():
    evts = [_event("dose", 10.0), _event("threshold", time=None, trigger="[A] > 5")]
    assert list(events.events_table(evts)["Event"]) == ["dose", "threshold"]


# --- timeline_figure ----------------------------------------------------------
# Structure only, no pixels: one equal-width bar per segment plus a single marker
# trace, and the reached/unreached colouring the run-window highlight depends on.
def _timeline(evts, **kwargs):
    return events.timeline_figure(events.segments(evts), **kwargs)


def _bars(figure):
    return [t for t in figure.data if t.type == "bar"]


def _markers(figure):
    return next(t for t in figure.data if t.type == "scatter")


def test_timeline_figure_draws_one_equal_width_bar_per_segment():
    figure = _timeline([_event("a", 10.0), _event("b", 1e6), _event("c", 1e6 + 1)])

    bars = _bars(figure)
    assert len(bars) == 3
    assert all(tuple(bar.x) == (1,) for bar in bars)  # every gap the same width
    assert figure.layout.barmode == "stack"


def test_timeline_figure_places_a_marker_at_every_events_seam():
    figure = _timeline([_event("a", 10.0), _event("b", 20.0), _event("c", 30.0)])

    markers = _markers(figure)
    assert tuple(markers.x) == (1, 2, 3)  # right edge of each stacked bar


def test_timeline_figure_hollows_the_marker_for_an_event_the_run_never_reaches():
    # The window highlight: solid = reached, hollow = beyond the run's end.
    figure = _timeline([_event("reached", 500.0), _event("missed", 1_000_000.0)], end=800.0)

    fills = list(_markers(figure).marker.color)
    assert fills == [events.MARKER_REACHED, events.MARKER_MISSED]


def test_timeline_figure_marks_every_event_reached_when_no_end_is_given():
    figure = _timeline([_event("a", 500.0), _event("b", 1_000_000.0)])

    fills = list(_markers(figure).marker.color)
    assert fills == [events.MARKER_REACHED, events.MARKER_REACHED]


def test_timeline_figure_raises_when_nothing_is_placeable():
    with pytest.raises(ValueError):
        events.timeline_figure([])
