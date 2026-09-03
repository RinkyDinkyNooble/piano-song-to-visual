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
from psv.model import DEFAULT_OVERLAP_TOLERANCE_S, Hand, Note, Part, Score
from psv.pipeline import run
from psv.sweep import PRESS, note_events
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


# -- notes shorter than the overlap tolerance ----------------------------


def test_a_note_shorter_than_the_tolerance_does_not_hold_a_voice() -> None:
    """Its release is clamped to its own start, and releases sort before
    presses, so it used to be taken out of the held set before it was put in
    and then left there forever. After eight such notes every later note was
    dropped as if the texture were full."""
    short = DEFAULT_OVERLAP_TOLERANCE_S / 2
    notes = [
        Note(pitch=60 + index % 12, start=index * 0.1, end=index * 0.1 + short)
        for index in range(200)
    ]
    kept, dropped = reduce_texture(notes, max_voices=8)
    assert dropped == [], "nothing here ever sounds with anything else"
    assert len(kept) == len(notes)


def test_the_held_set_empties_again_after_short_notes() -> None:
    """The leak, stated directly: press and release must balance."""
    short = DEFAULT_OVERLAP_TOLERANCE_S / 2
    notes = [
        Note(pitch=60, start=index * 0.5, end=index * 0.5 + short)
        for index in range(20)
    ]
    depth = 0
    for _time, rank, _index in note_events(notes, DEFAULT_OVERLAP_TOLERANCE_S):
        depth += 1 if rank == PRESS else -1
        assert depth >= 0, "a release arrived before its own press"
    assert depth == 0, "every press was released"


def test_short_notes_still_get_a_hand() -> None:
    """They have to stay visible to the sweep, not be skipped by it: a note
    with no hand renders in the neutral unassigned colour."""
    short = DEFAULT_OVERLAP_TOLERANCE_S / 2
    notes = [
        Note(pitch=48, start=0.0, end=short),
        Note(pitch=84, start=0.0, end=short),
    ]
    assigned = assign_hands(notes, max_span=12)
    assert {note.hand for note in assigned} == {Hand.LEFT, Hand.RIGHT}


# -- a file whose tracks are named by an engraver, not by hand -----------


def two_part_score(low_name: str = "track 1", high_name: str = "track 2") -> Score:
    """Two parts in clearly separate registers, with no hands assigned.

    What LilyPond exports: real two-hand piano writing whose track names say
    nothing about which hand is which.
    """
    left = Part(
        name=low_name,
        notes=tuple(
            Note(pitch=45 + index % 5, start=index * 0.5, end=index * 0.5 + 0.4)
            for index in range(40)
        ),
    )
    right = Part(
        name=high_name,
        notes=tuple(
            Note(pitch=72 + index % 7, start=index * 0.25, end=index * 0.25 + 0.2)
            for index in range(80)
        ),
    )
    return Score(parts=(left, right))


@pytest.mark.feature("F-44")
def test_two_parts_in_separate_registers_are_left_alone() -> None:
    """`psv inspect` calls this "hands look already separated" and says the
    decision is the arrange stage's to make. The stage has to make the same one,
    or the report is a lie and a quarter of the piece is thrown away."""
    score = two_part_score()
    result = arrange(score, max_span=12)

    assert result.was_already_arranged
    assert result.dropped == ()
    assert [n.pitch for n in result.score.notes] == [n.pitch for n in score.notes]


@pytest.mark.feature("F-44")
def test_an_already_separated_score_still_gets_its_hands_named() -> None:
    """Left alone means no note moves, not that hands stay unassigned: the
    renderer colours by hand and the constraint engine works per hand."""
    result = arrange(two_part_score(), max_span=12)
    hands = {note.hand for note in result.score.notes}
    assert hands == {Hand.LEFT, Hand.RIGHT}

    low = [n for n in result.score.notes if n.hand is Hand.LEFT]
    high = [n for n in result.score.notes if n.hand is Hand.RIGHT]
    assert max(n.pitch for n in low) < min(n.pitch for n in high)


@pytest.mark.feature("F-44")
def test_arrange_agrees_with_what_inspect_reports() -> None:
    """These two answer the same question and used to disagree."""
    from psv.inspect import inspect_score

    score = two_part_score()
    assert inspect_score(score).looks_pre_separated
    assert looks_arranged(score)


@pytest.mark.feature("F-44")
def test_two_parts_that_cross_registers_are_still_arranged() -> None:
    """Two parts are not automatically two hands. Parts that run through each
    other's register are two *voices*, and they need real assignment rather
    than a label saying the engraver already split them."""
    tangled = Score(
        parts=(
            Part(
                notes=tuple(
                    Note(pitch=48 + i, start=i * 0.5, end=i * 0.5 + 0.4)
                    for i in range(36)  # sweeps up through the other part
                )
            ),
            Part(
                notes=tuple(
                    Note(pitch=60, start=i * 0.5, end=i * 0.5 + 0.4) for i in range(36)
                )
            ),
        )
    )
    assert not looks_arranged(tangled)
    assert not arrange(tangled, max_span=12).was_already_arranged


# -- nothing disappears without being reported ---------------------------


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_every_note_removed_is_a_note_reported(fixture: str) -> None:
    """The invariant behind the whole class of bug this suite now guards.

    Both stages are allowed to remove notes. Neither is allowed to remove one
    quietly. When the sweep leaked, notes vanished inside `reduce_texture` and
    `apply_difficulty` and the counts still looked plausible, so the only way to
    notice was to compare a rendered video against the source by ear.
    """
    from psv.constraints import constrain

    config = Config.load(None)
    score = read_midi(FIXTURES[fixture]())
    before = len(score.notes)

    arranged = arrange(
        score,
        max_span=config.hands.layout_span,
        tolerance=config.hands.overlap_tolerance_s,
    )
    kept = len(arranged.score.notes)
    assert kept + len(arranged.dropped) == before, "arrange lost notes silently"

    result = constrain(arranged.score, config)
    reported = sum(1 for r in result.repairs if r.strategy == "drop") + len(
        result.removed_for_difficulty
    )
    assert kept - len(result.score.notes) == reported, "constrain lost notes silently"


@pytest.mark.needs_song("toccata")
def test_a_real_song_loses_nothing_unreported(
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    """The fixtures are small enough to be lucky. This one is 3,651 notes."""
    from psv.constraints import constrain
    from psv.midi import read_midi_file

    load_song("toccata")  # skips when absent
    source = (
        Path(__file__).resolve().parent
        / "assets"
        / "public-domain"
        / "bach-bwv565-toccata-and-fugue.mid"
    )
    config = Config.load(None)
    score = read_midi_file(source)

    arranged = arrange(score, max_span=config.hands.layout_span)
    assert len(arranged.score.notes) + len(arranged.dropped) == len(score.notes)

    result = constrain(arranged.score, config)
    reported = sum(1 for r in result.repairs if r.strategy == "drop") + len(
        result.removed_for_difficulty
    )
    assert len(arranged.score.notes) - len(result.score.notes) == reported
