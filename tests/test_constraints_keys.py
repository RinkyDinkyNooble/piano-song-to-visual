"""One key, one press.

A key held by one finger cannot be struck by another. Notation says it anyway,
and until this module existed psv drew the second note as a tile stacked on the
first and sent the synthesiser a note-on for a key that never came up.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from psv.config import Config, HandsConfig
from psv.constraints import constrain
from psv.constraints.keys import (
    detect_double_strikes,
    resolve_double_strikes,
    verify_single_press,
)
from psv.constraints.span import verify_span
from psv.model import HIGHEST_KEY, LOWEST_KEY, Hand, Note, Part, Provenance, Score


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


def config_for(max_span: int, *, single_press: bool = True) -> Config:
    return Config(
        hands=HandsConfig(max_span_semitones=max_span, single_press=single_press)
    )


# -- detection -----------------------------------------------------------


@pytest.mark.feature("F-88")
def test_a_key_tapped_while_held_is_a_double_strike() -> None:
    """ren's report: the right hand holds a key and the left hand taps it."""
    held = note(60, 0.0, 2.0, hand=Hand.RIGHT)
    tap = note(60, 1.0, 1.2, hand=Hand.LEFT)
    strikes = detect_double_strikes([held, tap])
    assert len(strikes) == 1
    assert strikes[0].pitch == 60
    assert strikes[0].time == pytest.approx(1.0)


@pytest.mark.feature("F-88")
def test_the_same_key_played_twice_in_a_row_is_not_a_double_strike() -> None:
    """Sequential presses are ordinary music, however close together."""
    assert detect_double_strikes([note(60, 0.0, 1.0), note(60, 1.0, 2.0)]) == []


@pytest.mark.feature("F-88")
def test_different_keys_never_clash_however_much_they_overlap() -> None:
    assert detect_double_strikes([note(60, 0.0, 4.0), note(61, 0.1, 4.0)]) == []


@pytest.mark.feature("F-88")
def test_a_brush_overlap_shorter_than_the_tolerance_is_not_a_clash() -> None:
    """A note let go 5 ms late is sloppy MIDI, not two fingers on one key."""
    late = note(60, 0.0, 1.005)
    assert detect_double_strikes([late, note(60, 1.0, 2.0)], tolerance=0.03) == []


# -- resolution ----------------------------------------------------------


@pytest.mark.feature("F-88")
def test_the_held_key_lifts_just_before_it_is_struck_again() -> None:
    held = note(60, 0.0, 2.0, hand=Hand.RIGHT)
    tap = note(60, 1.0, 1.2, hand=Hand.LEFT)
    notes, edits = resolve_double_strikes([held, tap])

    survivor = next(n for n in notes if n.end == pytest.approx(1.0))
    assert survivor.hand is Hand.RIGHT, "the held note is the one that lifts"
    assert Provenance.TRUNCATED in survivor.provenance
    assert tap in notes, "the tap is untouched"
    assert [strategy for strategy, _, _ in edits] == ["lift-to-restrike"]


@pytest.mark.feature("F-88")
def test_two_voices_striking_one_key_together_keep_the_longer() -> None:
    """Shortening cannot separate notes that start at the same instant, so one
    press has to serve both, and it lasts as long as the longer note asked."""
    short = note(60, 0.0, 0.5)
    long = note(60, 0.0, 3.0)
    notes, edits = resolve_double_strikes([short, long])
    assert notes == [long]
    assert [strategy for strategy, _, _ in edits] == ["merge-unison"]


@pytest.mark.feature("F-88")
def test_an_exact_duplicate_leaves_one_note() -> None:
    notes, _ = resolve_double_strikes([note(60, 0.0, 1.0), note(60, 0.0, 1.0)])
    assert notes == [note(60, 0.0, 1.0)]


@pytest.mark.feature("F-88")
def test_a_key_struck_three_times_under_one_held_note_resolves_completely() -> None:
    held = note(60, 0.0, 3.0, hand=Hand.RIGHT)
    taps = [note(60, t, t + 0.2, hand=Hand.LEFT) for t in (1.0, 1.5, 2.0)]
    notes, _ = resolve_double_strikes([held, *taps])
    assert detect_double_strikes(notes) == []
    assert len(notes) == 4, "every tap survives; only the held note is shortened"


@pytest.mark.feature("F-88")
def test_a_clean_score_is_handed_back_untouched() -> None:
    clean = [note(60, 0.0, 1.0), note(64, 0.0, 1.0), note(60, 2.0, 3.0)]
    notes, edits = resolve_double_strikes(clean)
    assert notes == clean
    assert edits == []


# -- nothing goes missing quietly ----------------------------------------


@pytest.mark.feature("F-88")
def test_every_removed_note_is_reported() -> None:
    _notes, edits = resolve_double_strikes([note(60, 0.0, 0.5), note(60, 0.0, 3.0)])
    removed = [before for _, before, after in edits if after is None]
    assert len(removed) == 1
    assert removed[0].end == pytest.approx(0.5)


# -- the whole engine ----------------------------------------------------


@pytest.mark.feature("F-88")
def test_constrain_resolves_double_strikes_it_was_given() -> None:
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 1.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(12))
    assert verify_single_press(result.score) == []


