"""Reading a model's COPASI **events**, read-only, for display.

Models use events for equilibration, dosing and switches, so a user can't read a
simulation without knowing what fired and when. This module turns basico's raw
event data into plain facts the UI can show. Per CLAUDE.md > Events we **never**
create or edit events — we only read them.

Like ``models`` and ``simulations``, this module deliberately does **not** import
Streamlit, so every function here is unit-testable without a running app. It
talks to **basico** only (never ``quicksimsbio``), keeping ``simulations`` the
one module that touches the package.

The central idea is a **conservative classification** of each event's trigger:

- A *time-based* trigger (``Time > 10000``, or ``Time > Values[dose_time]`` where
  that parameter is a constant) resolves to a number — we know exactly when it
  fires, so it can go on a timeline.
- Anything else — a *state-based* trigger like ``[A] > 5``, or one that depends on
  a computed quantity — has a fire time that's only knowable from the trajectory.
  We resolve it to ``None`` and it stays in the table, never on the timeline.

Unrecognised is always the safe answer: ``None`` means "shown in the table with
its condition", never a wrong number on a chart.

**Why the trigger string needs so much care.** COPASI's own infix for the CTCA
biomodel is ``<CN=...,Reference=Time> gt 1000000`` — it uses the *word* operator
``gt``, not ``>``. basico's display step then eats the following space, handing us
``'Time gt1000000'``. A trigger written as ``"Time > 10"`` comes back as
``'Time > 10'``. So both spellings are real, and whitespace can't be trusted;
``normalise_trigger`` is what reconciles them.
"""

import ast
import operator
import re
from dataclasses import dataclass

import basico as bsc
import pandas as pd
import plotly.graph_objects as go

# COPASI writes comparisons either as symbols or as these word operators, so we
# map the words onto symbols before doing anything else.
WORD_OPERATORS = {"gt": ">", "ge": ">=", "lt": "<", "le": "<=", "eq": "==", "ne": "!="}

# A word operator, optionally followed by the space basico drops. The lookahead is
# load-bearing: ``\bgt\b`` does NOT match ``gt1000000`` (``t``/``1`` are both word
# characters, so there's no boundary between them), which is exactly the real CTCA
# string. Requiring only that the operator is *followed* by the start of a value
# catches both ``gt 1000000`` and ``gt1000000``.
_WORD_OPERATOR_RE = re.compile(r"\b(gt|ge|lt|le|eq|ne)\s*(?=[\d\s({.+-])")

# A trigger we can place on a timeline: time compared against everything else.
_TIME_TRIGGER_RE = re.compile(r"^\s*Time\s*(?:>=|<=|>|<|==)\s*(.+?)\s*$")

# The arithmetic ``_safe_eval`` allows — enough for real triggers like
# ``1000000 - 10``, and nothing more.
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

# --- Timeline drawing ---------------------------------------------------------
# Follows the author's prototype: each gap between firings is an equal-width
# stacked bar (so spacing carries no meaning and extreme ratios stay legible), a
# bold marker sits at every event, and the real durations ride along in the
# labels/hover. Segment fills are left to Plotly's default cycle — here colour
# only distinguishes neighbours, it isn't identity — matching that prototype.
MARKER_REACHED = "#0b0b0b"   # an event the run reaches: solid, it happened
MARKER_MISSED = "#ffffff"    # an event beyond the run's end: hollow, never fired
MARKER_RING = "#ffffff"      # white ring so a marker reads against any segment
MARKER_MISSED_RING = "#898781"
SECONDARY_INK = "#52514e"


@dataclass(frozen=True)
class Event:
    """One COPASI event, as much resolved as we can honestly manage.

    ``time`` is the moment it fires, or ``None`` when the trigger depends on model
    state and so can't be known before running. ``trigger`` is always the
    normalised, human-readable condition — for a state-based event that string is
    what the table shows in place of a time.
    """

    name: str
    trigger: str
    time: float | None
    assignments: tuple[tuple[str, str], ...]  # (target, expression) pairs


