"""Turning several instruments into two hands.

The fuzziest stage in the pipeline, and the only one with no right answer. A
string quartet has four independent voices; a pianist has ten fingers and two
places to put them. Something has to give, and choosing what is a judgement a
human arranger spends years learning to make.

What is here is honest about being a first pass. Two steps:

1. **Reduce.** Cap how many notes sound at once, dropping the least salient
   first, so the texture could fit two hands at all.
2. **Assign hands.** Walk the piece choosing, at each instant, a pitch to split
   at. The split moves with the music, which is what makes it survive voices
   crossing, and it prefers to stay where it was, which is what stops the hands
   leaping about between chords.

It aims for *learnable*, not publishable. Hand-fixing the intermediate MIDI and
re-running from `constrain` is a supported workflow, not a failure.
"""

from __future__ import annotations

import logging
from bisect import insort
from dataclasses import dataclass
from itertools import pairwise

from psv.constraints.salience import contextual_salience
from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Hand, Note, Score

log = logging.getLogger(__name__)

#: Most notes a pair of hands can hold at once before the texture stops being
#: playable at all. Ten fingers exist, but ten held notes almost never do.
DEFAULT_MAX_VOICES = 8


@dataclass(frozen=True, slots=True)
class ArrangeResult:
    """The arrangement, and what it cost to get there."""

    score: Score
    dropped: tuple[Note, ...] = ()
    was_already_arranged: bool = False

    def summary(self) -> str:
        if self.was_already_arranged:
            return "already two hands: left alone"
        if not self.dropped:
            return "reduced to two hands, nothing dropped"
        return f"reduced to two hands, {len(self.dropped)} note(s) dropped"


def _instants(notes: list[Note], tolerance: float) -> list[tuple[float, int, int]]:
    events: list[tuple[float, int, int]] = []
    for index, note in enumerate(notes):
        events.append((note.start, 1, index))
        events.append((max(note.start, note.end - tolerance), 0, index))
    events.sort()
    return events


def reduce_texture(
    notes: list[Note],
    max_voices: int = DEFAULT_MAX_VOICES,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> tuple[list[Note], list[Note]]:
    """Thin the texture until no more than ``max_voices`` ever sound together.

    Sweeps once, and whenever the held set is too large removes the least
    salient note in it. Outer voices score highly in
    :func:`contextual_salience`, so the melody and the bass survive and the
    inner harmony is what gives way.
    """
    if max_voices <= 0:
        raise ValueError(f"max_voices must be positive, got {max_voices}")

    held: list[tuple[int, int]] = []
    dropped: set[int] = set()

    for _time, is_start, index in _instants(notes, tolerance):
        note = notes[index]
        if not is_start:
            entry = (note.pitch, index)
            if entry in held:
                held.remove(entry)
            continue
        if index in dropped:
            continue

        insort(held, (note.pitch, index))
        while len(held) > max_voices:
            chord = [notes[i] for _, i in held]
            worst = min(
                range(len(held)),
                key=lambda position: (
                    contextual_salience(notes[held[position][1]], chord),
                    -notes[held[position][1]].pitch,
                ),
            )
            _, victim = held.pop(worst)
            dropped.add(victim)

    kept = [note for index, note in enumerate(notes) if index not in dropped]
    removed = [notes[index] for index in sorted(dropped)]
    return kept, removed


def _best_split(sounding: list[int], previous: int, max_span: int) -> int:
    """Choose the pitch to divide the hands at, for one instant.

    Candidates are the gaps between the sounding pitches. A candidate is scored
    on whether each hand then fits inside ``max_span``, how evenly the notes
    divide, and how far the split has moved since the last instant.

    That last term is what makes this work where a fixed split does not. Voices
    crossing do not confuse it, because the split follows the music rather than
    sitting at one pitch; and a chord does not fling the hands across the
    keyboard, because moving is penalised.
    """
    if len(sounding) < 2:
        return previous

    candidates = {previous}
    for lower, upper in pairwise(sounding):
        candidates.add((lower + upper + 1) // 2)

    def cost(split: int) -> tuple[int, int, int]:
        left = [p for p in sounding if p < split]
        right = [p for p in sounding if p >= split]
        over = 0
        if left:
            over += max(0, (left[-1] - left[0]) - max_span)
        if right:
            over += max(0, (right[-1] - right[0]) - max_span)
        imbalance = abs(len(left) - len(right))
        return (over, imbalance, abs(split - previous))

    return min(candidates, key=cost)


def assign_hands(
    notes: list[Note],
    max_span: int,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> list[Note]:
    """Give every note a hand, using a split that moves with the music.

    Notes already sounding keep the hand they were given, so a held note is
    never yanked across mid-way. Only notes starting at an instant are assigned.
    """
    if not notes:
        return []

    assigned: dict[int, Hand] = {}
    held: list[tuple[int, int]] = []
    split = 60  # middle C, until the music says otherwise

    events = _instants(notes, tolerance)
    position = 0
    while position < len(events):
        time = events[position][0]
        starting: list[int] = []
        while position < len(events) and events[position][0] == time:
            _, is_start, index = events[position]
            note = notes[index]
            if is_start:
                insort(held, (note.pitch, index))
                starting.append(index)
            else:
                entry = (note.pitch, index)
                if entry in held:
                    held.remove(entry)
            position += 1

        if not starting:
            continue

        split = _best_split([pitch for pitch, _ in held], split, max_span)
        for index in starting:
            assigned[index] = Hand.LEFT if notes[index].pitch < split else Hand.RIGHT

    return [
        note.assigned_to(assigned.get(index, Hand.RIGHT))
        for index, note in enumerate(notes)
    ]


def looks_arranged(score: Score) -> bool:
    """Whether the score is already two-hand piano writing.

    A file that arrived with its hands separated, or one that has been through
    this stage before, must come out untouched.
    """
    hands = {note.hand for note in score.notes}
    return bool(hands) and hands <= {Hand.LEFT, Hand.RIGHT}


def arrange(
    score: Score,
    max_span: int = 12,
    max_voices: int = DEFAULT_MAX_VOICES,
    tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S,
) -> ArrangeResult:
    """Reduce ``score`` to two hands."""
    if score.is_empty:
        return ArrangeResult(score=score, was_already_arranged=True)

    if looks_arranged(score):
        log.info("score already has two hands; leaving it alone")
        return ArrangeResult(score=score, was_already_arranged=True)

    notes = list(score.notes)
    kept, dropped = reduce_texture(notes, max_voices, tolerance)
    handed = assign_hands(kept, max_span, tolerance)

    log.info(
        "arranged %d note(s) into two hands, dropping %d",
        len(handed),
        len(dropped),
    )
    return ArrangeResult(score=score.with_notes(handed), dropped=tuple(dropped))
