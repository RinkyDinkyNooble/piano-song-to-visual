"""Tempo and meter, so no other stage has to think about ticks.

A MIDI file measures time in ticks, whose duration depends on whichever tempo
is in force. Everything downstream of parsing works in seconds and in beats.
``TempoMap`` is the only place that converts between the three.
"""

from __future__ import annotations

import math
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


@dataclass(frozen=True, slots=True)
class MeterSegment:
    """One time signature's stretch of the piece, measured in beats."""

    start_beat: float
    beats_per_bar: float
    first_bar: int


@dataclass(frozen=True, slots=True)
class Meter:
    """Where the bar lines fall.

    A bar boundary needs both halves of the timing: the time signatures say how
    many beats are in a bar, and the tempo map says when those beats happen. So
    neither ``TempoMap`` nor ``TimeSignature`` can answer "when does bar 31
    start" alone, and this is the pair that can.

    Bars are numbered from 1, and a time-signature change starts a new one. That
    is the convention printed music follows, and it is what lets a partial bar
    before a meter change keep its own number instead of silently merging into
    the next.

    Times before bar 1 are not defined here. A count-in extrapolates backwards
    from the tempo at the start; see :mod:`psv.practice`.
    """

    tempo_map: TempoMap
    #: Non-empty, sorted by tick, starting at tick 0. Build with
    #: :meth:`from_score_data` rather than the constructor.
    segments: tuple[MeterSegment, ...]

    @classmethod
    def from_score_data(
        cls, tempo_map: TempoMap, time_signatures: Sequence[TimeSignature]
    ) -> Meter:
        """Build the bar index from a score's tempo map and time signatures."""
        ordered: list[TimeSignature] = []
        for signature in sorted(time_signatures, key=lambda sig: sig.tick):
            if ordered and ordered[-1].tick == signature.tick:
                ordered[-1] = signature  # two on one tick: the later one wins
            else:
                ordered.append(signature)
        if not ordered or ordered[0].tick > 0:
            ordered.insert(0, TimeSignature(0, 0.0, 4, 4))

        segments: list[MeterSegment] = []
        first_bar = 1
        for index, signature in enumerate(ordered):
            start_beat = tempo_map.tick_to_beat(signature.tick)
            segments.append(
                MeterSegment(
                    start_beat=start_beat,
                    beats_per_bar=signature.beats_per_bar,
                    first_bar=first_bar,
                )
            )
            if index + 1 < len(ordered):
                span = tempo_map.tick_to_beat(ordered[index + 1].tick) - start_beat
                # A meter change part way through a bar still ends that bar, so
                # round the count up rather than dropping the remainder.
                first_bar += max(1, _bars_in(span, signature.beats_per_bar))

        return cls(tempo_map, tuple(segments))

    # -- lookups ---------------------------------------------------------

    def _segment_for_bar(self, bar: int) -> MeterSegment:
        found = self.segments[0]
        for segment in self.segments:
            if segment.first_bar > bar:
                break
            found = segment
        return found

    def _segment_for_beat(self, beat: float) -> MeterSegment:
        found = self.segments[0]
        for segment in self.segments:
            if segment.start_beat > beat:
                break
            found = segment
        return found

    def bar_start_beat(self, bar: int) -> float:
        """The beat position where ``bar`` begins. Bar 1 begins at beat 0."""
        if bar < 1:
            raise ValueError(f"bars are numbered from 1, got {bar}")
        segment = self._segment_for_bar(bar)
        return segment.start_beat + (bar - segment.first_bar) * segment.beats_per_bar

    def bar_start(self, bar: int) -> float:
        """The wall-clock second where ``bar`` begins."""
        return self.tempo_map.beat_to_seconds(self.bar_start_beat(bar))

    def bar_beats(self, bar: int) -> float:
        """How many beats long ``bar`` is, under the meter in force there."""
        if bar < 1:
            raise ValueError(f"bars are numbered from 1, got {bar}")
        return self._segment_for_bar(bar).beats_per_bar

    def bar_at(self, seconds: float) -> int:
        """Which bar is sounding at ``seconds``."""
        beat = self.tempo_map.seconds_to_beat(seconds)
        segment = self._segment_for_beat(beat)
        offset = int((beat - segment.start_beat) / segment.beats_per_bar + 1e-9)
        return segment.first_bar + max(0, offset)

    def beats_per_bar_at(self, seconds: float) -> float:
        beat = self.tempo_map.seconds_to_beat(seconds)
        return self._segment_for_beat(beat).beats_per_bar

    def bar_times(
        self, until_seconds: float, *, since_seconds: float = 0.0
    ) -> Iterator[tuple[int, float]]:
        """Yield ``(bar number, seconds)`` for every bar line in a span.

        Walks bar numbers rather than stepping in seconds, so the lines stay on
        the bars through a tempo change. ``since_seconds`` skips straight to the
        first bar in view instead of counting up from the top of the piece.
        """
        bar = self.bar_at(max(0.0, since_seconds))
        while True:
            seconds = self.bar_start(bar)
            if seconds > until_seconds:
                return
            if seconds >= since_seconds:
                yield bar, seconds
            bar += 1


def _bars_in(span_beats: float, beats_per_bar: float) -> int:
    """How many bars a span covers, counting a partial bar as a whole one."""
    return math.ceil(span_beats / beats_per_bar - 1e-9)