@dataclass(frozen=True)
class Segment:
    """The stretch of time leading up to one event firing.

    One per placeable event: the first spans time 0 → the first firing, and each
    later one spans the previous firing → this one. ``event`` is the firing that
    *ends* the segment (drawn as the marker at its right edge), and ``from_label``
    names what came before (``"Start"`` or the previous event) so the segment can
    label itself ``"Start → dose"``.

    Two events at the same instant give two segments — the second with
    ``duration`` 0 — rather than being merged, so each still gets its own marker.
    There is deliberately no trailing "after the last event" segment: the bar ends
    at the final firing.
    """

    start: float
    duration: float
    event: Event
    from_label: str


def normalise_trigger(trigger: str) -> str:
    """Rewrite COPASI's word operators as symbols and tidy the spacing.

    ``'Time gt1000000'`` -> ``'Time > 1000000'``. This is both the first step of
    resolving a time and the string we *show* for a state-based event, so the
    table reads ``[A] > Values[thresh]`` rather than COPASI's raw output.
    """
    swapped = _WORD_OPERATOR_RE.sub(
        lambda match: f" {WORD_OPERATORS[match.group(1)]} ", trigger
    )
    return re.sub(r"\s+", " ", swapped).strip()


def _safe_eval(expression: str) -> float:
    """Evaluate a plain arithmetic expression, e.g. ``'1000000 - 10'`` -> 999990.0.

    Deliberately *not* ``eval`` — trigger text comes from a model file, and this
    only ever needs numbers and ``+ - * /``. (``ast.literal_eval`` won't do: it
    rejects arithmetic on plain numbers, so it can't handle ``1000000 - 10``.)
    Raises ``ValueError`` on anything outside that grammar, which callers treat as
    "not placeable".
    """

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](
                evaluate(node.left), evaluate(node.right)
            )
        raise ValueError(f"Unsupported expression: {expression!r}")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Unparseable expression: {expression!r}") from exc
    return evaluate(tree)


def _resolve_constant(expression: str, fixed_values: dict[str, float]) -> float | None:
    """Reduce an expression to a number, or ``None`` if it isn't constant.

    Substitutes each ``Values[name]`` for its value, then evaluates the arithmetic
    that's left. ``fixed_values`` holds **only constant parameters**, which is what
    makes this correct by construction: a parameter driven by an assignment simply
    isn't in the dict, so its ``Values[...]`` token survives and the leftover
    bracket check below rejects the whole expression.

    Names are substituted literally, longest-first, rather than pulled out with a
    regex — COPASI parameter names contain brackets of their own (``[G]tot``,
    ``Event_[Ligand] Applied``), so a pattern like ``Values\\[([^\\]]+)\\]`` would
    stop at the first ``]`` and capture a mangled name.
    """
    for name in sorted(fixed_values, key=len, reverse=True):
        token = f"Values[{name}]"
        if token in expression:
            expression = expression.replace(token, f"({float(fixed_values[name])!r})")

    # Anything bracketed left over is a species or an unresolved parameter, and a
    # second `Time` means a compound condition — either way, not a constant.
    if re.search(r"[\[\]]", expression) or "Time" in expression:
        return None
    try:
        return _safe_eval(expression)
    except ValueError:
        return None


def resolve_trigger_time(
    trigger: str, delay: str, fixed_values: dict[str, float]
) -> float | None:
    """When does this event fire? ``None`` if that can't be known up front.

    Only the narrow, safe shape resolves: ``Time <op> <constant>``. Compound
    triggers (``Time > 10 and [A] > 0.5``) and state-based ones (``[A] > 5``) fall
    through to ``None`` by design — see the module docstring.

    A constant ``delay`` shifts the firing later, so it's added. An unresolvable
    delay makes the whole fire time unknowable, so it returns ``None`` too.
    """
    match = _TIME_TRIGGER_RE.match(normalise_trigger(trigger))
    if not match:
        return None
    time = _resolve_constant(match.group(1), fixed_values)
    if time is None:
        return None

    if not delay or not delay.strip():
        return time
    offset = _resolve_constant(normalise_trigger(delay), fixed_values)
    return None if offset is None else time + offset


