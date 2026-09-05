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


@pytest.mark.feature("F-87")
def test_a_louder_note_outranks_a_quieter_one() -> None:
    """Read against the piece's own range, so this needs two velocities in the
    score to mean anything at all."""
    from psv.constraints.salience import Salience

    quiet, loud = note(60, velocity=20), note(64, 1.0, 1.5, velocity=100)
    weigh = Salience.analyse([quiet, loud])
    assert weigh.alone(loud) > weigh.alone(quiet)


@pytest.mark.feature("F-87")
def test_a_longer_note_outranks_a_shorter_one_at_equal_volume() -> None:
    from psv.constraints.salience import Salience

    weigh = Salience.analyse([])
    assert weigh.alone(note(60, 0.0, 2.0)) > weigh.alone(note(60, 0.0, 0.1))


@pytest.mark.feature("F-87")
def test_a_note_with_no_context_is_scored_on_its_own() -> None:
    """No chord around it, so no outer-voice bonus to add."""
    from psv.constraints.salience import Salience

    weigh = Salience.analyse([])
    lone = note(60)
    assert weigh.of(lone, []) == weigh.alone(lone)


# -- salience read off the whole score -----------------------------------


def run(pitches: list[int], start: float = 0.0, step: float = 0.06) -> list[Note]:
    """A scale in short notes: a melodic run, and every note an ornament by
    length alone."""
    return [
        note(pitch, start + index * step, start + index * step + step * 0.9)
        for index, pitch in enumerate(pitches)
    ]


@pytest.mark.feature("F-87")
def test_a_note_in_a_run_carries_the_line_and_an_isolated_short_note_does_not() -> None:
    """The distinction a duration threshold cannot make.

    Both are short. One is a scale going somewhere and the other is a stab on
    its own, and telling them apart needs the notes either side.
    """
    from psv.constraints.salience import Salience

    scale = run([60, 62, 64, 65, 67])
    stab = [note(84, 5.0, 5.05)]
    weigh = Salience.analyse([*scale, *stab])

    assert all(weigh.carries_line(n) for n in scale), "a scale is not a line"
    assert not weigh.carries_line(stab[0]), "a lone short note is a line"


@pytest.mark.feature("F-87")
def test_a_leap_is_not_a_line() -> None:
    """Two notes far apart in pitch are two voices, however close in time."""
    from psv.constraints.salience import Salience

    leaping = [note(40, 0.0, 0.05), note(80, 0.06, 0.11)]
    weigh = Salience.analyse(leaping)
    assert not any(weigh.carries_line(n) for n in leaping)


@pytest.mark.feature("F-87")
def test_notes_struck_together_are_a_chord_not_a_line() -> None:
    """Otherwise every close-voiced chord would read as a melody."""
    from psv.constraints.salience import Salience

    chord = [note(60, 0.0, 1.0), note(62, 0.0, 1.0), note(64, 0.0, 1.0)]
    weigh = Salience.analyse(chord)
    assert not any(weigh.carries_line(n) for n in chord)


@pytest.mark.feature("F-87")
def test_a_short_note_in_the_tune_outranks_the_long_note_under_it() -> None:
    """The bug this was built for.

    A melodic run is short notes over long accompaniment, so scoring by length
    keeps the accompaniment and throws away the tune. On the one test file with
    a fast right hand this was losing 231 of 505 melody notes.
    """
    from psv.constraints.salience import Salience

    tune = run([72, 74, 76, 77])
    held = note(60, 0.0, 2.0)
    weigh = Salience.analyse([*tune, held])

    among = [tune[1], held]
    assert weigh.of(tune[1], among) > weigh.of(held, among), (
        "the long note under a run still outranks the run"
    )


@pytest.mark.feature("F-87")
def test_the_shorter_of_two_notes_on_one_key_is_the_one_to_lose() -> None:
    """A piano cannot sound one key twice. Two overlapping notes at the same
    pitch are one sound written twice, and the copy that stops first is free to
    drop; dropping the other cuts the note the listener hears."""
    from psv.constraints.salience import Salience

    longer = note(45, 0.0, 1.0, velocity=90)
    shorter = note(45, 0.0, 0.2, velocity=120)
    weigh = Salience.analyse([longer, shorter])

    among = [longer, shorter]
    assert weigh.of(shorter, among) < weigh.of(longer, among), (
        "the copy that ends first was preferred, so the sound gets cut short"
    )


