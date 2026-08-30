"""The Score model: invariants, overlap semantics, and provenance."""

from __future__ import annotations

import pytest

from psv.model import (
    HIGHEST_KEY,
    LOWEST_KEY,
    Hand,
    Note,
    Part,
    Pedal,
    PedalEvent,
    Provenance,
    Score,
    is_black_key,
    pitch_name,
)


def note(pitch: int = 60, start: float = 0.0, end: float = 1.0, **kw: object) -> Note:
    return Note(pitch=pitch, start=start, end=end, **kw)  # type: ignore[arg-type]


# -- validation ----------------------------------------------------------


def test_a_note_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValueError, match="ends before it starts"):
        Note(pitch=60, start=2.0, end=1.0)


@pytest.mark.parametrize("pitch", [-1, 128])
def test_pitch_must_be_in_midi_range(pitch: int) -> None:
    with pytest.raises(ValueError, match="pitch out of MIDI range"):
        Note(pitch=pitch, start=0.0, end=1.0)


@pytest.mark.parametrize("velocity", [-1, 128])
def test_velocity_must_be_in_midi_range(velocity: int) -> None:
    with pytest.raises(ValueError, match="velocity out of MIDI range"):
        Note(pitch=60, start=0.0, end=1.0, velocity=velocity)


def test_a_pedal_depth_of_zero_is_not_a_press() -> None:
    with pytest.raises(ValueError, match="depth out of range"):
        PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=1.0, depth=0)


# -- keyboard ------------------------------------------------------------


@pytest.mark.parametrize(
    ("pitch", "black"),
    [
        (60, False),
        (61, True),
        (62, False),
        (63, True),
        (64, False),
        (65, False),
        (66, True),
        (67, False),
        (68, True),
        (69, False),
        (70, True),
        (71, False),
    ],
)
def test_black_keys_are_identified_across_one_octave(pitch: int, black: bool) -> None:
    assert is_black_key(pitch) is black
    assert note(pitch).is_black_key is black


def test_pitch_names_use_scientific_notation() -> None:
    assert pitch_name(60) == "C4"
    assert pitch_name(21) == "A0"
    assert pitch_name(108) == "C8"
    assert pitch_name(61) == "C#4"


def test_notes_outside_the_88_keys_are_flagged() -> None:
    assert note(LOWEST_KEY).on_keyboard
    assert note(HIGHEST_KEY).on_keyboard
    assert not note(LOWEST_KEY - 1).on_keyboard
    assert not note(HIGHEST_KEY + 1).on_keyboard


# -- overlap -------------------------------------------------------------


def test_notes_that_share_time_overlap() -> None:
    assert note(60, 0.0, 2.0).overlaps(note(64, 1.0, 3.0))


def test_notes_that_merely_touch_do_not_overlap() -> None:
    assert not note(60, 0.0, 1.0).overlaps(note(64, 1.0, 2.0))


@pytest.mark.feature("F-21")
def test_an_overlap_under_the_tolerance_does_not_count() -> None:
    """Sloppy MIDI, not a stretch the player has to hold."""
    first = note(48, 0.0, 1.005)
    second = note(84, 1.0, 2.0)
    assert not first.overlaps(second, tolerance=0.03)
    assert first.overlaps(second, tolerance=0.001)


def test_sounds_at_is_half_open() -> None:
    n = note(60, 1.0, 2.0)
    assert not n.sounds_at(0.999)
    assert n.sounds_at(1.0)
    assert n.sounds_at(1.999)
    assert not n.sounds_at(2.0)


# -- provenance ----------------------------------------------------------


@pytest.mark.feature("F-26")
def test_an_untouched_note_reports_no_edits() -> None:
    assert note().provenance == (Provenance.ORIGINAL,)
    assert not note().was_edited


@pytest.mark.feature("F-26")
def test_an_octave_shift_is_recorded() -> None:
    shifted = note(60).octave_shifted(-1)
    assert shifted.pitch == 48
    assert Provenance.OCTAVE_SHIFTED in shifted.provenance
    assert shifted.was_edited


def test_shifting_by_zero_octaves_changes_nothing() -> None:
    assert note(60).octave_shifted(0) == note(60)


@pytest.mark.feature("F-26")
def test_truncation_is_recorded_and_never_lengthens() -> None:
    original = note(60, 0.0, 2.0)
    shortened = original.shortened_to(1.0)
    assert shortened.end == 1.0
    assert Provenance.TRUNCATED in shortened.provenance
    # Asking for a later end is a no-op, not an extension.
    assert original.shortened_to(5.0) == original


def test_truncating_below_the_start_clamps_to_zero_length() -> None:
    shortened = note(60, 1.0, 2.0).shortened_to(0.0)
    assert shortened.start == shortened.end == 1.0


@pytest.mark.feature("F-26")
def test_first_hand_assignment_is_not_an_edit_but_moving_is() -> None:
    assigned = note().assigned_to(Hand.RIGHT)
    assert assigned.hand is Hand.RIGHT
    assert not assigned.was_edited

    moved = assigned.assigned_to(Hand.LEFT)
    assert moved.hand is Hand.LEFT
    assert Provenance.REASSIGNED in moved.provenance


def test_provenance_does_not_accumulate_duplicates() -> None:
    twice = note(60).octave_shifted(1).octave_shifted(1)
    assert twice.provenance.count(Provenance.OCTAVE_SHIFTED) == 1


# -- ordering and containers ---------------------------------------------


def test_notes_sort_into_playing_order_low_pitch_first() -> None:
    notes = [note(67, 0.0), note(60, 1.0), note(64, 0.0)]
    assert [n.pitch for n in sorted(notes)] == [64, 67, 60]


def test_an_empty_score_has_no_duration_and_no_range() -> None:
    score = Score()
    assert score.is_empty
    assert score.duration == 0.0
    assert score.pitch_range is None
    assert score.notes == ()


def test_score_duration_includes_pedal_ringing_past_the_last_note() -> None:
    score = Score(
        parts=(Part(notes=(note(60, 0.0, 1.0),)),),
        pedals=(PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=4.0),),
    )
    assert score.duration == pytest.approx(4.0)


def test_notes_between_returns_only_the_visible_window() -> None:
    score = Score(
        parts=(
            Part(notes=(note(60, 0.0, 1.0), note(62, 5.0, 6.0), note(64, 0.5, 5.5))),
        )
    )
    window = score.notes_between(2.0, 3.0)
    assert [n.pitch for n in window] == [64]


def test_pedal_at_finds_only_the_requested_pedal() -> None:
    score = Score(
        pedals=(
            PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=2.0),
            PedalEvent(pedal=Pedal.SOFT, start=3.0, end=4.0),
        )
    )
    assert score.pedal_at(1.0) is not None
    assert score.pedal_at(3.5) is None
    assert score.pedal_at(3.5, Pedal.SOFT) is not None


def test_with_notes_regroups_parts_by_hand() -> None:
    score = Score().with_notes(
        [
            note(60, 0.0, hand=Hand.RIGHT),
            note(48, 0.0, hand=Hand.LEFT),
            note(64, 1.0, hand=Hand.RIGHT),
        ]
    )
    assert [part.hand for part in score.parts] == [Hand.LEFT, Hand.RIGHT]
    assert len(score.parts[0]) == 1
    assert len(score.parts[1]) == 2


def test_half_pedal_is_not_reduced_to_a_boolean() -> None:
    shallow = PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=1.0, depth=20)
    deep = PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=1.0, depth=100)
    assert shallow != deep
    assert not shallow.is_full
    assert deep.is_full
