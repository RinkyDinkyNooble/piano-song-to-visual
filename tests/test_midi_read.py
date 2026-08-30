"""MIDI ingest, driven by the synthetic fixtures and the committed songs.

The fixtures exist because engraved scores cannot exercise dynamics, pedalling,
or the parser's awkward corners. Each test below names the fixture built for it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import mido
import pytest

from psv.midi import read_midi, read_midi_file, score_to_midi, write_midi_file
from psv.midi.read import DRUM_CHANNEL, MidiReadError
from psv.model import Pedal
from tests.fixtures.midi_builder import FIXTURES


def load(name: str) -> object:
    return read_midi(FIXTURES[name]())


# -- the basics ----------------------------------------------------------


@pytest.mark.feature("F-01")
def test_a_single_note_survives_intact() -> None:
    score = read_midi(FIXTURES["single-note"]())
    assert len(score.notes) == 1
    note = score.notes[0]
    assert note.pitch == 60
    assert note.start == pytest.approx(0.0)
    assert note.end == pytest.approx(0.5)  # one beat at the default 120 BPM
    assert note.velocity == 64


@pytest.mark.feature("F-50")
def test_an_empty_file_parses_to_an_empty_score() -> None:
    score = read_midi(FIXTURES["empty"]())
    assert score.is_empty
    assert score.notes == ()
    assert score.duration == 0.0


@pytest.mark.feature("F-01")
def test_every_key_of_an_88_key_piano_round_trips() -> None:
    score = read_midi(FIXTURES["full-keyboard"]())
    assert len(score.notes) == 88
    assert score.pitch_range == (21, 108)
    assert all(note.on_keyboard for note in score.notes)


@pytest.mark.feature("F-01")
def test_parts_keep_their_track_index_and_name() -> None:
    score = read_midi(FIXTURES["two-hands"]())
    assert [part.name for part in score.parts] == ["right", "left"]
    assert [part.source_track for part in score.parts] == [0, 1]


# -- the awkward corners of MIDI -----------------------------------------


@pytest.mark.feature("F-02")
def test_note_on_with_velocity_zero_ends_a_note() -> None:
    """Legal MIDI and common. A parser watching only for note_off sees nothing
    end, and every note runs to the end of the track."""
    score = read_midi(FIXTURES["zero-velocity-note-off"]())
    assert len(score.notes) == 4
    for note in score.notes:
        assert note.duration == pytest.approx(0.45)  # 0.9 beats at 120 BPM


@pytest.mark.feature("F-03")
def test_a_pitch_struck_again_closes_the_first_note() -> None:
    """Neither note is dropped, and neither is left hanging."""
    score = read_midi(FIXTURES["retriggered-pitch"]())
    assert len(score.notes) == 2
    first, second = score.notes
    assert first.start == pytest.approx(0.0)
    assert first.end == pytest.approx(0.5)  # cut short by the second strike
    assert second.start == pytest.approx(0.5)
    assert second.velocity == 90
    assert first.end <= second.start


@pytest.mark.feature("F-04")
def test_percussion_channel_is_excluded() -> None:
    """Channel 9 note numbers pick a drum, not a pitch."""
    score = read_midi(FIXTURES["drum-channel"]())
    assert len(score.notes) == 4
    assert all(note.channel != DRUM_CHANNEL for note in score.notes)
    assert {note.pitch for note in score.notes} == {60}


def test_a_note_left_hanging_is_ended_at_the_track_end() -> None:
    track = mido.MidiTrack([mido.Message("note_on", note=60, velocity=64, time=0)])
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(track)
    score = read_midi(midi)
    assert len(score.notes) == 1
    assert score.notes[0].duration >= 0.0


# -- tempo and meter -----------------------------------------------------


@pytest.mark.feature("F-05")
def test_tempo_changes_are_resolved_to_seconds() -> None:
    score = read_midi(FIXTURES["tempo-changes"]())
    assert len(score.tempo_map.changes) == 4
    bpms = [round(change.bpm) for change in score.tempo_map.changes]
    assert bpms == [60, 120, 90, 180]
    # Beats 0-3 at 60 BPM take a second each, so beat 4 lands at 4.0s.
    assert score.notes[4].start == pytest.approx(4.0)


@pytest.mark.feature("F-06")
def test_time_signature_changes_are_kept_in_order() -> None:
    score = read_midi(FIXTURES["time-signatures"]())
    assert [(s.numerator, s.denominator) for s in score.time_signatures] == [
        (4, 4),
        (3, 4),
        (7, 8),
    ]


@pytest.mark.feature("F-06")
def test_a_file_without_a_time_signature_gets_four_four() -> None:
    score = read_midi(FIXTURES["single-note"]())
    assert [(s.numerator, s.denominator) for s in score.time_signatures] == [(4, 4)]


def test_tempo_events_are_found_on_any_track() -> None:
    """Type 0 files put tempo inline with notes, and type 1 files do not always
    put it on track 0."""
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack())
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("set_tempo", tempo=1_000_000, time=0),
                mido.Message("note_on", note=60, velocity=64, time=0),
                mido.Message("note_off", note=60, velocity=0, time=480),
            ]
        )
    )
    score = read_midi(midi)
    assert score.notes[0].duration == pytest.approx(1.0)


# -- pedals --------------------------------------------------------------


@pytest.mark.feature("F-07")
def test_sustain_pedal_presses_become_events() -> None:
    score = read_midi(FIXTURES["sustain-pedal"]())
    sustain = [e for e in score.pedals if e.pedal is Pedal.SUSTAIN]
    assert len(sustain) == 1
    assert sustain[0].start == pytest.approx(0.0)
    assert sustain[0].end == pytest.approx(2.0)  # 4 beats at 120 BPM
    assert sustain[0].depth == 127


@pytest.mark.feature("F-07")
def test_partial_pedal_depths_are_preserved() -> None:
    """Treating CC64 as a boolean would read all five of these identically."""
    score = read_midi(FIXTURES["half-pedal"]())
    depths = [event.depth for event in score.pedals]
    assert depths == [20, 50, 80, 110, 127]
    assert any(not event.is_full for event in score.pedals)


@pytest.mark.feature("F-07")
def test_all_three_pedals_are_read_independently() -> None:
    score = read_midi(FIXTURES["three-pedals"]())
    found = {event.pedal for event in score.pedals}
    assert found == {Pedal.SUSTAIN, Pedal.SOSTENUTO, Pedal.SOFT}


@pytest.mark.feature("F-07")
def test_raising_the_threshold_gives_the_on_off_reading() -> None:
    """The default shows half-pedalling; 64 is the MIDI convention."""
    midi = FIXTURES["half-pedal"]()
    lenient = read_midi(midi, pedal_threshold=1)
    strict = read_midi(midi, pedal_threshold=64)
    assert len(lenient.pedals) == 5
    assert [e.depth for e in strict.pedals] == [80, 110, 127]


def test_pedal_state_at_a_moment_is_queryable() -> None:
    score = read_midi(FIXTURES["sustain-pedal"]())
    assert score.pedal_at(1.0) is not None
    assert score.pedal_at(3.0) is None


# -- errors --------------------------------------------------------------


def test_a_missing_file_raises_a_read_error(tmp_path: Path) -> None:
    with pytest.raises(MidiReadError, match="could not read"):
        read_midi_file(tmp_path / "nope.mid")


def test_a_file_that_is_not_midi_raises_a_read_error(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mid"
    bogus.write_bytes(b"this is not a MIDI file at all")
    with pytest.raises(MidiReadError):
        read_midi_file(bogus)


# -- round trip ----------------------------------------------------------


@pytest.mark.feature("F-09")
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_survives_a_round_trip(name: str, tmp_path: Path) -> None:
    """Score -> MIDI -> Score must preserve pitches, timing, and velocity.

    This is the tightest feedback loop in the parser: any ingest asymmetry shows
    up here as a diff rather than as a mysterious render later.
    """
    original = read_midi(FIXTURES[name]())
    path = write_midi_file(original, tmp_path / f"{name}.mid")
    reloaded = read_midi_file(path)

    assert len(reloaded.notes) == len(original.notes)
    for before, after in zip(original.notes, reloaded.notes, strict=True):
        assert after.pitch == before.pitch
        assert after.velocity == before.velocity
        assert after.start == pytest.approx(before.start, abs=0.005)
        assert after.end == pytest.approx(before.end, abs=0.005)

    assert len(reloaded.pedals) == len(original.pedals)
    for pedal_before, pedal_after in zip(original.pedals, reloaded.pedals, strict=True):
        assert pedal_after.pedal == pedal_before.pedal
        assert pedal_after.depth == pedal_before.depth
        assert pedal_after.start == pytest.approx(pedal_before.start, abs=0.005)


@pytest.mark.feature("F-09")
def test_round_trip_preserves_the_part_layout() -> None:
    original = read_midi(FIXTURES["orchestral"]())
    reloaded = read_midi(score_to_midi(original))
    assert len(reloaded.parts) == len(original.parts) == 4


# -- real songs ----------------------------------------------------------


@pytest.mark.feature("F-01")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
def test_committed_songs_match_their_recorded_shape(
    song_id: str,
    songs: dict[str, dict[str, Any]],
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    """The manifest records what each file contains; parsing must agree."""
    expected = songs[song_id]
    score = read_midi(load_song(song_id))

    assert len(score.notes) == expected["notes"]
    assert list(score.pitch_range or ()) == expected["pitch_range"]
    assert score.duration == pytest.approx(float(expected["duration_s"]), abs=1.0)


@pytest.mark.feature("F-09")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
def test_real_songs_survive_a_round_trip(
    song_id: str,
    load_song: Callable[[str], mido.MidiFile],
    tmp_path: Path,
) -> None:
    original = read_midi(load_song(song_id))
    reloaded = read_midi_file(write_midi_file(original, tmp_path / "out.mid"))
    assert len(reloaded.notes) == len(original.notes)
    assert reloaded.pitch_range == original.pitch_range