@pytest.mark.feature("F-87")
def test_length_is_counted_from_the_moment_of_the_choice() -> None:
    """What matters is how much of a note is still to come, not how long it was
    written. A note about to stop costs nothing to drop."""
    from psv.constraints.salience import Salience

    weigh = Salience.analyse([])
    nearly_over = note(60, 0.0, 1.02)
    just_begun = note(64, 1.0, 1.9)

    assert weigh.alone(nearly_over) > weigh.alone(just_begun), "written length"
    assert weigh.alone(nearly_over, now=1.0) < weigh.alone(just_begun, now=1.0), (
        "remaining length was not used"
    )


@pytest.mark.feature("F-87")
def test_velocity_is_read_against_the_range_the_piece_uses() -> None:
    """An engraved score exported to MIDI has one velocity for every note, and
    five of the six real test files are like that. A raw velocity term adds the
    same constant to all of them and decides nothing while looking decisive."""
    from psv.constraints.salience import Salience

    flat = [note(60 + i, i * 1.0, i * 1.0 + 0.5, velocity=64) for i in range(4)]
    weigh = Salience.analyse(flat)
    assert weigh.velocity_range == 0
    assert len({weigh.alone(n) for n in flat}) == 1, "a flat file is being ranked"

    varied = [note(60, 0.0, 0.5, velocity=30), note(64, 1.0, 1.5, velocity=120)]
    loud = Salience.analyse(varied)
    assert loud.alone(varied[1]) > loud.alone(varied[0])


@pytest.mark.feature("F-87")
def test_an_unanalysed_note_falls_back_rather_than_failing() -> None:
    """The repair stage scores notes it has just rewritten, which were not in
    the score the analysis read."""
    from psv.constraints.salience import Salience

    weigh = Salience.analyse(run([60, 62, 64]))
    rewritten = note(75, 9.0, 9.5)
    assert weigh.of(rewritten, [rewritten, note(70, 9.0, 9.5)]) > 0.0
    assert not weigh.carries_line(rewritten)


@pytest.mark.feature("F-87")
def test_outer_voices_still_outrank_inner_ones() -> None:
    """The one factor that was already there, and still the largest."""
    from psv.constraints.salience import Salience

    chord = [note(48), note(60), note(72)]
    weigh = Salience.analyse(chord)
    assert weigh.of(chord[0], chord) > weigh.of(chord[1], chord)
    assert weigh.of(chord[2], chord) > weigh.of(chord[1], chord)


@pytest.mark.feature("F-87")
def test_a_run_in_the_tune_survives_being_thinned() -> None:
    """End to end, through the stage that was destroying it.

    A scale in the right hand with a note held under it in the same hand, which
    is what makes the sweep look at it at all: a short note alone in its hand is
    already spared. The scale is the piece; losing it to keep the drone is the
    failure this exists to prevent.
    """
    from psv.constraints.difficulty import apply_difficulty

    tune = [
        Note(pitch=72 + i, start=i * 0.06, end=i * 0.06 + 0.05, hand=Hand.RIGHT)
        for i in range(16)
    ]
    drone = [Note(pitch=67, start=0.0, end=1.2, hand=Hand.RIGHT)]
    chords = [
        Note(pitch=pitch, start=0.0, end=1.2, hand=Hand.LEFT) for pitch in (48, 55)
    ]
    score = Score().with_notes([*tune, *drone, *chords])

    thinned, _ = apply_difficulty(score, "beginner")
    kept = {(n.pitch, round(n.start, 4)) for n in thinned.notes}
    survived = sum(1 for n in tune if (n.pitch, round(n.start, 4)) in kept)

    assert survived >= len(tune) - 1, (
        f"only {survived} of {len(tune)} run notes survived; the tune was thinned "
        f"away to keep the accompaniment"
    )
