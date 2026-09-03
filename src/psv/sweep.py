"""Turning notes into ordered press and release events.

Five places needed this and each wrote its own copy: texture reduction, hand
assignment, span detection, difficulty thinning, and the inspect report. All
five copies had the same bug, so this module exists as much to make that
impossible again as to save the repetition.

The bug is worth stating, because the fix looks arbitrary without it. A release
is placed ``tolerance`` early, which is what stops a note let go a few
milliseconds late from counting as held against the next one. For a note
*shorter* than the tolerance that clamp lands the release on the note's own
start, and since releases sort before presses the note was taken out of the held
set before it was ever put in, and then left in that set for the rest of the
piece. Every later instant was judged against a set that only grew.

On one real file, 171 notes of 26 ms against a 30 ms tolerance produced 547
span violations that did not exist, and made ``difficulty = "medium"`` drop 273
notes it had no reason to drop.
"""

from __future__ import annotations

from collections.abc import Sequence

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Note

#: Event ranks, which are also the tie-break order within one instant.
#:
#: A release is seen before a press, so a note handed over exactly where another
#: begins is never counted as held with it. LATE_RELEASE is the exception, and
#: exists only for notes too short to hold: it sorts after presses so such a
#: note is pressed and released inside its own instant. It stays visible to
#: whoever is sweeping, which matters because hand assignment only assigns notes
#: it sees press, and it occupies nothing afterwards.
RELEASE, PRESS, LATE_RELEASE = 0, 1, 2


def note_events(
    notes: Sequence[Note], tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> list[tuple[float, int, int]]:
    """``(time, rank, index)`` for every note boundary, in sweep order.

    ``index`` is the position in ``notes``, so a caller can recover the note.
    Compare ``rank`` against :data:`PRESS`; anything else is a release.
    """
    events: list[tuple[float, int, int]] = []
    for index, note in enumerate(notes):
        release = max(note.start, note.end - tolerance)
        rank = LATE_RELEASE if release <= note.start else RELEASE
        events.append((note.start, PRESS, index))
        events.append((release, rank, index))
    events.sort()
    return events
