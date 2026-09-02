"""Turning a finished arrangement into something you can practise against.

Four things, and they are all the same kind of thing: they change how the piece
is *presented* without changing what it is. Play it slower. Play forty bars of
it. Count yourself in. Play one hand.

None of them touches the arrangement. Every function here is
``Score -> Score`` or ``Score -> a window in seconds``, and the score they are
handed has already been through arrange and constrain, so the notes are settled
before any of this runs. That ordering matters for tempo in particular: the
constraint engine measures overlaps in real seconds against a fixed tolerance,
so scaling the time before it ran would change which stretches it repaired and
give you a different arrangement at every practice speed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from psv.config import PracticeConfig
from psv.model import Hand, Note, PedalEvent, Score
from psv.tempo import TempoMap

# -- tempo ---------------------------------------------------------------


def time_scaled(score: Score, factor: float) -> Score:
    """Stretch or compress the whole piece in time, keeping every pitch.

    ``factor`` is playback speed: 0.75 plays at three-quarters of the written
    tempo, so everything takes 1/0.75 as long. The tempo map is scaled with the
    notes, so the beat and bar lines stay on the beats and bars.

    Tempo is stored as an integer number of microseconds per beat, so the scaled
    map is rounded to the nearest microsecond. At 500,000 us per beat that is
    about two parts in a million: far below a frame at any frame rate.
    """
    if factor <= 0:
        raise ValueError(f"tempo factor must be positive, got {factor}")
    if factor == 1.0:
        return score

    stretch = 1.0 / factor

    tempo_map = TempoMap.from_changes(
        score.tempo_map.ticks_per_beat,
        [
            (change.tick, max(1, round(change.us_per_beat * stretch)))
            for change in score.tempo_map.changes
        ],
    )

    parts = tuple(
        part.with_notes(_scaled_note(note, stretch) for note in part.notes)
        for part in score.parts
    )
    pedals = tuple(_scaled_pedal(event, stretch) for event in score.pedals)
    signatures = tuple(
        replace(signature, seconds=tempo_map.tick_to_seconds(signature.tick))
        for signature in score.time_signatures
    )

    return replace(
        score,
        parts=parts,
        pedals=pedals,
        tempo_map=tempo_map,
        time_signatures=signatures,
    )


def _scaled_note(note: Note, stretch: float) -> Note:
    return replace(note, start=note.start * stretch, end=note.end * stretch)


def _scaled_pedal(event: PedalEvent, stretch: float) -> PedalEvent:
    return replace(event, start=event.start * stretch, end=event.end * stretch)


# -- one hand at a time --------------------------------------------------


def for_hand(score: Score, hand: Hand) -> Score:
    """Only the notes ``hand`` plays.

    Used for the soundtrack, not the picture: the other hand stays on screen,
    drawn faintly, so you can still see where it is. Pedalling is kept, because
    the pedal is shared and a passage practised without it sounds wrong.
    """
    kept = [note for note in score.notes if note.hand is hand]
    return score.with_notes(kept)


# -- section practice ----------------------------------------------------


def bar_window(
    score: Score, first_bar: int, last_bar: int, *, tail: float
) -> tuple[float, float]:
    """When bars ``first_bar`` to ``last_bar`` start, and how long they run.

    Inclusive of both ends, which is how anyone reading a score means it. The
    span runs to the start of the bar *after* the last one, plus ``tail``: the
    final bar's notes reach the keyboard at the moment the section would
    otherwise stop, and cutting there would show them landing and never
    sounding.
    """
    if first_bar < 1:
        raise ValueError(f"bars are numbered from 1, got {first_bar}")
    if last_bar < first_bar:
        raise ValueError(f"bar range runs backwards: {first_bar}-{last_bar}")

    meter = score.meter
    start = meter.bar_start(first_bar)
    end = meter.bar_start(last_bar + 1)
    return start, max(0.0, end - start) + tail


# -- count-in and metronome ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Click:
    """One metronome click. ``accent`` marks the first beat of a bar."""

    time: float
    accent: bool


def count_in_seconds(score: Score, music_start: float, bars: int) -> float:
    """How much silence to put in front of the music for a count-in.

    Measured at the tempo and meter in force where the music starts, not at the
    top of the piece, so counting into bar 40 of a piece that has slowed down
    counts at the speed you are about to play.
    """
    if bars <= 0:
        return 0.0
    beat_s = 60.0 / score.tempo_map.bpm_at(music_start)
    return bars * score.meter.beats_per_bar_at(music_start) * beat_s


def click_times(
    score: Score,
    *,
    music_start: float,
    end: float,
    count_in_bars: int = 0,
    metronome: bool = False,
) -> tuple[Click, ...]:
    """Every click to sound between the count-in and ``end``.

    The count-in beats are extrapolated backwards from ``music_start`` at a
    steady tempo, because there is no music there to follow and a count-in that
    accelerates is useless. Clicks during the piece come from the tempo map and
    the bar index instead, so they follow tempo and meter changes.
    """
    clicks: list[Click] = []
    if count_in_bars > 0:
        clicks.extend(_count_in_clicks(score, music_start, count_in_bars))
    if metronome:
        clicks.extend(_metronome_clicks(score, music_start, end))
    return tuple(clicks)


def _count_in_clicks(score: Score, music_start: float, bars: int) -> list[Click]:
    beat_s = 60.0 / score.tempo_map.bpm_at(music_start)
    per_bar = max(1, round(score.meter.beats_per_bar_at(music_start)))
    total = bars * per_bar
    return [
        Click(music_start - (total - index) * beat_s, index % per_bar == 0)
        for index in range(total)
    ]


def _metronome_clicks(score: Score, music_start: float, end: float) -> list[Click]:
    """Clicks on every beat of every bar, accented on the downbeat.

    Walks bars rather than stepping in seconds so the accents land on real bar
    lines. A bar whose length is not a whole number of beats, 7/8 for one, gets
    a click on each whole beat it contains and the remainder falls to the next
    downbeat, which is where the accent belongs anyway.
    """
    if end <= music_start:
        return []
    meter = score.meter
    tempo_map = score.tempo_map
    clicks: list[Click] = []
    bar = meter.bar_at(music_start)
    while True:
        base = meter.bar_start_beat(bar)
        length = meter.bar_beats(bar)
        offset = 0.0
        while offset < length - 1e-9:
            seconds = tempo_map.beat_to_seconds(base + offset)
            if seconds >= end:
                return clicks
            if seconds >= music_start:
                clicks.append(Click(seconds, offset == 0.0))
            offset += 1.0
        bar += 1


# -- putting it together -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Presentation:
    """A score and the span of it to show, with the practice settings applied.

    One object because the four settings interact: a count-in has to know where
    the section starts, the section is measured in bars whose length depends on
    the tempo scaling, and the clicks have to land in the same time base as the
    notes. Working any of them out separately is how they drift apart.
    """

    score: Score
    start: float
    duration: float | None
    focus: Hand | None
    clicks: tuple[Click, ...]
    label: str

    @property
    def audio_score(self) -> Score:
        """The notes that should sound, which is one hand's when focused."""
        if self.focus is None:
            return self.score
        return for_hand(self.score, self.focus)