def fixed_parameter_values(model) -> dict[str, float]:
    """``{name: value}`` for every parameter that is a **constant**.

    COPASI tags each global quantity as ``fixed`` (a constant we can read now) or
    ``assignment`` (computed from model state as the run proceeds) — precisely the
    hardcoded-vs-assigned split that decides whether a parameter-encoded trigger
    time is knowable. Only the constants go in the dict; see ``_resolve_constant``
    for why leaving the rest out is what makes resolution safe.
    """
    parameters = bsc.get_parameters(model=model)
    if parameters is None:  # a model with no global quantities
        return {}
    fixed = parameters[parameters["type"] == "fixed"]
    return {str(name): float(value) for name, value in fixed["initial_value"].items()}


def time_unit(model) -> str:
    """The model's own unit of time (e.g. ``'s'``), for labelling.

    Read from the model rather than assumed: these are somebody else's models and
    nothing says a time course is in seconds. Falls back to an empty string, which
    callers render as an unlabelled number rather than a wrong one.
    """
    return str(bsc.get_model_units(model=model).get("time_unit", ""))


def _clean_assignments(assignments) -> tuple[tuple[str, str], ...]:
    """basico's assignment dicts -> ``(target, expression)`` pairs, braces stripped.

    basico wraps a referenced quantity in braces (``'{Values[Initial for X]}'``),
    which is noise to a reader, so it comes off.
    """
    return tuple(
        (str(item["target"]), str(item["expression"]).strip("{}"))
        for item in (assignments or [])
    )


def read_events(model) -> list[Event]:
    """Every event in ``model``, resolved and ordered for display.

    Time-based events come first, in firing order; state-based ones follow in the
    model's own order (they have no time to sort on). Returns ``[]`` for a model
    with no events — basico returns ``None`` there, which is every one of COPASI's
    bundled examples.
    """
    table = bsc.get_events(model=model)
    if table is None or table.empty:
        return []

    fixed_values = fixed_parameter_values(model)
    events = [
        Event(
            name=str(name),
            trigger=normalise_trigger(str(row["trigger"])),
            time=resolve_trigger_time(
                str(row["trigger"]), str(row["delay"]), fixed_values
            ),
            assignments=_clean_assignments(row["assignments"]),
        )
        for name, row in table.iterrows()
    ]
    timed = sorted((e for e in events if e.time is not None), key=lambda e: e.time)
    state_based = [e for e in events if e.time is None]
    return [*timed, *state_based]


def segments(events: list[Event]) -> list[Segment]:
    """One equal-width timeline segment per placeable event, in firing order.

    Each segment is the run-up *to* an event: the first from time 0, each later
    one from the previous firing. State-based events (no known time) are skipped —
    they can't be placed and live in the table instead.

    Returns ``[]`` when nothing is placeable, so a purely state-based model draws
    no bar at all rather than an empty one.
    """
    placeable = sorted(
        (e for e in events if e.time is not None), key=lambda e: e.time
    )
    built = []
    previous_time = 0.0
    previous_label = "Start"
    for event in placeable:
        built.append(
            Segment(
                start=previous_time,
                duration=event.time - previous_time,
                event=event,
                from_label=previous_label,
            )
        )
        previous_time = event.time
        previous_label = event.name
    return built


def missed_events(events: list[Event], end: float) -> list[Event]:
    """Time-based events that never fire because the run stops before them.

    Only ``end`` matters, not the start of the recorded window: COPASI always
    integrates from time 0 and the start merely decides from when output is
    *recorded*. So an event before the window still fires (its effect is baked
    into what you see); one after ``end`` genuinely never happens.

    This is what catches the app's sharpest edge: the CTCA model's events fire at
    ~1e6 while the default run ends at 1000, so by default a user sees a
    simulation in which nothing ever happens.
    """
    return [e for e in events if e.time is not None and e.time > end]


def format_time(value: float) -> str:
    """Format a time for display: ``999990.0`` -> ``'999,990'``.

    The precision matters. Plain ``g`` keeps only 6 significant digits, which
    turns 1000000 into ``'1e+06'`` right next to a ``'999,990'`` — the two times
    that matter most in the CTCA model, rendered in two different notations. Ten
    digits keeps realistic times readable and still falls back to an exponent for
    genuinely extreme values.
    """
    return f"{value:,.10g}"


