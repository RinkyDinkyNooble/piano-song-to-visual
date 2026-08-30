"""Making an over-wide chord playable.

Four repairs, tried in order of what they cost the music:

1. **Reassign.** Move the outlier to the other hand. Nothing is heard
   differently, so it is always tried first.
2. **Truncate, pedal down.** Shorten the note that is being held into the
   stretch. While the sustain pedal is down the string keeps ringing, so
   lifting the key early is inaudible. This is why the engine reads CC64 at
   all, and why truncation outranks octave shifting *only* in that case.
3. **Octave shift.** Move the outlier by a whole octave. Pitch class and
   harmonic function survive; the register does not.
4. **Truncate, pedal up.** The same edit, now actually audible as a shorter
   note, so it ranks below moving the octave.
5. **Drop.** Remove the least salient of the two extremes. Always available,
   which is what makes the whole loop guaranteed to terminate.

Everything is recorded. A :class:`Repair` per edit means the output can be
audited note by note, rather than being taken on trust.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from psv.config import Config
from psv.model import (
    DEFAULT_OVERLAP_TOLERANCE_S,
    HIGHEST_KEY,
    LOWEST_KEY,
    Hand,
    Note,
    Pedal,
    PedalEvent,
    Score,
)

from .difficulty import apply_difficulty
from .hands import ensure_hands
from .salience import contextual_salience
from .span import Violation, detect_violations, verify_span

log = logging.getLogger(__name__)

#: How many detect-and-repair rounds before falling back to dropping only.
#: A repair can move a note into a fresh conflict elsewhere, so the loop needs
#: a ceiling; in practice a handful of rounds settles even dense organ writing.
MAX_PASSES = 12

#: A note may be moved by at most this many octaves in total, so a note cannot
#: oscillate up and down between two conflicts forever.
MAX_OCTAVE_SHIFTS = 3


class ConstraintError(RuntimeError):
    """The engine failed to satisfy its own guarantee. Always a bug here."""


@dataclass(frozen=True, slots=True)
class Repair:
    """One edit, and why it happened."""

    strategy: str
    hand: Hand
    time: float
    before: Note
    after: Note | None

    @property
    def dropped(self) -> bool:
        return self.after is None

    def __str__(self) -> str:
        where = f"{self.before.name} at {self.before.start:.2f}s"
        if self.after is None:
            return f"drop {where}"
        if self.after.pitch != self.before.pitch:
            return f"{self.strategy} {where} -> {self.after.name}"
        if self.after.hand is not self.before.hand:
            return f"{self.strategy} {where} -> {self.after.hand.value} hand"
        return f"{self.strategy} {where} -> ends {self.after.end:.2f}s"


@dataclass(frozen=True, slots=True)
class ConstrainResult:
    """What came out, and everything that was done to get there."""

    score: Score
    repairs: tuple[Repair, ...] = ()
    removed_for_difficulty: tuple[Note, ...] = ()
    violations_before: int = 0
    passes: int = 0

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for repair in self.repairs:
            counts[repair.strategy] = counts.get(repair.strategy, 0) + 1
        return counts

    def summary(self) -> str:
        if not self.violations_before and not self.removed_for_difficulty:
            return "nothing to do: already playable at this span"
        lines = [
            f"{self.violations_before} span violation(s) found, "
            f"resolved in {self.passes} pass(es)"
        ]
        if self.removed_for_difficulty:
            lines.append(
                f"  difficulty       {len(self.removed_for_difficulty)} note(s) removed"
            )
        for strategy, count in sorted(self.counts.items()):
            lines.append(f"  {strategy:16} {count}")
        return "\n".join(lines)


@dataclass
class _Working:
    """Mutable state for one run. Indices stay stable within a pass."""

    notes: list[Note]
    dropped: set[int] = field(default_factory=set)
    shifts: dict[int, int] = field(default_factory=dict)
    reassigned: set[int] = field(default_factory=set)

    def live(self, indices: Sequence[int]) -> list[int]:
        return [i for i in indices if i not in self.dropped]

    def compact(self) -> None:
        if not self.dropped:
            return
        self.notes = [n for i, n in enumerate(self.notes) if i not in self.dropped]
        self.dropped.clear()
        self.shifts.clear()
        self.reassigned.clear()


def _other_hand(hand: Hand) -> Hand | None:
    if hand is Hand.LEFT:
        return Hand.RIGHT
    if hand is Hand.RIGHT:
        return Hand.LEFT
    return None


def _pedal_down(pedals: Sequence[PedalEvent], time: float) -> bool:
    return any(
        event.pedal is Pedal.SUSTAIN and event.active_at(time) for event in pedals
    )


def _span(pitches: Sequence[int]) -> int:
    return max(pitches) - min(pitches) if pitches else 0


def _choose_outlier(state: _Working, held: list[int]) -> int:
    """Which extreme to move.

    Removing either end narrows the set. Take whichever removal narrows it more;
    when both are equal, give up the one the music will miss least.
    """
    pitches = sorted((state.notes[i].pitch, i) for i in held)
    without_low = pitches[-1][0] - pitches[1][0]
    without_high = pitches[-2][0] - pitches[0][0]
    if without_low < without_high:
        return pitches[0][1]
    if without_high < without_low:
        return pitches[-1][1]

    chord = [state.notes[i] for i in held]
    low, high = pitches[0][1], pitches[-1][1]
    return min(
        (low, high),
        key=lambda i: (
            contextual_salience(state.notes[i], chord),
            -state.notes[i].pitch,
        ),
    )


# -- the individual repairs ----------------------------------------------


def _try_reassign(
    state: _Working, violation: Violation, held: list[int], outlier: int, max_span: int
) -> tuple[int, Note] | None:
    del held
    other = _other_hand(violation.hand)
    if other is None or outlier in state.reassigned:
        # Moving a note twice risks it bouncing between hands forever.
        return None

    across = state.live(violation.active.get(other, ()))
    pitches = [state.notes[i].pitch for i in across] + [state.notes[outlier].pitch]
    if _span(pitches) > max_span:
        return None

    state.reassigned.add(outlier)
    return outlier, state.notes[outlier].assigned_to(other)


def _try_octave_shift(
    state: _Working, violation: Violation, held: list[int], outlier: int, max_span: int
) -> tuple[int, Note] | None:
    del violation, max_span
    if state.shifts.get(outlier, 0) >= MAX_OCTAVE_SHIFTS:
        return None

    pitches = sorted((state.notes[i].pitch, i) for i in held)
    direction = 1 if outlier == pitches[0][1] else -1
    candidate = state.notes[outlier].octave_shifted(direction)
    if not LOWEST_KEY <= candidate.pitch <= HIGHEST_KEY:
        return None

    others = [state.notes[i].pitch for i in held if i != outlier]
    if candidate.pitch in others:
        # Landing on a note the same hand is already holding would silently
        # merge two voices into one.
        return None
    if _span([*others, candidate.pitch]) >= _span([p for p, _ in pitches]):
        # Insist on strict improvement, so a note cannot shift back and forth.
        return None

    state.shifts[outlier] = state.shifts.get(outlier, 0) + 1
    return outlier, candidate


def _try_truncate(
    state: _Working, violation: Violation, held: list[int], outlier: int, max_span: int
) -> tuple[int, Note] | None:
    del outlier, max_span
    pitches = sorted((state.notes[i].pitch, i) for i in held)
    low, high = pitches[0][1], pitches[-1][1]
    earlier, later = (
        (low, high)
        if state.notes[low].start <= state.notes[high].start
        else (high, low)
    )

    start_gap = state.notes[later].start - state.notes[earlier].start
    if start_gap <= DEFAULT_OVERLAP_TOLERANCE_S:
        # Struck together. Shortening one cannot separate them.
        return None

    new_end = state.notes[later].start
    if new_end >= state.notes[earlier].end:
        return None
    del violation
    return earlier, state.notes[earlier].shortened_to(new_end)


def _drop(state: _Working, held: list[int], outlier: int) -> tuple[int, None]:
    del outlier
    chord = [state.notes[i] for i in held]
    pitches = sorted((state.notes[i].pitch, i) for i in held)
    low, high = pitches[0][1], pitches[-1][1]
    victim = min(
        (low, high),
        key=lambda i: (
            contextual_salience(state.notes[i], chord),
            -state.notes[i].pitch,
        ),
    )
    return victim, None


# -- the loop ------------------------------------------------------------


def _repair_violation(
    state: _Working,
    violation: Violation,
    pedals: Sequence[PedalEvent],
    max_span: int,
) -> Repair | None:
    """Apply the cheapest repair that works. Returns None if already resolved."""
    held = state.live(violation.indices)
    if len(held) < 2:
        return None
    if _span([state.notes[i].pitch for i in held]) <= max_span:
        return None

    outlier = _choose_outlier(state, held)
    pedal = _pedal_down(pedals, violation.time)

    # Truncation is nearly free while the pedal holds the note ringing, so it
    # jumps ahead of octave shifting in that case and falls behind it otherwise.
    order: list[tuple[str, object]] = [("reassign", _try_reassign)]
    if pedal:
        order.append(("truncate-under-pedal", _try_truncate))
        order.append(("octave-shift", _try_octave_shift))
    else:
        order.append(("octave-shift", _try_octave_shift))
        order.append(("truncate", _try_truncate))

    for name, strategy in order:
        outcome = strategy(state, violation, held, outlier, max_span)  # type: ignore[operator]
        if outcome is None:
            continue
        index, replacement = outcome
        before = state.notes[index]
        state.notes[index] = replacement
        return Repair(name, violation.hand, violation.time, before, replacement)

    index, _ = _drop(state, held, outlier)
    before = state.notes[index]
    state.dropped.add(index)
    return Repair("drop", violation.hand, violation.time, before, None)


def _force_clean(state: _Working, max_span: int, tolerance: float) -> list[Repair]:
    """Last resort: drop until nothing violates.

    Dropping strictly reduces the number of notes, and a hand holding fewer than
    two notes cannot violate anything, so this always terminates. It is what
    makes the guarantee unconditional rather than best-effort.
    """
    repairs: list[Repair] = []
    while True:
        state.compact()
        violations = detect_violations(state.notes, max_span, tolerance)
        if not violations:
            return repairs
        violation = violations[0]
        held = state.live(violation.indices)
        if len(held) < 2:  # pragma: no cover - compact() keeps indices live
            return repairs
        index, _ = _drop(state, held, _choose_outlier(state, held))
        before = state.notes[index]
        state.dropped.add(index)
        repairs.append(Repair("drop", violation.hand, violation.time, before, None))


def constrain(score: Score, config: Config) -> ConstrainResult:
    """Make ``score`` playable within the configured hand span.

    Order matters: hands first, because span is per hand; difficulty next,
    because it only removes notes; span enforcement last, so nothing downstream
    of it can widen a reach.

    The postcondition is checked before returning, on every call and not only
    under test. If it fails, that is a bug in this module and it raises rather
    than handing back a score that quietly cannot be played.
    """
    max_span = config.hands.max_span_semitones
    tolerance = config.hands.overlap_tolerance_s

    score = ensure_hands(score)
    score, removed = apply_difficulty(score, config.difficulty.level, tolerance)

    state = _Working(notes=list(score.notes))
    violations_before = len(detect_violations(state.notes, max_span, tolerance))
    repairs: list[Repair] = []
    passes = 0

    for passes in range(1, MAX_PASSES + 1):  # noqa: B007
        violations = detect_violations(state.notes, max_span, tolerance)
        if not violations:
            break
        for violation in violations:
            repair = _repair_violation(state, violation, score.pedals, max_span)
            if repair is not None:
                repairs.append(repair)
        state.compact()
    else:
        log.warning(
            "span repair did not settle in %d passes; dropping the remainder",
            MAX_PASSES,
        )

    state.compact()
    repairs.extend(_force_clean(state, max_span, tolerance))

    result = score.with_notes(state.notes)

    remaining = verify_span(result, max_span, tolerance)
    if remaining:  # pragma: no cover - the guarantee, asserted in production
        raise ConstraintError(
            f"constrain left {len(remaining)} violation(s), first: {remaining[0]}. "
            "This is a bug in psv.constraints, not in the input."
        )

    log.info(
        "constrained: %d violation(s) -> %d repair(s) in %d pass(es)",
        violations_before,
        len(repairs),
        passes,
    )
    return ConstrainResult(
        score=result,
        repairs=tuple(repairs),
        removed_for_difficulty=removed,
        violations_before=violations_before,
        passes=passes,
    )