def prepare(
    score: Score,
    config: PracticeConfig,
    *,
    start: float = 0.0,
    seconds: float | None = None,
    bars: tuple[int, int] | None = None,
    tail: float,
) -> Presentation:
    """Apply the practice settings to a finished arrangement.

    ``start`` and ``seconds`` are in the time base of the *output*, so they mean
    the same thing at any practice tempo. ``bars`` overrides both, and is where
    you want to be thinking anyway.

    The count-in is a window that opens before the music rather than silence
    spliced into the score, so the notes keep the times they already had and the
    picture and the soundtrack cannot disagree about where the music starts.
    """
    scaled = time_scaled(score, config.tempo)

    if bars is not None:
        start, seconds = bar_window(scaled, bars[0], bars[1], tail=tail)

    lead_in = count_in_seconds(scaled, start, config.count_in_bars)
    end = start + seconds if seconds is not None else scaled.duration + tail
    clicks = click_times(
        scaled,
        music_start=start,
        end=end,
        count_in_bars=config.count_in_bars,
        metronome=config.metronome,
    )

    return Presentation(
        score=scaled,
        start=start - lead_in,
        duration=None if seconds is None else seconds + lead_in,
        focus=_focus(config.hands),
        clicks=clicks,
        label=_label(config, bars),
    )


def _focus(hands: str) -> Hand | None:
    return {"left": Hand.LEFT, "right": Hand.RIGHT}.get(hands)


def _label(config: PracticeConfig, bars: tuple[int, int] | None) -> str:
    """A one-line description of what was applied, empty when nothing was."""
    parts: list[str] = []
    if config.tempo != 1.0:
        parts.append(f"{config.tempo:g}x tempo")
    if bars is not None:
        first, last = bars
        parts.append(f"bar {first}" if first == last else f"bars {first}-{last}")
    if config.hands != "both":
        parts.append(f"{config.hands} hand")
    if config.count_in_bars:
        parts.append(f"{config.count_in_bars}-bar count-in")
    if config.metronome:
        parts.append("metronome")
    return ", ".join(parts)
