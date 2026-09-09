"""One key, one press.

A piano key can only be held down by one finger at a time. Scores ask for more
than that all the time, and none of it is a mistake in the source: two voices
sharing a staff write the same pitch together, or one hand holds a note while
the other taps it. Notation can say that. A keyboard cannot do it.

Left alone it reaches the video as tiles stacked on top of each other, and the
audio as a second note-on for a key that never came up.

The resolution is what a player does at the keyboard:

* **Struck later** — the key lifts just before the second strike, which is what
  re-articulating a held note means. While the sustain pedal is down it is
  inaudible, for the same reason truncation is cheap in ``repair``.
* **Struck together** — there is only one press to make, so the longer note
  keeps the key and the shorter one goes. Nothing is heard that was not heard
  before: the pitch still sounds, for the longer of the two durations.

This runs after span repair rather than before it. Repairs can create a clash
themselves by moving a note onto an occupied key, so going first would miss
those; and neither resolution here can widen a reach, because shortening and
dropping only ever narrow what one hand holds. The span guarantee survives this
pass untouched.

What it does not do is put the held note back after the tap. That would mean
splitting one note into two, and the engine moves, shortens and removes notes
but does not invent them.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Note, Score, pitch_name
from psv.sweep import PRESS, note_events

log = logging.getLogger(__name__)

#: Detection finds every clashing pair at once, so real music settles in one.
#: The bound is here for the same reason the span loop has one.
MAX_PASSES = 4


@dataclass(frozen=True, slots=True)
class DoubleStrike:
    """One moment a key is struck while another note is still holding it.

    ``held`` and ``struck`` index the sequence the strike was detected from.
    """

    pitch: int
    time: float
    held: int
    struck: int

    def __str__(self) -> str:
        return f"{pitch_name(self.pitch)} struck at {self.time:.2f}s while still held"


def detect_double_strikes(
    notes: Sequence[Note], tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> list[DoubleStrike]:
    """Every press that lands on a key some other note already holds.

    Swept with the shared press/release sweep, so an overlap shorter than
    ``tolerance`` disappears here exactly as it does everywhere else. A note
    released two milliseconds after the next one starts is sloppy MIDI, not a
    key held by two fingers.
    """
    down: dict[int, list[int]] = defaultdict(list)
    found: list[DoubleStrike] = []
    for time, rank, index in note_events(notes, tolerance):
        pitch = notes[index].pitch
        if rank == PRESS:
            found.extend(DoubleStrike(pitch, time, held, index) for held in down[pitch])
            down[pitch].append(index)
        elif index in down[pitch]:
            down[pitch].remove(index)
    return found


def verify_single_press(
    score: Score, tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> list[DoubleStrike]:
    """The postcondition: no key is ever struck while it is already down."""
    return detect_double_strikes(score.notes, tolerance)


def _resolve_pass(
    working: list[Note],
    strikes: Sequence[DoubleStrike],
    tolerance: float,
) -> tuple[set[int], list[tuple[str, Note, Note | None]]]:
    """Apply one round of resolutions, returning what was dropped and why."""
    gone: set[int] = set()
    edits: list[tuple[str, Note, Note | None]] = []
    for strike in strikes:
        if strike.held in gone or strike.struck in gone:
            continue
        held, struck = working[strike.held], working[strike.struck]
        if not held.overlaps(struck, tolerance):
            # An earlier resolution in this pass already parted them.
            continue
        if struck.start - held.start > tolerance:
            lifted = held.shortened_to(struck.start)
            working[strike.held] = lifted
            edits.append(("lift-to-restrike", held, lifted))
        else:
            victim = strike.held if held.duration <= struck.duration else strike.struck
            gone.add(victim)
            edits.append(("merge-unison", working[victim], None))
    return gone, edits


def resolve_double_strikes(
    notes: Sequence[Note], tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> tuple[list[Note], list[tuple[str, Note, Note | None]]]:
    """Make every key playable by one finger, and say what that cost.

    Returns the notes and one ``(strategy, before, after)`` triple per edit,
    with ``after`` None for a note that was removed. The caller turns those
    into whatever it records; this module does not depend on ``repair``.

    Termination: every edit either shortens a note strictly, which removes that
    clash for good and cannot create another, or removes one. The note count is
    finite, so the loop cannot run forever.
    """
    working = list(notes)
    edits: list[tuple[str, Note, Note | None]] = []
    for _ in range(MAX_PASSES):
        strikes = detect_double_strikes(working, tolerance)
        if not strikes:
            return working, edits
        gone, done = _resolve_pass(working, strikes, tolerance)
        edits.extend(done)
        if gone:
            working = [n for i, n in enumerate(working) if i not in gone]

    remaining = detect_double_strikes(working, tolerance)
    if remaining:  # pragma: no cover - real music settles in the first pass
        log.warning(
            "double strikes did not settle in %d passes; dropping %d held note(s)",
            MAX_PASSES,
            len(remaining),
        )
        gone = {strike.held for strike in remaining}
        edits.extend(("merge-unison", working[i], None) for i in sorted(gone))
        working = [n for i, n in enumerate(working) if i not in gone]
    return working, edits
