"""How much a note matters, for deciding what to sacrifice.

Used in two places: choosing which note to drop when nothing gentler will fix a
violation, and thinning texture for a difficulty level.

This is deliberately crude. Real salience needs harmonic analysis, which
nothing in psv does yet. What is here is enough to make the two decisions above
defensibly rather than arbitrarily, and it is a single function, so there is one
place to replace when something better exists.
"""

from __future__ import annotations

from collections.abc import Sequence

from psv.model import Note

#: Outer voices carry the tune and the bass. Losing one is far more damaging
#: than losing an inner voice, so they get a large thumb on the scale.
OUTER_VOICE_BONUS = 40.0


def salience(note: Note) -> float:
    """A note's importance on its own, ignoring its neighbours.

    Loud and long notes are structural; quiet, brief ones are usually
    ornamental. Velocity dominates, duration breaks ties.
    """
    return note.velocity + min(note.duration, 2.0) * 10.0


def contextual_salience(note: Note, among: Sequence[Note]) -> float:
    """A note's importance within the chord it is sounding in.

    The top and bottom of a simultaneity are the melody and the bass. Whatever
    is between them is harmony, which a listener will miss least.
    """
    if not among:
        return salience(note)
    pitches = [other.pitch for other in among]
    score = salience(note)
    if note.pitch == max(pitches) or note.pitch == min(pitches):
        score += OUTER_VOICE_BONUS
    return score
