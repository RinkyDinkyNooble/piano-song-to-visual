"""Turn a Score back into MIDI.

Every intermediate in the pipeline can be written out, opened in a MIDI editor,
fixed by hand, and fed back in. That is the escape hatch for when the arrange
stage guesses wrong, and it is why hand-editing is a supported workflow rather
than a sign of failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import mido

from psv.model import Note, Score
from psv.tempo import TempoMap

log = logging.getLogger(__name__)


def write_midi_file(score: Score, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    score_to_midi(score).save(path)
    log.info("wrote %s", path)
    return path


def score_to_midi(score: Score) -> mido.MidiFile:
    """Build a type 1 MidiFile from a Score.

    Track 0 carries tempo and meter only; each part becomes its own track, so a
    round trip preserves the part layout.
    """
    tempo_map = score.tempo_map
    midi = mido.MidiFile(type=1, ticks_per_beat=tempo_map.ticks_per_beat)
    midi.tracks.append(_meta_track(score))

    for part in score.parts:
        midi.tracks.append(_note_track(part.notes, part.name, tempo_map))

    if score.pedals:
        midi.tracks.append(_pedal_track(score, tempo_map))

    return midi


def _meta_track(score: Score) -> mido.MidiTrack:
    events: list[tuple[int, mido.MetaMessage]] = []
    if score.title:
        events.append((0, mido.MetaMessage("track_name", name=score.title)))
    for change in score.tempo_map.changes:
        events.append(
            (change.tick, mido.MetaMessage("set_tempo", tempo=change.us_per_beat))
        )
    for signature in score.time_signatures:
        events.append(
            (
                signature.tick,
                mido.MetaMessage(
                    "time_signature",
                    numerator=signature.numerator,
                    denominator=signature.denominator,
                ),
            )
        )
    return _to_track(events)


def _note_track(
    notes: tuple[Note, ...], name: str, tempo_map: TempoMap
) -> mido.MidiTrack:
    events: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    if name:
        events.append((0, mido.MetaMessage("track_name", name=name)))
    for note in notes:
        channel = min(note.channel, 15)
        events.append(
            (
                tempo_map.seconds_to_tick(note.start),
                mido.Message(
                    "note_on",
                    note=note.pitch,
                    velocity=note.velocity,
                    channel=channel,
                ),
            )
        )
        events.append(
            (
                tempo_map.seconds_to_tick(note.end),
                mido.Message("note_off", note=note.pitch, velocity=0, channel=channel),
            )
        )
    return _to_track(events)


def _pedal_track(score: Score, tempo_map: TempoMap) -> mido.MidiTrack:
    events: list[tuple[int, mido.Message | mido.MetaMessage]] = [
        (0, mido.MetaMessage("track_name", name="pedals"))
    ]
    for pedal in score.pedals:
        events.append(
            (
                tempo_map.seconds_to_tick(pedal.start),
                mido.Message(
                    "control_change", control=int(pedal.pedal), value=pedal.depth
                ),
            )
        )
        events.append(
            (
                tempo_map.seconds_to_tick(pedal.end),
                mido.Message("control_change", control=int(pedal.pedal), value=0),
            )
        )
    return _to_track(events)


def _to_track(
    events: list[tuple[int, mido.Message | mido.MetaMessage]]
    | list[tuple[int, mido.MetaMessage]],
) -> mido.MidiTrack:
    """Sort absolute-tick events and convert them to delta times.

    Note-offs sort before note-ons at the same tick, so a note that ends exactly
    where the next one of the same pitch begins does not cancel it.
    """

    def key(item: tuple[int, mido.Message | mido.MetaMessage]) -> tuple[int, int]:
        tick, message = item
        if message.type in {"note_off", "control_change"}:
            rank = 0
        elif message.type == "note_on":
            rank = 2
        else:
            rank = 1
        return (tick, rank)

    track = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(events, key=key):
        track.append(message.copy(time=tick - previous))
        previous = tick
    return track