@pytest.mark.feature("F-88")
def test_an_octave_shift_will_not_land_on_a_key_that_is_already_sounding() -> None:
    """The guard used to read the pitches the *violating hand* held at the
    violating instant. That misses two cases the engine hits constantly: the
    note being moved outlasts that instant, and the clash is with the other
    hand.

    The left hand reaches 24 semitones from C2 to C4. C4 is the outlier, and
    the right hand cannot take it, so the octave shift is tried next and would
    drop C4 onto the C3 the right hand is already holding. That destroys the
    melody note and doubles a C3. Refusing lets truncation take the C2 instead,
    which costs two seconds of bass and keeps every pitch.
    """
    score = score_of(
        note(36, 0.0, 3.0, hand=Hand.LEFT),
        note(60, 1.0, 3.0, hand=Hand.LEFT),
        note(48, 0.0, 3.0, hand=Hand.RIGHT),
        note(42, 0.0, 3.0, hand=Hand.RIGHT),
    )
    result = constrain(score, config_for(12))

    assert 60 in {n.pitch for n in result.score.notes}, "the C4 was merged away"
    assert sorted(n.pitch for n in result.score.notes) == [36, 42, 48, 60]
    assert verify_single_press(result.score) == []


@pytest.mark.feature("F-94")
def test_no_span_limit_still_resolves_double_strikes() -> None:
    """The reported bug: every video rendered at span 0 had stacked tiles.

    Span and one key one press are different questions. How far a hand
    stretches differs by player and is fair to decline with
    `max_span_semitones = 0`. A key being one lever is not a judgement, and no
    setting makes a score that asks for it twice at once playable.
    """
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 1.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(0))

    assert result.span_enforced is False, "span is still not being enforced"
    assert result.single_press_enforced is True
    assert verify_single_press(result.score) == []
    assert result.repairs, "a note was edited and nothing recorded it"


@pytest.mark.feature("F-94")
def test_no_span_limit_still_leaves_the_reach_alone() -> None:
    """Resolving keys must not start enforcing span by the back door.

    Shortening and dropping only ever narrow what one hand holds, so a reach
    the user asked to keep is still there afterwards.
    """
    score = score_of(
        note(36, 0.0, 2.0, hand=Hand.LEFT),
        note(72, 0.0, 2.0, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(0))

    assert result.score.notes == score.notes, "an unreachable chord was repaired"
    assert len(verify_span(result.score, 12)) == 1


@pytest.mark.feature("F-94")
def test_turning_single_press_off_hands_back_the_score_as_written() -> None:
    """The escape hatch, and the one case where the clash survives on purpose.

    Resolving a double strike can cost music: a long note under a repeated tap
    keeps only what precedes the first tap, because the engine shortens and
    removes notes but does not split one into two. Someone who would rather
    have the notation can say so.
    """
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 1.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(0, single_press=False))

    assert result.single_press_enforced is False
    assert result.score.notes == score.notes
    assert len(verify_single_press(result.score)) == 1
    assert "not enforced" in result.summary()


@pytest.mark.feature("F-94")
def test_single_press_off_applies_with_a_span_limit_too() -> None:
    """One setting, one meaning, whichever path it takes through the engine."""
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 1.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(12, single_press=False))

    assert result.span_enforced is True
    assert result.single_press_enforced is False
    assert len(verify_single_press(result.score)) == 1


@pytest.mark.feature("F-94")
def test_repairs_made_without_a_span_limit_are_reported() -> None:
    """Music never goes missing quietly, on this path as much as the other.

    The summary used to return early when span was unenforced, because that
    path could not produce a repair. Now it can.
    """
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 0.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(0))

    assert result.counts == {"merge-unison": 1}
    assert "merge-unison" in result.summary()


# -- the invariant, over generated scores --------------------------------


@st.composite
def clashing_scores(draw: st.DrawFn) -> Score:
    """Scores drawn from few pitches, so same-key collisions are common."""
    pitches = draw(
        st.lists(
            st.integers(min_value=LOWEST_KEY, max_value=HIGHEST_KEY),
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    count = draw(st.integers(min_value=0, max_value=20))
    notes = []
    for _ in range(count):
        start = round(draw(st.floats(min_value=0.0, max_value=6.0)), 3)
        duration = round(draw(st.floats(min_value=0.05, max_value=3.0)), 3)
        notes.append(
            Note(
                pitch=draw(st.sampled_from(pitches)),
                start=start,
                end=start + duration,
                velocity=draw(st.integers(min_value=1, max_value=127)),
                hand=draw(st.sampled_from([Hand.LEFT, Hand.RIGHT])),
            )
        )
    return score_of(*notes)


SLOW = settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@pytest.mark.feature("F-88")
@SLOW
@given(score=clashing_scores(), max_span=st.integers(min_value=0, max_value=18))
def test_output_never_holds_one_key_with_two_notes(score: Score, max_span: int) -> None:
    """The promise, tested as a promise: any score, any limit, one key one press.

    The range starts at 0, which is the setting meaning "no span limit". It
    used to start at 1, so the one path that skipped this pass entirely was
    also the one path the property never visited.
    """
    result = constrain(score, config_for(max_span))
    assert verify_single_press(result.score) == []


@pytest.mark.feature("F-88")
@SLOW
@given(score=clashing_scores())
def test_resolution_never_invents_a_note(score: Score) -> None:
    notes, _ = resolve_double_strikes(score.notes)
    assert len(notes) <= len(score.notes)
    starts = {(n.pitch, n.start) for n in score.notes}
    assert all((n.pitch, n.start) in starts for n in notes)


@pytest.mark.feature("F-88")
@SLOW
@given(score=clashing_scores())
def test_resolving_twice_is_the_same_as_resolving_once(score: Score) -> None:
    once, _ = resolve_double_strikes(score.notes)
    twice, edits = resolve_double_strikes(once)
    assert twice == once
    assert edits == []
