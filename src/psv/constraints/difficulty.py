"""Thinning texture to a chosen difficulty.

Difficulty and hand span are separate knobs on purpose. Difficulty decides how
*much* is played; span decides how far apart it can be. This module runs first
and span enforcement runs after it, so span always gets the last word. There is
no code path here that can widen a reach, and that is structural rather than a
promise: this module only ever removes notes.
"""

from __future__ import annotations

import logging
from bisect import insort
from dataclasses import dataclass

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Hand, Note, Score
from psv.sweep import PRESS, note_events

from .salience import Salience

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DifficultyProfile:
    """What a difficulty level actually does.

    ``max_simultaneous`` caps how many notes one hand holds at once, which is
    the main lever on how hard a passage feels to play. ``min_duration_s``
    strips ornaments: notes too brief to matter that are sounding underneath
    something longer.
    """

    max_simultaneous: int | None
    min_duration_s: float
    description: str


PROFILES: dict[str, DifficultyProfile] = {
    "beginner": DifficultyProfile(2, 0.12, "melody and bass, ornaments removed"),
    "easy": DifficultyProfile(3, 0.08, "thin harmony, most ornaments removed"),
    "medium": DifficultyProfile(4, 0.0, "full harmony, nothing removed for speed"),
    "hard": DifficultyProfile(5, 0.0, "dense harmony kept"),
    "original": DifficultyProfile(None, 0.0, "untouched"),
}


def apply_difficulty(
    score: Score,
    level: str,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> tuple[Score, tuple[Note, ...]]:
    """Thin ``score`` to ``level``. Returns the new score and what was removed.

    A single sweep. At each note start the hand's held set is checked, and the
    least salient note is removed until the set fits. Outer voices score highly
    in :class:`Salience`, so the melody and the bass survive and the harmony
    between them is what gives way.
    """
    if level not in PROFILES:
        raise ValueError(
            f"unknown difficulty {level!r}; expected one of {list(PROFILES)}"
        )

    profile = PROFILES[level]
    if profile.max_simultaneous is None and profile.min_duration_s <= 0:
        return score, ()

    notes = list(score.notes)
    if not notes:
        return score, ()

    weigh = Salience.analyse(notes)
    active: dict[Hand, list[tuple[int, int]]] = {}
    dropped: set[int] = set()

    for now, rank, index in note_events(notes, tolerance):
        note = notes[index]
        held = active.setdefault(note.hand, [])

        if rank != PRESS:
            entry = (note.pitch, index)
            if entry in held:
                held.remove(entry)
            continue

        if index in dropped:
            continue
        insort(held, (note.pitch, index))

        # An ornament is only expendable when something else is still sounding;
        # removing the last voice would leave a hole rather than simplify.
        #
        # And only when it is really an ornament. A short note that continues a
        # line is a melodic run, which a duration threshold cannot tell from a
        # decoration: on a piece with a fast right hand this rule alone was
        # taking 230 of the 231 melody notes that went missing, because a run
        # is short notes and the accompaniment under it is long ones.
        #
        # Only the top of the hand is spared. A line in an inner voice is
        # figuration, and sparing that instead spent the budget on filler:
        # long notes went in its place and left beats with nothing sounding
        # on them at all.
        if (
            profile.min_duration_s > 0
            and note.duration < profile.min_duration_s
            and len(held) > 1
            and not (weigh.carries_line(note) and note.pitch >= held[-1][0])
        ):
            held.remove((note.pitch, index))
            dropped.add(index)
            continue

        limit = profile.max_simultaneous
        while limit is not None and len(held) > limit:
            chord = [notes[i] for _, i in held]
            worst = min(
                range(len(held)),
                key=lambda position: (
                    weigh.of(notes[held[position][1]], chord, now),
                    -notes[held[position][1]].pitch,
                ),
            )
            _, victim = held.pop(worst)
            dropped.add(victim)

    if not dropped:
        return score, ()

    removed = tuple(notes[i] for i in sorted(dropped))
    log.info("difficulty %s removed %d note(s)", level, len(removed))
    kept = [note for index, note in enumerate(notes) if index not in dropped]
    return score.with_notes(kept), removed
