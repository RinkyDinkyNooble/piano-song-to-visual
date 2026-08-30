"""Violation detection.

Everything the engine does rests on this sweep being right. A missed violation
is a chord nobody can play; a phantom one is a note edited for no reason.
"""

from __future__ import annotations

import pytest

from psv.constraints.span import (
    detect_violations,
    verify_span,
    widest_span_per_hand,
)
from psv.model import Hand, Note, Part, Score


def note(
    pitch: int,
    start: float = 0.0,
    end: float = 1.0,
    hand: Hand = Hand.LEFT,
    velocity: int = 64,
) -> Note:
    return Note(pitch=pitch, start=start, end=end, velocity=velocity, hand=hand)


def score_of(*notes: Note) -> Score:
    return Score(parts=(Part(notes=tuple(sorted(notes))),))


# -- the basic question --------------------------------------------------


@pytest.mark.feature("F-15")
def test_a_chord_inside_the_limit_is_not_a_violation() -> None:
    notes = [note(60), note(67), note(72)]  # exactly 12 semitones
    assert detect_violations(notes, max_span=12) == []


@pytest.mark.feature("F-15")
def test_one_semitone_over_the_limit_is_a_violation() -> None:
    notes = [note(60), note(73)]
    violations = detect_violations(notes, max_span=12)
    assert len(violations) == 1
    assert violations[0].span == 13
    assert violations[0].hand is Hand.LEFT


@pytest.mark.feature("F-15")
def test_a_single_note_can_never_violate() -> None:
    assert detect_violations([note(60)], max_span=0) == []


@pytest.mark.feature("F-15")
def test_an_empty_score_has_no_violations() -> None:
    assert detect_violations([], max_span=12) == []


@pytest.mark.feature("F-15")
def test_the_violation_names_the_extremes() -> None:
    notes = [note(40), note(60), note(80)]
    violation = detect_violations(notes, max_span=12)[0]
    assert notes[violation.lowest].pitch == 40
    assert notes[violation.highest].pitch == 80


# -- hands are independent -----------------------------------------------


@pytest.mark.feature("F-15")
def test_a_wide_reach_split_across_two_hands_is_fine() -> None:
    """Forty semitones apart is unplayable in one hand and trivial in two."""
    notes = [note(40, hand=Hand.LEFT), note(80, hand=Hand.RIGHT)]
    assert detect_violations(notes, max_span=12) == []


@pytest.mark.feature("F-15")
def test_each_hand_is_checked_separately() -> None:
    notes = [
        note(40, hand=Hand.LEFT),
        note(60, hand=Hand.LEFT),
        note(80, hand=Hand.RIGHT),
        note(84, hand=Hand.RIGHT),
    ]
    violations = detect_violations(notes, max_span=12)
    assert [v.hand for v in violations] == [Hand.LEFT]


@pytest.mark.feature("F-15")
def test_the_violation_records_what_both_hands_were_holding() -> None:
    """The cheapest repair is usually moving a note across, so the detector
    captures the other hand rather than making repair rescan the score."""
    notes = [
        note(40, hand=Hand.LEFT),
        note(60, hand=Hand.LEFT),
        note(84, hand=Hand.RIGHT),
    ]
    violation = detect_violations(notes, max_span=12)[0]
    assert set(violation.active) == {Hand.LEFT, Hand.RIGHT}
    assert len(violation.active[Hand.RIGHT]) == 1


# -- time --------------------------------------------------------------


@pytest.mark.feature("F-15")
def test_notes_that_never_overlap_are_not_a_chord() -> None:
    """A wide leap is fine. The constraint is about simultaneous reach only."""
    notes = [note(40, 0.0, 1.0), note(90, 2.0, 3.0)]
    assert detect_violations(notes, max_span=12) == []


@pytest.mark.feature("F-15")
def test_notes_that_merely_touch_are_not_held_together() -> None:
    notes = [note(40, 0.0, 1.0), note(90, 1.0, 2.0)]
    assert detect_violations(notes, max_span=12) == []