def events_table(events: list[Event]) -> pd.DataFrame:
    """The read-only event inventory — one row per event, nothing hidden.

    This is the complete surface, and the **home for state-based events**: they
    can't be placed on a timeline, so instead of a time their *When* cell shows
    the condition itself (``[A] > Values[thresh]``), which tells a modeller
    exactly what to watch for. *Effects* reads in plain words ("... set to ...")
    rather than an assignment operator, since the audience is non-technical.
    """
    rows = [
        {
            "Event": event.name,
            "When": event.trigger if event.time is None else format_time(event.time),
            "Effects": " · ".join(
                f"{target} set to {expression}"
                for target, expression in event.assignments
            ),
        }
        for event in events
    ]
    return pd.DataFrame(rows, columns=["Event", "When", "Effects"])


def _with_unit(text: str, unit: str) -> str:
    """Append the model's time unit to a formatted number, when we have one."""
    return f"{text} {unit}".strip()


def _segment_hover(segment: Segment, unit: str) -> str:
    """The real facts the equal widths hide: when this gap runs, and how long."""
    return (
        f"<b>{segment.from_label} → {segment.event.name}</b><br>"
        f"Window: {format_time(segment.start)} → {format_time(segment.event.time)}<br>"
        f"Duration: {_with_unit(format_time(segment.duration), unit)}"
        "<extra></extra>"
    )


def timeline_figure(
    timeline: list[Segment], *, end: float | None = None, unit: str = ""
):
    """Draw the event timeline: each gap an **equal-width** bar, whatever its length.

    Follows the author's prototype. A model that equilibrates for 1e6 then doses 1
    later can't be read on a true time axis — the two events land on the same pixel
    — so every gap between firings is drawn the same width and the real durations
    live in the labels and hover. Each segment is its own stacked bar (so adjacent
    gaps read as distinct blocks), and a bold marker sits at every event.

    When ``end`` is given, a marker is **hollow** for any event the run never
    reaches (``time > end``) and solid otherwise — the one visual cue for the app's
    sharpest edge, a run that stops before its events ever fire.

    Lives here rather than in ``plotting`` because it isn't one of the adaptive
    result plots: it describes the *model*, not a result frame, and it needs this
    module's vocabulary (``Segment``, ``format_time``). ``plotting`` also documents
    itself as free of the basico import, which importing this module would break.
    """
    if not timeline:
        raise ValueError("Nothing to draw: no event has a knowable firing time.")

    figure = go.Figure()
    for segment in timeline:
        figure.add_trace(
            go.Bar(
                x=[1],
                y=["Events"],
                orientation="h",
                text=f"{segment.from_label} → {segment.event.name}",
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate=_segment_hover(segment, unit),
                showlegend=False,
            )
        )

    reached = [end is None or segment.event.time <= end for segment in timeline]
    figure.add_trace(
        go.Scatter(
            x=[index + 1 for index in range(len(timeline))],  # right edge of each bar
            y=["Events"] * len(timeline),
            mode="markers",
            marker=dict(
                size=22,
                symbol="circle",
                color=[MARKER_REACHED if ok else MARKER_MISSED for ok in reached],
                line=dict(
                    color=[MARKER_RING if ok else MARKER_MISSED_RING for ok in reached],
                    width=4,
                ),
            ),
            hovertext=[
                f"<b>{segment.event.name}</b><br>"
                f"Time: {_with_unit(format_time(segment.event.time), unit)}"
                + ("" if ok else "<br><i>not reached by this run</i>")
                for segment, ok in zip(timeline, reached)
            ],
            hovertemplate="%{hovertext}<extra></extra>",
            showlegend=False,
        )
    )

    figure.update_layout(
        barmode="stack",
        template="plotly_white",
        bargap=0,
        height=150,
        margin=dict(l=20, r=24, t=16, b=34),
        xaxis=dict(
            range=[-0.1, len(timeline) + 0.1],  # padding so end markers don't clip
            showgrid=False,
            showticklabels=False,
            fixedrange=True,
            zeroline=False,
        ),
        yaxis=dict(visible=False, fixedrange=True),
    )
    # Equal width is not equal time — say so once, quietly, so the chart can't mislead.
    figure.add_annotation(
        xref="paper",
        yref="paper",
        x=0,
        y=-0.18,
        text="Not to scale",
        showarrow=False,
        xanchor="left",
        font=dict(color=SECONDARY_INK, size=11),
    )
    return figure
