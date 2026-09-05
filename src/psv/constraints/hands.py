"""The fallback hand assignment, for a score that arrives without one.

The constraint engine works per hand, so a score has to have hands before it
can be constrained. `psv.arrange` is what normally provides them, and it is
much better at it. This is what runs when `constrain` is called on its own, on
a file whose hands nobody has decided yet.

It splits at the piece's median pitch. That is not good hand assignment: it
puts a left-hand melody in the right hand whenever the voices cross, and it
takes no account of continuity or of what is comfortable to play. It is here so
the engine always has something to work on, and so its failures are the kind you
can see rather than the kind that silently produce nothing.

`ensure_hands` never overwrites an assignment that already exists, which is what
keeps the arrange stage's decisions from being undone by the stage after it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from psv.model import Hand, Note, Score

log = logging.getLogger(__name__)


def median_pitch(notes: Sequence[Note]) -> int:
    if not notes:
        return 60
    pitches = sorted(note.pitch for note in notes)
    return pitches[len(pitches) // 2]


def has_hands(score: Score) -> bool:
    """Whether every note already knows which hand plays it."""
    return all(note.hand is not Hand.UNASSIGNED for note in score.notes)


def assign_by_register(score: Score, split: int | None = None) -> Score:
    """Put everything below ``split`` in the left hand, the rest in the right."""
    notes = score.notes
    if not notes:
        return score
    if split is None:
        split = median_pitch(notes)

    log.info("provisional hand split at pitch %d", split)
    return score.with_notes(
        note.assigned_to(Hand.LEFT if note.pitch < split else Hand.RIGHT)
        for note in notes
    )


def ensure_hands(score: Score) -> Score:
    """Assign hands only if they are missing, so real ones are never overwritten.

    A file that already separates the hands, or a score that has been through
    the arrange stage, must come out of here untouched.
    """
    if score.is_empty or has_hands(score):
        return score
    return assign_by_register(score)
