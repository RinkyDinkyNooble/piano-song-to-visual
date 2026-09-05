"""The Score: the one data structure every pipeline stage reads and writes.

Deliberately carries more than MIDI does. A ``Note`` knows which hand plays it
and what the pipeline has already done to it, so the constraint engine's
decisions can be audited afterwards instead of being taken on trust.

Everything here is immutable. Stages are ``Score -> Score`` functions, and
frozen dataclasses make it impossible to mutate a score a caller still holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum
from pathlib import Path

from psv.tempo import Meter, TempoMap, TimeSignature

#: An 88-key piano runs from A0 to C8 in MIDI note numbers.
LOWEST_KEY = 21
HIGHEST_KEY = 108

#: Pitch classes that sit on a black key, as semitones above C.
_BLACK_PITCH_CLASSES = frozenset({1, 3, 6, 8, 10})

#: Two notes overlapping by less than this are sloppy MIDI, not a stretch a
#: player has to hold. See docs/ARCHITECTURE.md, stage 3.
DEFAULT_OVERLAP_TOLERANCE_S = 0.03


class Hand(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    #: Parsing does not guess. The arrange stage assigns hands.
    UNASSIGNED = "unassigned"


class Provenance(StrEnum):
    """What the pipeline has done to a note."""

    ORIGINAL = "original"
    REASSIGNED = "reassigned"
    OCTAVE_SHIFTED = "octave-shifted"
    TRUNCATED = "truncated"
    ADDED = "added"


class Pedal(IntEnum):
    """The three pedals, by their MIDI controller numbers."""

    SUSTAIN = 64
    SOSTENUTO = 66
    SOFT = 67


def is_black_key(pitch: int) -> bool:
    return pitch % 12 in _BLACK_PITCH_CLASSES


def pitch_name(pitch: int) -> str:
    """Scientific pitch notation, e.g. 60 -> C4."""
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


@dataclass(frozen=True, slots=True)
class Note:
    """One key press."""

    pitch: int
    start: float
    end: float
    velocity: int = 64
    hand: Hand = Hand.UNASSIGNED
    provenance: tuple[Provenance, ...] = (Provenance.ORIGINAL,)
    source_track: int = 0
    channel: int = 0

    @property
    def sort_key(self) -> tuple[float, int, float, int, str, int]:
        """A *total* order, so a set of notes has one canonical sequence.

        Playing order first: by start time, then low pitch to high. The
        remaining fields only break ties, but they have to be there. Two notes
        alike in pitch and timing but played by different hands would otherwise
        sort arbitrarily, and regrouping the score by hand could reorder them,
        which makes `Score.notes` non-deterministic for no good reason.
        """
        return (
            self.start,
            self.pitch,
            self.end,
            self.velocity,
            self.hand.value,
            self.channel,
        )

    def __lt__(self, other: Note) -> bool:
        return self.sort_key < other.sort_key

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"note ends before it starts: {self.start} -> {self.end}")
        if not 0 <= self.pitch <= 127:
            raise ValueError(f"pitch out of MIDI range: {self.pitch}")
        if not 0 <= self.velocity <= 127:
            raise ValueError(f"velocity out of MIDI range: {self.velocity}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_black_key(self) -> bool:
        return is_black_key(self.pitch)

    @property
    def name(self) -> str:
        return pitch_name(self.pitch)

    @property
    def on_keyboard(self) -> bool:
        """Whether this pitch exists on an 88-key piano."""
        return LOWEST_KEY <= self.pitch <= HIGHEST_KEY

    @property
    def was_edited(self) -> bool:
        return self.provenance != (Provenance.ORIGINAL,)

    def sounds_at(self, time: float) -> bool:
        return self.start <= time < self.end

    def overlaps(
        self, other: Note, tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
    ) -> bool:
        """Whether both notes are held together for longer than ``tolerance``.

        The tolerance is what stops a note released a few milliseconds late from
        counting as a chord the player has to stretch for.
        """
        shared = min(self.end, other.end) - max(self.start, other.start)
        return shared > tolerance

    def with_provenance(self, step: Provenance) -> Note:
        if step in self.provenance:
            return self
        return replace(self, provenance=(*self.provenance, step))

    def octave_shifted(self, octaves: int) -> Note:
        """Move by whole octaves, recording that it happened.

        Whole octaves only, because that is the one displacement that preserves
        pitch class and harmonic function. The constraint engine never moves a
        note by any other interval.
        """
        if octaves == 0:
            return self
        moved = replace(self, pitch=self.pitch + 12 * octaves)
        return moved.with_provenance(Provenance.OCTAVE_SHIFTED)

    def shortened_to(self, end: float) -> Note:
        """End earlier, recording that it happened."""
        if end >= self.end:
            return self
        return replace(self, end=max(end, self.start)).with_provenance(
            Provenance.TRUNCATED
        )

    def assigned_to(self, hand: Hand) -> Note:
        if hand == self.hand:
            return self
        moved = replace(self, hand=hand)
        if self.hand == Hand.UNASSIGNED:
            return moved
        return moved.with_provenance(Provenance.REASSIGNED)


@dataclass(frozen=True, slots=True)
class PedalEvent:
    """One pedal press, from ``start`` until ``end``.

    ``depth`` is the raw controller value. Half-pedalling is real technique, so
    a press is not reduced to a boolean here.
    """

    pedal: Pedal
    start: float
    end: float
    depth: int = 127

    def __lt__(self, other: PedalEvent) -> bool:
        return (self.start, self.pedal) < (other.start, other.pedal)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"pedal ends before it starts: {self.start} -> {self.end}")
        if not 1 <= self.depth <= 127:
            raise ValueError(f"pedal depth out of range: {self.depth}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_full(self) -> bool:
        """Whether this would read as "down" under the MIDI on/off convention."""
        return self.depth >= 64

    def active_at(self, time: float) -> bool:
        return self.start <= time < self.end


@dataclass(frozen=True, slots=True)
class Part:
    """A stream of notes belonging to one player's one hand.

    A list of parts rather than a left/right pair, so duet mode is later config
    rather than a rewrite.
    """

    notes: tuple[Note, ...] = ()
    name: str = ""
    hand: Hand = Hand.UNASSIGNED
    source_track: int = 0

    def __iter__(self) -> Iterator[Note]:
        return iter(self.notes)

    def __len__(self) -> int:
        return len(self.notes)

    @property
    def is_empty(self) -> bool:
        return not self.notes

    def with_notes(self, notes: Iterable[Note]) -> Part:
        return replace(self, notes=tuple(sorted(notes)))


@dataclass(frozen=True, slots=True)
class Score:
    """A whole piece: parts, pedalling, and the timing they are measured against."""

    parts: tuple[Part, ...] = ()
    pedals: tuple[PedalEvent, ...] = ()
    tempo_map: TempoMap = field(default_factory=lambda: TempoMap.constant(480, 120.0))
    time_signatures: tuple[TimeSignature, ...] = ()
    source: Path | None = None
    title: str = ""
    #: Who wrote it, when the file says. MIDI has nowhere to put this; MusicXML
    #: does, and reading it rather than asking for it typed is the same argument
    #: that made MusicXML worth supporting at all.
    composer: str = ""

    # Derived values, computed once on first use. Not constructor arguments and
    # not part of equality or the repr: they carry no information the fields
    # above do not already carry.
    #
    # Memoising on a frozen dataclass is safe precisely because it is frozen.
    # Parts, notes, pedals and the tempo map are all immutable all the way down,
    # so neither of these can go stale, and `replace()` does not copy init=False
    # fields, so a derived score starts with an empty cache rather than the
    # wrong one. Two threads racing would compute the same answer twice and
    # store the same value, which matters because parallel frame rendering is
    # the next thing on the roadmap.
    _notes: tuple[Note, ...] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _meter: Meter | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def notes(self) -> tuple[Note, ...]:
        """Every note across every part, in playing order.

        Cached. The renderer asks once per frame, which is about 14,400 times
        for a four-minute 1080p60 render, and sorting several thousand notes
        into a fresh tuple that often was a fifth of the whole frame cost.
        """
        cached = self._notes
        if cached is None:
            cached = tuple(sorted(note for part in self.parts for note in part.notes))
            object.__setattr__(self, "_notes", cached)
        return cached

    @property
    def is_empty(self) -> bool:
        return all(part.is_empty for part in self.parts)

    @property
    def duration(self) -> float:
        """Seconds until the last sound stops, pedalling included."""
        ends = [note.end for part in self.parts for note in part.notes]
        ends += [pedal.end for pedal in self.pedals]
        return max(ends, default=0.0)

    @property
    def meter(self) -> Meter:
        """Where the bar lines fall, from the tempo map and the time signatures.

        Cached, like `notes`. Cheap to build, but the renderer asks for it once
        per frame when the grid is drawing bar lines, and a value that cannot
        change is not worth rebuilding fourteen thousand times.
        """
        cached = self._meter
        if cached is None:
            cached = Meter.from_score_data(self.tempo_map, self.time_signatures)
            object.__setattr__(self, "_meter", cached)
        return cached

    @property
    def pitch_range(self) -> tuple[int, int] | None:
        pitches = [note.pitch for part in self.parts for note in part.notes]
        return (min(pitches), max(pitches)) if pitches else None

    def notes_between(self, start: float, end: float) -> tuple[Note, ...]:
        """Notes sounding at any point in ``[start, end)``.

        The renderer only ever draws a window of the piece, so this is how it
        avoids walking every note on every frame.
        """
        return tuple(
            note for note in self.notes if note.start < end and note.end > start
        )

    def sounding_at(self, time: float) -> tuple[Note, ...]:
        return tuple(note for note in self.notes if note.sounds_at(time))

    def pedal_at(self, time: float, pedal: Pedal = Pedal.SUSTAIN) -> PedalEvent | None:
        for event in self.pedals:
            if event.pedal == pedal and event.active_at(time):
                return event
        return None

    def with_parts(self, parts: Sequence[Part]) -> Score:
        return replace(self, parts=tuple(parts))

    def with_notes(self, notes: Iterable[Note]) -> Score:
        """Rebuild the parts from a flat note list, grouped by hand.

        Used by stages that move notes between hands and do not care about the
        original track layout.
        """
        by_hand: dict[Hand, list[Note]] = {}
        for note in notes:
            by_hand.setdefault(note.hand, []).append(note)
        parts = tuple(
            Part(notes=tuple(sorted(group)), name=hand.value, hand=hand)
            for hand, group in sorted(by_hand.items(), key=lambda item: item[0].value)
        )
        return replace(self, parts=parts)
