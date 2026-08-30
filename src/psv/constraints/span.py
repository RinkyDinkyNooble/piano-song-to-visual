"""Finding the moments where one hand is asked to stretch too far.

The whole engine rests on one question: at every instant, does the set of notes
one hand is holding fit inside ``max_span`` semitones? This module answers it,
and only that. Repair lives next door in ``repair.py``.

Detection is a sweep line over note boundaries in time order. Two facts make it
simple:

* A span can only *grow* when a note starts. Ending a note can never widen the
  set that remains, so only start events need checking.
* An overlap shorter than the tolerance is sloppy MIDI, not a stretch anyone
  holds. Ending every note early by the tolerance makes those overlaps vanish
  from the sweep entirely, rather than needing a special case.
"""

from __future__ import annotations

from bisect import insort
from collections.abc import Sequence
from dataclasses import dataclass, field

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Hand, Note, Score


@dataclass(frozen=True, slots=True)
class Violation:
    """One instant where a hand's held notes are further apart than allowed.

    Indices refer to the sequence the violation was detected from. ``active``
    carries what *both* hands were holding, because the cheapest repair is
    usually to move a note across, and answering "would the other hand cope?"
    later would mean scanning the whole score again.
    """

    hand: Hand
    time: float
    span: int
    lowest: int
    highest: int
    active: dict[Hand, tuple[int, ...]] = field(default_factory=dict)

    @property
    def indices(self) -> tuple[int, ...]:
        return self.active.get(self.hand, ())

    def __str__(self) -> str:
        return f"{self.hand.value} hand spans {self.span} semitones at {self.time:.2f}s"


def _events(notes: Sequence[Note], tolerance: float) -> list[tuple[float, int, int]]:
    """Note boundaries as ``(time, is_start, index)``, in sweep order.

    Ends sort before starts at the same instant, so a note that stops exactly
    where another begins is never counted as held with it.
    """
    events: list[tuple[float, int, int]] = []
    for index, note in enumerate(notes):
        end = max(note.start, note.end - tolerance)
        events.append((note.start, 1, index))
        events.append((end, 0, index))
    events.sort()
    return events


def detect_violations(
    notes: Sequence[Note],
    max_span: int,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> list[Violation]:
    """Every instant where some hand exceeds ``max_span``, in time order.

    Unassigned notes are swept as their own group. That is not a real hand, but
    it means a score that has not been through hand assignment still reports
    something meaningful rather than silently passing.
    """
    if max_span < 0:
        raise ValueError(f"max_span cannot be negative, got {max_span}")

    active: dict[Hand, list[tuple[int, int]]] = {}
    violations: list[Violation] = []
    events = _events(notes, tolerance)

    position = 0
    while position < len(events):
        # Settle the whole instant before judging it. Evaluating after each
        # individual start would report a chord half-built: the extremes would
        # be wrong, and the other hand might not have been added yet, which is
        # exactly the information repair needs to decide where a note can go.
        time = events[position][0]
        started: set[Hand] = set()
        while position < len(events) and events[position][0] == time:
            _, is_start, index = events[position]
            note = notes[index]
            held = active.setdefault(note.hand, [])
            if is_start:
                insort(held, (note.pitch, index))
                started.add(note.hand)
            else:
                entry = (note.pitch, index)
                if entry in held:
                    held.remove(entry)
            position += 1

        snapshot: dict[Hand, tuple[int, ...]] | None = None
        for hand in started:
            held = active[hand]
            if len(held) < 2:
                continue
            span = held[-1][0] - held[0][0]
            if span <= max_span:
                continue
            if snapshot is None:
                snapshot = {
                    other: tuple(i for _, i in entries)
                    for other, entries in active.items()
                    if entries
                }
            violations.append(
                Violation(
                    hand=hand,
                    time=time,
                    span=span,
                    lowest=held[0][1],
                    highest=held[-1][1],
                    active=snapshot,
                )
            )

    return violations


def verify_span(
    score: Score,
    max_span: int,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> list[Violation]:
    """The guarantee, checked.

    This runs at the end of every ``constrain`` call, not only in tests. If it
    ever returns a non-empty list on the engine's own output, that is a bug in
    the engine, not a warning to be logged and ignored.
    """
    return detect_violations(score.notes, max_span, tolerance)


def widest_span_per_hand(
    notes: Sequence[Note], tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> dict[Hand, int]:
    """The widest simultaneous reach each hand is asked for.

    Useful for reporting what a piece actually demands, and for confirming that
    constraining brought it under the limit.
    """
    active: dict[Hand, list[int]] = {}
    widest: dict[Hand, int] = {}

    for time, is_start, index in _events(notes, tolerance):
        del time
        note = notes[index]
        held = active.setdefault(note.hand, [])
        if is_start:
            insort(held, note.pitch)
            if len(held) > 1:
                span = held[-1] - held[0]
                widest[note.hand] = max(widest.get(note.hand, 0), span)
        elif note.pitch in held:
            held.remove(note.pitch)

    for hand in active:
        widest.setdefault(hand, 0)
    return widest
