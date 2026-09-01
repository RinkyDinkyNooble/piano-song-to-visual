"""The arrangement stage, and the whole pipeline it completes.

This is the fuzziest stage, so the tests check properties rather than exact
output: nothing is invented, already-arranged music is left alone, crossing
voices survive, and the result is something the constraint engine can finish
turning into a playable score.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import mido
import pytest

from psv.arrange import arrange, assign_hands, looks_arranged, reduce_texture
from psv.config import Config, VisualConfig
from psv.constraints import constrain, verify_span
from psv.midi import read_midi
from psv.model import Hand, Note, Score
from psv.pipeline import run
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import video_meta


def notes_of(*specs: tuple[int, float, float]) -> list[Note]:
    return [
        Note(pitch=pitch, start=start, end=end, velocity=64)
        for pitch, start, end in specs
    ]


# -- leaving well alone --------------------------------------------------


@pytest.mark.feature("F-43")
def test_a_score_that_already_has_two_hands_is_untouched() -> None:
    """A piano MIDI that arrived with its hands separated is the arrangement.
    Re-deriving it could only make it worse."""
    score = read_midi(FIXTURES["two-hands"]())
    assert looks_arranged(score)

    result = arrange(score)
    assert result.was_already_arranged
    assert result.score.notes == score.notes
    assert result.dropped == ()
    assert "left alone" in result.summary()


@pytest.mark.feature("F-43")
def test_an_empty_score_is_handled() -> None:
    result = arrange(Score())
    assert result.score.is_empty
    assert result.dropped == ()


@pytest.mark.feature("F-43")
def test_arranging_twice_changes_nothing_the_second_time() -> None:
    once = arrange(read_midi(FIXTURES["orchestral"]())).score
    twice = arrange(once)
    assert twice.was_already_arranged
    assert twice.score.notes == once.notes


# -- reduction -----------------------------------------------------------


@pytest.mark.feature("F-46")
def test_reduction_caps_how_many_notes_sound_at_once() -> None:
    notes = notes_of(*[(p, 0.0, 1.0) for p in (48, 52, 55, 59, 62, 65, 69, 72, 76)])
    kept, dropped = reduce_texture(notes, max_voices=4)
    assert len(kept) == 4
    assert len(dropped) == 5


@pytest.mark.feature("F-46")
def test_reduction_keeps_the_outer_voices() -> None:
    """Melody and bass carry the piece; the harmony between them is what a
    listener misses least."""
    notes = notes_of(*[(p, 0.0, 1.0) for p in (48, 55, 60, 64, 67, 84)])
    kept, _ = reduce_texture(notes, max_voices=2)
    assert sorted(n.pitch for n in kept) == [48, 84]


@pytest.mark.feature("F-46")
def test_reduction_leaves_a_thin_texture_alone() -> None:
    notes = notes_of((60, 0.0, 1.0), (64, 0.0, 1.0))
    kept, dropped = reduce_texture(notes, max_voices=8)
    assert dropped == []
    assert kept == notes


@pytest.mark.feature("F-46")
def test_notes_that_never_overlap_are_never_reduced() -> None:
    """Density is about what sounds together, not how many notes there are."""
    notes = notes_of(*[(60 + i, i * 1.0, i * 1.0 + 0.5) for i in range(10)])
    kept, dropped = reduce_texture(notes, max_voices=2)
    assert dropped == []
    assert len(kept) == 10


def test_a_nonpositive_voice_cap_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_voices must be positive"):
        reduce_texture([], max_voices=0)


# -- hand assignment -----------------------------------------------------


@pytest.mark.feature("F-44")
def test_every_note_gets_a_hand() -> None:
    notes = list(read_midi(FIXTURES["orchestral"]()).notes)
    handed = assign_hands(notes, max_span=12)
    assert len(handed) == len(notes)
    assert all(note.hand in (Hand.LEFT, Hand.RIGHT) for note in handed)


@pytest.mark.feature("F-44")
def test_a_wide_chord_is_split_between_the_hands() -> None:
    notes = notes_of((36, 0.0, 1.0), (40, 0.0, 1.0), (72, 0.0, 1.0), (76, 0.0, 1.0))
    handed = assign_hands(notes, max_span=12)
    hands = {note.pitch: note.hand for note in handed}
    assert hands[36] is Hand.LEFT
    assert hands[40] is Hand.LEFT
    assert hands[72] is Hand.RIGHT
    assert hands[76] is Hand.RIGHT


@pytest.mark.feature("F-45")
def test_the_split_follows_the_music_when_voices_cross() -> None:
    """A fixed split point gets this wrong by construction: the two voices swap
    register halfway, so any single pitch threshold mislabels one of them."""
    score = read_midi(FIXTURES["voice-crossing"]())
    handed = assign_hands(list(score.notes), max_span=12)

    # The fixture is eight beats at 120 BPM: four seconds. The voices start an
    # octave apart, meet in the middle, and finish swapped.
    for time in (0.1, 1.6, 3.6):
        sounding = [n for n in handed if n.sounds_at(time)]
        assert len(sounding) == 2, f"expected two voices at {time}s"
        assert {n.hand for n in sounding} == {Hand.LEFT, Hand.RIGHT}, (
            "two simultaneous notes should be one per hand"
        )
        low, high = sorted(sounding, key=lambda n: n.pitch)
        assert low.hand is Hand.LEFT and high.hand is Hand.RIGHT


@pytest.mark.feature("F-45")
def test_a_held_note_is_never_moved_between_hands_mid_way() -> None:
    handed = assign_hands(
        notes_of((40, 0.0, 8.0), (80, 1.0, 2.0), (44, 3.0, 4.0)), max_span=12
    )
    long_note = next(n for n in handed if n.pitch == 40)
    assert long_note.hand is Hand.LEFT


@pytest.mark.feature("F-44")
def test_assignment_of_nothing_is_nothing() -> None:
    assert assign_hands([], max_span=12) == []


# -- the whole stage -----------------------------------------------------


@pytest.mark.feature("F-44")
def test_four_instruments_become_two_hands() -> None:
    score = read_midi(FIXTURES["orchestral"]())
    assert len({n.source_track for n in score.notes}) == 4

    result = arrange(score, max_span=12)
    assert not result.was_already_arranged
    assert {n.hand for n in result.score.notes} == {Hand.LEFT, Hand.RIGHT}


@pytest.mark.feature("F-44")
def test_arranging_never_invents_notes() -> None:
    score = read_midi(FIXTURES["orchestral"]())
    result = arrange(score, max_span=12)
    assert len(result.score.notes) + len(result.dropped) == len(score.notes)
    for note in result.score.notes:
        assert any(
            note.pitch == original.pitch and note.start == original.start
            for original in score.notes
        )


@pytest.mark.feature("F-44")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
def test_a_real_multi_instrument_score_arranges_and_constrains(
    song_id: str, load_song: Callable[[str], mido.MidiFile]
) -> None:
    """The pair together are the actual claim: arrange makes it two hands, and
    constrain makes those two hands reachable."""
    config = Config()
    score = read_midi(load_song(song_id))

    arranged = arrange(score, max_span=config.hands.max_span_semitones)
    constrained = constrain(arranged.score, config)

    assert verify_span(constrained.score, config.hands.max_span_semitones) == []
    kept = len(constrained.score.notes) / len(score.notes)
    assert kept > 0.9, f"only {kept:.0%} of the music survived"


@pytest.mark.feature("F-44")
def test_arranging_first_leaves_the_constraint_engine_less_to_do(
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    """The point of the stage. A moving split beats the placeholder register
    split the constraint engine falls back on."""
    from psv.constraints.hands import assign_by_register

    config = Config()
    score = read_midi(load_song("quartet"))

    naive = constrain(assign_by_register(score), config)
    arranged = constrain(arrange(score, max_span=12).score, config)

    assert arranged.violations_before < naive.violations_before
    assert len(arranged.score.notes) > len(naive.score.notes)


# -- the full pipeline ---------------------------------------------------


@pytest.mark.feature("F-48")
def test_run_produces_a_video_with_sound(tmp_path: Path) -> None:
    config = Config()
    config = type(config)(
        hands=config.hands,
        difficulty=config.difficulty,
        visual=VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0),
        pedals=config.pedals,
        audio=config.audio,
    )

    source = tmp_path / "in.mid"
    FIXTURES["orchestral"]().save(source)
    output = tmp_path / "out.mp4"

    result = run(source, output, config, duration=2.0)

    assert output.exists()
    assert result.output == output
    assert verify_span(result.score, config.hands.max_span_semitones) == []
    assert not result.audio.is_silent

    meta = video_meta(output)
    assert meta["size"] == (160, 120)
    assert meta["duration"] == pytest.approx(2.0, abs=0.15)


@pytest.mark.feature("F-48")
def test_run_reports_every_stage(tmp_path: Path) -> None:
    config = Config()
    config = type(config)(
        hands=config.hands,
        difficulty=config.difficulty,
        visual=VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0),
        pedals=config.pedals,
        audio=config.audio,
    )
    source = tmp_path / "in.mid"
    FIXTURES["two-hands"]().save(source)

    summary = run(source, tmp_path / "out.mp4", config, duration=1.0).summary()
    for stage in ("arrange", "constrain", "audio", "notes"):
        assert stage in summary


@pytest.mark.feature("F-48")
def test_run_writes_a_silent_video_when_audio_is_off(tmp_path: Path) -> None:
    from psv.config import AudioConfig

    config = Config()
    config = type(config)(
        hands=config.hands,
        difficulty=config.difficulty,
        visual=VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0),
        pedals=config.pedals,
        audio=AudioConfig(backend="none"),
    )
    source = tmp_path / "in.mid"
    FIXTURES["single-note"]().save(source)
    output = tmp_path / "out.mp4"

    result = run(source, output, config, duration=1.0)
    assert result.audio.is_silent
    assert output.exists()
    assert output.stat().st_size > 0
