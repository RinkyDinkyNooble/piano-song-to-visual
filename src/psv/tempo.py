"""Tempo and meter, so no other stage has to think about ticks.

A MIDI file measures time in ticks, whose duration depends on whichever tempo
is in force. Everything downstream of parsing works in seconds and in beats.
``TempoMap`` is the only place that converts between the three.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

#: MIDI's default when a file states no tempo: 120 BPM.
DEFAULT_US_PER_BEAT = 500_000

MICROSECONDS_PER_SECOND = 1_000_000


def us_per_beat_to_bpm(us_per_beat: int) -> float:
    return 60_000_000 / us_per_beat


@dataclass(frozen=True, slots=True)
class TempoChange:
    """A tempo in force from ``tick`` (equivalently ``seconds``) onward."""

    tick: int
    seconds: float
    us_per_beat: int

    @property
    def bpm(self) -> float:
        return us_per_beat_to_bpm(self.us_per_beat)


@dataclass(frozen=True, slots=True)
class TimeSignature:
    """A meter in force from ``tick`` onward."""

    tick: int
    seconds: float
    numerator: int
    denominator: int

    @property
    def beats_per_bar(self) -> float:
        """Length of one bar in quarter notes, which is what a MIDI beat is."""
        return self.numerator * 4 / self.denominator


@dataclass(frozen=True, slots=True)
class TempoMap:
    """Converts between ticks, beats, and seconds.

    ``changes`` is non-empty, sorted by tick, and always starts at tick 0, so
    every lookup has something to find. Build one with :meth:`constant` or
    :meth:`from_changes` rather than calling the constructor directly.
    """

    ticks_per_beat: int
    changes: tuple[TempoChange, ...]

    @classmethod
    def constant(cls, ticks_per_beat: int, bpm: float = 120.0) -> TempoMap:
        us = round(60_000_000 / bpm)
        return cls(ticks_per_beat, (TempoChange(0, 0.0, us),))

    @classmethod
    def from_changes(
        cls, ticks_per_beat: int, raw: Sequence[tuple[int, int]]
    ) -> TempoMap:
        """Build from ``(tick, us_per_beat)`` pairs in any order.

        A file with no tempo event, or whose first event is not at tick 0, gets
        the MIDI default filling the gap at the start.
        """
        if ticks_per_beat <= 0:
            raise ValueError(f"ticks_per_beat must be positive, got {ticks_per_beat}")

        # Sort on tick alone. Python's sort is stable, so two tempo events on
        # the same tick keep the order the file gave them and the later wins.
        # Sorting on the whole tuple would order those by tempo value instead.
        ordered = sorted(raw, key=lambda item: item[0])
        if not ordered or ordered[0][0] > 0:
            ordered.insert(0, (0, DEFAULT_US_PER_BEAT))

        changes: list[TempoChange] = []
        seconds = 0.0
        previous_tick = 0
        previous_us = ordered[0][1]
        for tick, us in ordered:
            if us <= 0:
                raise ValueError(f"tempo at tick {tick} is not positive: {us}")
            seconds += _tick_span_seconds(
                tick - previous_tick, previous_us, ticks_per_beat
            )
            if changes and changes[-1].tick == tick:
                # Two tempo events on the same tick: the later one wins.
                changes[-1] = TempoChange(tick, changes[-1].seconds, us)
            else:
                changes.append(TempoChange(tick, seconds, us))
            previous_tick, previous_us = tick, us

        return cls(ticks_per_beat, tuple(changes))

    # -- lookups ---------------------------------------------------------

    def _change_at_tick(self, tick: int) -> TempoChange:
        found = self.changes[0]
        for change in self.changes:
            if change.tick > tick:
                break
            found = change
        return found

    def _change_at_seconds(self, seconds: float) -> TempoChange:
        found = self.changes[0]
        for change in self.changes:
            if change.seconds > seconds:
                break
            found = change
        return found

    def tick_to_seconds(self, tick: int) -> float:
        change = self._change_at_tick(tick)
        return change.seconds + _tick_span_seconds(
            tick - change.tick, change.us_per_beat, self.ticks_per_beat
        )

    def seconds_to_tick(self, seconds: float) -> int:
        change = self._change_at_seconds(seconds)
        elapsed = seconds - change.seconds
        beats = elapsed * MICROSECONDS_PER_SECOND / change.us_per_beat
        return change.tick + round(beats * self.ticks_per_beat)

    def tick_to_beat(self, tick: int) -> float:
        return tick / self.ticks_per_beat

    def beat_to_tick(self, beat: float) -> int:
        return round(beat * self.ticks_per_beat)

    def beat_to_seconds(self, beat: float) -> float:
        return self.tick_to_seconds(self.beat_to_tick(beat))

    def seconds_to_beat(self, seconds: float) -> float:
        return self.tick_to_beat(self.seconds_to_tick(seconds))

    def bpm_at(self, seconds: float) -> float:
        return self._change_at_seconds(seconds).bpm

    def beat_times(self, until_seconds: float, step: float = 1.0) -> Iterator[float]:
        """Yield the wall-clock time of every beat boundary up to a limit.

        This is what the renderer's vertical grid is drawn from, which is why it
        walks beats and converts each one rather than stepping in seconds: the
        lines have to stay on the beat through a tempo change.
        """
        if step <= 0:
            raise ValueError(f"step must be positive, got {step}")
        beat = 0.0
        while True:
            seconds = self.beat_to_seconds(beat)
            if seconds > until_seconds:
                return
            yield seconds
            beat += step

    @property
    def is_constant(self) -> bool:
        return len(self.changes) == 1


def _tick_span_seconds(ticks: int, us_per_beat: int, ticks_per_beat: int) -> float:
    return ticks * us_per_beat / ticks_per_beat / MICROSECONDS_PER_SECOND
