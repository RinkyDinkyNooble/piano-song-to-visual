"""The inspect report.

This is what tells you, before running anything else, whether a file needs the
arrange stage and whether it carries the dynamics and pedal data the visuals
depend on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import mido
import pytest

from psv.inspect import format_report, inspect_score
from psv.midi import read_midi
from psv.model import Pedal
from tests.fixtures.midi_builder import FIXTURES


def report_for(name: str) -> object:
    return inspect_score(read_midi(FIXTURES[name]()))


@pytest.mark.feature("F-08")
def test_an_empty_score_reports_zeroes_without_crashing() -> None:
    report = inspect_score(read_midi(FIXTURES["empty"]()))
    assert report.note_count == 0
    assert report.pitch_low is None
    assert report.peak_polyphony == 0
    assert report.widest_span == 0
    assert "0 in 0 part(s)" in format_report(report)


@pytest.mark.feature("F-08")
def test_polyphony_counts_notes_held_together() -> None:
    """dynamic-levels is three-note chords, one at a time."""
    report = inspect_score(read_midi(FIXTURES["dynamic-levels"]()))
    assert report.peak_polyphony == 3


@pytest.mark.feature("F-08")
def test_a_single_line_has_polyphony_of_one() -> None:
    report = inspect_score(read_midi(FIXTURES["full-keyboard"]()))
    assert report.peak_polyphony == 1


@pytest.mark.feature("F-08")
def test_the_widest_span_is_found_and_located() -> None:
    """wide-span-chord is pitches 36 to 73 sounding together."""
    report = inspect_score(read_midi(FIXTURES["wide-span-chord"]()))
    assert report.widest_span == 73 - 36
    assert report.widest_span_time == pytest.approx(0.0)


@pytest.mark.feature("F-08")
def test_a_tiny_overlap_does_not_count_toward_the_widest_span() -> None:
    """tiny-overlap has a 10ms brush at a 36-semitone spread, then a real one.

    Both pairs span the same interval, so if the tolerance were ignored the
    reported time would be the first pair rather than the second.
    """
    report = inspect_score(read_midi(FIXTURES["tiny-overlap"]()))
    assert report.widest_span == 84 - 48
    assert report.widest_span_time == pytest.approx(2.25, abs=0.05)


@pytest.mark.feature("F-08")
def test_uniform_velocity_is_reported_as_no_dynamics() -> None:
    report = inspect_score(read_midi(FIXTURES["full-keyboard"]()))
    assert report.distinct_velocities == 1
    assert not report.has_dynamics
    assert "none (every note the same velocity)" in format_report(report)


@pytest.mark.feature("F-08")
def test_a_velocity_ramp_is_reported_as_having_dynamics() -> None:
    report = inspect_score(read_midi(FIXTURES["velocity-ramp"]()))
    assert report.distinct_velocities == 127
    assert report.has_dynamics


@pytest.mark.feature("F-08")
def test_pedal_presence_and_partial_depths_are_reported() -> None:
    report = inspect_score(read_midi(FIXTURES["half-pedal"]()))
    assert report.has_pedal
    assert report.has_partial_pedalling
    assert "partial depths" in format_report(report)


@pytest.mark.feature("F-08")
def test_pedals_are_counted_per_pedal() -> None:
    report = inspect_score(read_midi(FIXTURES["three-pedals"]()))
    assert set(report.pedal_counts) == {Pedal.SUSTAIN, Pedal.SOSTENUTO, Pedal.SOFT}


@pytest.mark.feature("F-08")
def test_a_file_with_no_pedal_says_so() -> None:
    report = inspect_score(read_midi(FIXTURES["single-note"]()))
    assert not report.has_pedal
    assert "pedal          none" in format_report(report)


@pytest.mark.feature("F-08")
def test_separated_hands_are_recognised() -> None:
    report = inspect_score(read_midi(FIXTURES["two-hands"]()))
    assert report.looks_pre_separated
    assert "look already separated" in format_report(report)


@pytest.mark.feature("F-08")
def test_four_instrument_parts_are_not_mistaken_for_two_hands() -> None:
    report = inspect_score(read_midi(FIXTURES["orchestral"]()))
    assert not report.looks_pre_separated
    assert "needs the arrange stage" in format_report(report)


@pytest.mark.feature("F-08")
def test_crossing_voices_are_not_mistaken_for_separated_hands() -> None:
    """Two parts, but their registers overlap completely, so a register split
    would be wrong."""
    report = inspect_score(read_midi(FIXTURES["voice-crossing"]()))
    assert not report.looks_pre_separated


@pytest.mark.feature("F-08")
def test_notes_outside_the_88_keys_are_counted_and_explained() -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.Message("note_on", note=12, velocity=64, time=0),
                mido.Message("note_off", note=12, velocity=0, time=480),
            ]
        )
    )
    report = inspect_score(read_midi(midi))
    assert report.off_keyboard_notes == 1
    text = format_report(report)
    assert "1 outside the 88 keys" in text
    assert "cannot be played on an 88-key piano" in text


@pytest.mark.feature("F-08")
def test_tempo_and_meter_appear_in_the_report() -> None:
    text = format_report(inspect_score(read_midi(FIXTURES["tempo-changes"]())))
    assert "60 to 180 BPM, 4 changes" in text

    steady = format_report(inspect_score(read_midi(FIXTURES["single-note"]())))
    assert "120 BPM, constant" in steady


@pytest.mark.feature("F-08")
def test_verbose_adds_a_per_track_breakdown() -> None:
    report = inspect_score(read_midi(FIXTURES["orchestral"]()))
    plain = format_report(report, verbose=False)
    detailed = format_report(report, verbose=True)
    assert len(detailed) > len(plain)
    assert detailed.count("notes") > plain.count("notes")


@pytest.mark.feature("F-08")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
def test_the_report_agrees_with_the_song_manifest(
    song_id: str,
    songs: dict[str, dict[str, Any]],
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    """The manifest records what is in each file. If parsing and the manifest
    disagree, one of them is wrong and this says so."""
    expected = songs[song_id]
    report = inspect_score(read_midi(load_song(song_id)))

    assert report.note_count == expected["notes"]
    assert [report.pitch_low, report.pitch_high] == expected["pitch_range"]
    assert report.duration_s == pytest.approx(float(expected["duration_s"]), abs=1.0)
    # Both are LilyPond exports, which is why the fixtures exist.
    assert not report.has_dynamics
    assert not report.has_pedal


@pytest.mark.feature("F-08")
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_produces_a_printable_report(name: str) -> None:
    report = inspect_score(read_midi(FIXTURES[name]()))
    text = format_report(report, verbose=True)
    assert text.strip()
    assert "duration" in text
