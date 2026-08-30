"""Turn a MIDI file into a Score.

The awkward parts of MIDI all live here, so nothing downstream has to know
about them:

* a note-off is often written as note-on with velocity 0
* the same pitch can be struck again before its first note-off arrives
* channel 9 is percussion and has no pitch
* tempo and meta events may sit on any track, not only the first
* a pedal is a continuous controller, not a switch
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import mido

from psv.model import Hand, Note, Part, Pedal, PedalEvent, Score
from psv.tempo import TempoMap, TimeSignature

log = logging.getLogger(__name__)

#: MIDI reserves channel 9 for percussion, where note numbers pick a drum
#: rather than a pitch. Nothing on it belongs on a piano keyboard.
DRUM_CHANNEL = 9

#: Any controller value at or above this counts as the pedal being engaged.
#: Deliberately 1, not the conventional 64: this is a tool for *seeing* what a
#: performance did, and half-pedalling is worth seeing. Raise it in config to
#: get the on/off reading instead.
DEFAULT_PEDAL_THRESHOLD = 1

_PEDAL_CONTROLLERS = {int(pedal): pedal for pedal in Pedal}


class MidiReadError(ValueError):
    """The file could not be understood as MIDI."""


@dataclass(frozen=True, slots=True)
class _OpenNote:
    tick: int
    velocity: int


def read_midi_file(path: Path | str) -> Score:
    """Read a Score from a path.

    Raises :class:`MidiReadError` for anything unreadable, so callers do not
    have to know which of mido's exceptions mean what.
    """
    path = Path(path)
    try:
        midi = mido.MidiFile(path)
    except (OSError, EOFError, ValueError, IndexError) as exc:
        raise MidiReadError(f"could not read {path}: {exc}") from exc
    return read_midi(midi, source=path, title=path.stem)


def read_midi(
    midi: mido.MidiFile,
    *,
    source: Path | None = None,
    title: str = "",
    pedal_threshold: int = DEFAULT_PEDAL_THRESHOLD,
) -> Score:
    """Convert an already-open MidiFile into a Score."""
    if midi.ticks_per_beat <= 0:
        raise MidiReadError(f"invalid ticks_per_beat: {midi.ticks_per_beat}")

    tempo_map = _read_tempo_map(midi)
    time_signatures = _read_time_signatures(midi, tempo_map)

    parts: list[Part] = []
    pedals: list[PedalEvent] = []
    for index, track in enumerate(midi.tracks):
        notes = _read_notes(track, index, tempo_map)
        pedals.extend(_read_pedals(track, tempo_map, pedal_threshold))
        if notes:
            parts.append(
                Part(
                    notes=tuple(sorted(notes)),
                    name=_track_name(track) or f"track {index}",
                    hand=Hand.UNASSIGNED,
                    source_track=index,
                )
            )

    log.info(
        "read %s: %d part(s), %d note(s), %d pedal event(s)",
        source or "<memory>",
        len(parts),
        sum(len(part) for part in parts),
        len(pedals),
    )
    return Score(
        parts=tuple(parts),
        pedals=tuple(sorted(pedals)),
        tempo_map=tempo_map,
        time_signatures=time_signatures,
        source=source,
        title=title,
    )


# -- meta ----------------------------------------------------------------


def _absolute(track: mido.MidiTrack) -> Iterator[tuple[int, mido.Message]]:
    """Walk a track yielding absolute ticks instead of deltas."""
    tick = 0
    for message in track:
        tick += message.time
        yield tick, message


def _read_tempo_map(midi: mido.MidiFile) -> TempoMap:
    """Collect set_tempo events from every track, not just the first.

    Type 0 files put them inline with the notes, and plenty of type 1 files put
    them somewhere other than track 0.
    """
    changes: list[tuple[int, int]] = []
    for track in midi.tracks:
        for tick, message in _absolute(track):
            if message.type == "set_tempo":
                changes.append((tick, message.tempo))
    return TempoMap.from_changes(midi.ticks_per_beat, changes)


def _read_time_signatures(
    midi: mido.MidiFile, tempo_map: TempoMap
) -> tuple[TimeSignature, ...]:
    found: dict[int, TimeSignature] = {}
    for track in midi.tracks:
        for tick, message in _absolute(track):
            if message.type == "time_signature":
                found[tick] = TimeSignature(
                    tick=tick,
                    seconds=tempo_map.tick_to_seconds(tick),
                    numerator=message.numerator,
                    denominator=message.denominator,
                )
    if not found:
        return (TimeSignature(0, 0.0, 4, 4),)
    return tuple(found[tick] for tick in sorted(found))


def _track_name(track: mido.MidiTrack) -> str:
    for message in track:
        if message.type == "track_name":
            name: str = message.name.strip()
            return name
    return ""


# -- notes ---------------------------------------------------------------


def _read_notes(
    track: mido.MidiTrack, track_index: int, tempo_map: TempoMap
) -> list[Note]:
    open_notes: dict[tuple[int, int], _OpenNote] = {}
    notes: list[Note] = []

    def close(key: tuple[int, int], tick: int) -> None:
        opened = open_notes.pop(key, None)
        if opened is None:
            return
        channel, pitch = key
        notes.append(
            Note(
                pitch=pitch,
                start=tempo_map.tick_to_seconds(opened.tick),
                end=tempo_map.tick_to_seconds(tick),
                velocity=opened.velocity,
                source_track=track_index,
                channel=channel,
            )
        )

    for tick, message in _absolute(track):
        if message.type not in {"note_on", "note_off"}:
            continue
        if message.channel == DRUM_CHANNEL:
            continue

        key = (message.channel, message.note)
        is_note_on = message.type == "note_on" and message.velocity > 0
        if is_note_on:
            # A pitch struck again before its note-off ends the first one here,
            # rather than leaving it hanging or dropping the second strike.
            if key in open_notes:
                close(key, tick)
            open_notes[key] = _OpenNote(tick, message.velocity)
        else:
            close(key, tick)

    for key in list(open_notes):
        # A note still held at end of track: end it there rather than discard it.
        log.debug(
            "track %d: note %s never released, ending at track end", track_index, key
        )
        close(key, _last_tick(track))

    return notes


def _last_tick(track: mido.MidiTrack) -> int:
    return sum(message.time for message in track)


# -- pedals --------------------------------------------------------------


def _read_pedals(
    track: mido.MidiTrack, tempo_map: TempoMap, threshold: int
) -> list[PedalEvent]:
    """Turn continuous controller traffic into discrete presses.

    A change of depth while the pedal is still down closes the current event and
    opens a new one, so a gradual release shows up as distinct segments rather
    than being flattened to whatever value happened to come first.
    """
    events: list[PedalEvent] = []
    open_at: dict[Pedal, tuple[int, int]] = {}

    for tick, message in _absolute(track):
        if message.type != "control_change":
            continue
        pedal = _PEDAL_CONTROLLERS.get(message.control)
        if pedal is None:
            continue

        engaged = message.value >= threshold
        current = open_at.get(pedal)

        if current is not None and (not engaged or current[1] != message.value):
            start_tick, depth = current
            if tick > start_tick:
                events.append(
                    PedalEvent(
                        pedal=pedal,
                        start=tempo_map.tick_to_seconds(start_tick),
                        end=tempo_map.tick_to_seconds(tick),
                        depth=depth,
                    )
                )
            del open_at[pedal]

        if engaged and pedal not in open_at:
            open_at[pedal] = (tick, message.value)

    end_tick = _last_tick(track)
    for pedal, (start_tick, depth) in open_at.items():
        if end_tick > start_tick:
            events.append(
                PedalEvent(
                    pedal=pedal,
                    start=tempo_map.tick_to_seconds(start_tick),
                    end=tempo_map.tick_to_seconds(end_tick),
                    depth=depth,
                )
            )
    return events