@pytest.mark.feature("F-21")
def test_an_overlap_under_the_tolerance_is_not_a_violation() -> None:
    """A note released 5ms late is sloppy MIDI, not a stretch anyone holds."""
    notes = [note(40, 0.0, 1.005), note(90, 1.0, 2.0)]
    assert detect_violations(notes, max_span=12, tolerance=0.03) == []
    assert detect_violations(notes, max_span=12, tolerance=0.001) != []


@pytest.mark.feature("F-21")
def test_a_real_overlap_is_still_caught_with_tolerance_on() -> None:
    notes = [note(40, 0.0, 2.0), note(90, 0.5, 1.5)]
    assert detect_violations(notes, max_span=12, tolerance=0.03) != []


@pytest.mark.feature("F-15")
def test_a_violation_is_reported_at_the_moment_it_opens() -> None:
    notes = [note(60, 0.0, 5.0), note(80, 2.0, 3.0)]
    violation = detect_violations(notes, max_span=12)[0]
    assert violation.time == pytest.approx(2.0)


@pytest.mark.feature("F-15")
def test_violations_come_back_in_time_order() -> None:
    notes = [
        note(60, 0.0, 10.0),
        note(80, 1.0, 2.0),
        note(85, 5.0, 6.0),
        note(90, 3.0, 4.0),
    ]
    times = [v.time for v in detect_violations(notes, max_span=12)]
    assert times == sorted(times)


@pytest.mark.feature("F-15")
def test_a_note_ending_can_resolve_a_violation() -> None:
    """Three notes held; once the low one ends, the rest fit."""
    notes = [note(50, 0.0, 1.0), note(60, 0.0, 3.0), note(70, 2.0, 3.0)]
    violations = detect_violations(notes, max_span=12)
    assert violations == []


# -- reporting -----------------------------------------------------------


def test_widest_span_reports_each_hand() -> None:
    notes = [
        note(40, 0.0, 2.0, hand=Hand.LEFT),
        note(55, 0.0, 2.0, hand=Hand.LEFT),
        note(80, 0.0, 2.0, hand=Hand.RIGHT),
        note(84, 0.0, 2.0, hand=Hand.RIGHT),
    ]
    assert widest_span_per_hand(notes) == {Hand.LEFT: 15, Hand.RIGHT: 4}


def test_widest_span_of_a_single_line_is_zero() -> None:
    notes = [note(60, 0.0, 1.0), note(72, 2.0, 3.0)]
    assert widest_span_per_hand(notes) == {Hand.LEFT: 0}


def test_verify_span_reads_a_whole_score() -> None:
    clean = score_of(note(60), note(67))
    assert verify_span(clean, 12) == []

    broken = score_of(note(60), note(80))
    assert len(verify_span(broken, 12)) == 1


def test_a_negative_span_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        detect_violations([note(60)], max_span=-1)


@pytest.mark.feature("F-15")
def test_unassigned_notes_are_still_swept() -> None:
    """A score that skipped hand assignment must not silently pass."""
    notes = [note(40, hand=Hand.UNASSIGNED), note(80, hand=Hand.UNASSIGNED)]
    assert len(detect_violations(notes, max_span=12)) == 1


# -- salience ------------------------------------------------------------


def test_a_louder_note_outranks_a_quieter_one() -> None:
    from psv.constraints.salience import salience

    assert salience(note(60, velocity=100)) > salience(note(60, velocity=20))


def test_a_longer_note_outranks_a_shorter_one_at_equal_volume() -> None:
    from psv.constraints.salience import salience

    assert salience(note(60, 0.0, 2.0)) > salience(note(60, 0.0, 0.1))


def test_outer_voices_outrank_inner_ones() -> None:
    """Melody and bass carry the piece; the harmony between them is what a
    listener misses least, which is what decides both dropping and thinning."""
    from psv.constraints.salience import contextual_salience

    chord = [note(48), note(60), note(72)]
    assert contextual_salience(chord[0], chord) > contextual_salience(chord[1], chord)
    assert contextual_salience(chord[2], chord) > contextual_salience(chord[1], chord)


def test_a_note_with_no_context_is_scored_on_its_own() -> None:
    from psv.constraints.salience import contextual_salience, salience

    lone = note(60)
    assert contextual_salience(lone, []) == salience(lone)
