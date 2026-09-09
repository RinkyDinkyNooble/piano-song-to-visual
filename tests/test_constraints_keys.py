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


def config_for(max_span: int) -> Config:
    return Config(hands=HandsConfig(max_span_semitones=max_span))


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
def test_no_span_limit_leaves_double_strikes_alone() -> None:
    """The known limit of this pass, kept in a test rather than left as a gap.

    `max_span_semitones = 0` means "the piece as written, I will judge it
    myself". Resolving a double strike costs a note, so that path does not,
    and the tiles stack in the video exactly as the score stacks them.
    """
    score = score_of(
        note(60, 0.0, 2.0, hand=Hand.RIGHT),
        note(60, 1.0, 1.2, hand=Hand.LEFT),
    )
    result = constrain(score, config_for(0))
    assert result.span_enforced is False
    assert result.score.notes == score.notes
    assert len(verify_single_press(result.score)) == 1


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
@given(score=clashing_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_output_never_holds_one_key_with_two_notes(score: Score, max_span: int) -> None:
    """The promise, tested as a promise: any score, any limit, one key one press."""
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
