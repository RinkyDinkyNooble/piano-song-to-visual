"""The shared press/release sweep, and the invariant every user of it needs.

This file exists because of a bug, and the bug is worth keeping in view. Five
modules each wrote their own version of `note_events`, and all five clamped a
note's release to `max(start, end - tolerance)` without noticing what that does
to a note shorter than the tolerance: the release lands on the note's own start,
releases sort before presses, and the note is removed from the held set before
it is ever inserted. It then stays in that set for the rest of the piece.

The damage was silent and it compounded. On one real file, 171 notes of 26 ms
against a 30 ms tolerance produced 547 span violations that did not exist, made
`difficulty = "medium"` drop 273 notes it had no reason to drop, and let the
arrange stage throw away a quarter of the piece.

So the tests here are deliberately about the *property*, not about that file:
presses and releases must balance, and a note too short to be held must not
change what anything else is judged against.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S as TOL
from psv.model import Hand, Note, Part, Score
from psv.sweep import LATE_RELEASE, PRESS, RELEASE, note_events


def sweep_depth(notes: list[Note], tolerance: float = TOL) -> tuple[int, int]:
    """Run the sweep, returning the lowest depth reached and the final depth.

    A negative low means some note was released before it was pressed. A
    non-zero final depth means something was never released at all. Either is
    the bug.
    """
    depth = low = 0
    for _time, rank, _index in note_events(notes, tolerance):
        depth += 1 if rank == PRESS else -1
        low = min(low, depth)
    return low, depth


# -- the invariant -------------------------------------------------------


def test_a_note_is_never_released_before_it_is_pressed() -> None:
    """The bug, stated as directly as it can be."""
    notes = [Note(pitch=60, start=0.0, end=TOL / 2)]
    low, final = sweep_depth(notes)
    assert low == 0, "a release arrived first"
    assert final == 0, "the press was never released"


def test_a_zero_length_note_still_balances() -> None:
    notes = [Note(pitch=60, start=1.0, end=1.0)]
    assert sweep_depth(notes) == (0, 0)


def test_a_short_note_releases_after_its_own_press() -> None:
    note = Note(pitch=60, start=2.0, end=2.0 + TOL / 3)
    ranks = [rank for _t, rank, _i in note_events([note])]
    assert ranks == [PRESS, LATE_RELEASE]


def test_a_normal_note_releases_early_and_before_other_presses() -> None:
    """The tolerance still does its job: a note handed over exactly where the
    next begins must not read as two notes held together."""
    first = Note(pitch=60, start=0.0, end=1.0)
    second = Note(pitch=64, start=1.0, end=2.0)
    events = note_events([first, second])
    assert events[0] == (0.0, PRESS, 0)
    assert events[1][1] == RELEASE
    assert events[1][0] < 1.0, "released before the next note presses"


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=21, max_value=108),
            st.floats(min_value=0.0, max_value=60.0, allow_nan=False),
            st.floats(min_value=0.0, max_value=2.0, allow_nan=False),
        ),
        min_size=1,
        max_size=60,
    ),
    st.floats(min_value=0.0, max_value=0.2, allow_nan=False),
)
def test_the_sweep_always_balances(
    raw: list[tuple[int, float, float]], tolerance: float
) -> None:
    """Over any notes and any tolerance, including durations far below it.

    This is the test that would have caught the original bug, and the reason it
    is a property rather than an example: the failing case was a 26 ms note
    against a 30 ms tolerance, which nobody would have thought to write down.
    """
    notes = [
        Note(pitch=pitch, start=start, end=start + length)
        for pitch, start, length in raw
    ]
    low, final = sweep_depth(notes, tolerance)
    assert low >= 0, "released before pressed"
    assert final == 0, "a note was never released"


# -- what the leak did to the things built on top of it ------------------


def short_and_long_versions() -> tuple[list[Note], list[Note]]:
    """The same music twice, once with brief notes and once with held ones.

    The brief version must not be judged as *more* crowded than the held one:
    that is exactly backwards, and it is what the leak produced.
    """
    brief = [
        Note(
            pitch=60 + (index % 5),
            start=index * 0.5,
            end=index * 0.5 + TOL / 3,
            hand=Hand.RIGHT,
        )
        for index in range(40)
    ]
    held = [
        Note(
            pitch=note.pitch,
            start=note.start,
            end=note.start + TOL * 3,
            hand=Hand.RIGHT,
        )
        for note in brief
    ]
    return brief, held


@pytest.mark.feature("F-19")
def test_brief_notes_are_never_judged_more_crowded_than_held_ones() -> None:
    from psv.constraints.span import detect_violations, widest_span_per_hand

    brief, held = short_and_long_versions()
    assert len(detect_violations(brief, 12)) <= len(detect_violations(held, 12))
    assert (
        widest_span_per_hand(brief)[Hand.RIGHT]
        <= (widest_span_per_hand(held)[Hand.RIGHT])
    )


@pytest.mark.feature("F-19")
def test_brief_notes_do_not_accumulate_a_phantom_span() -> None:
    """None of these ever sound together, so no hand is asked to reach at all."""
    from psv.constraints.span import detect_violations

    brief, _ = short_and_long_versions()
    assert detect_violations(brief, 1) == []


@pytest.mark.feature("F-24")
def test_difficulty_does_not_thin_music_that_is_already_thin() -> None:
    """One note at a time, none of them overlapping. There is nothing to
    simplify, and `medium` used to remove hundreds of them."""
    from psv.constraints.difficulty import apply_difficulty

    brief, _ = short_and_long_versions()
    score = Score(parts=(Part(notes=tuple(brief), hand=Hand.RIGHT),))
    for level in ("original", "hard", "medium"):
        _result, removed = apply_difficulty(score, level, TOL)
        assert removed == (), f"{level} removed notes from a single line"


@pytest.mark.feature("F-45")
def test_reduction_leaves_a_single_line_alone() -> None:
    from psv.arrange import reduce_texture

    brief, _ = short_and_long_versions()
    _kept, dropped = reduce_texture(brief, max_voices=8)
    assert dropped == []


@pytest.mark.feature("F-06")
def test_inspect_does_not_over_report_a_thin_texture() -> None:
    from psv.inspect import inspect_score

    brief, _ = short_and_long_versions()
    report = inspect_score(Score(parts=(Part(notes=tuple(brief)),)))
    assert report.peak_polyphony == 1
    assert report.widest_span == 0
