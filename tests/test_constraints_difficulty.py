"""Difficulty, and its separation from hand span.

The spec is explicit that these are different knobs: a harder setting should
mean more notes and faster passages, never a wider stretch. That separation is
structural rather than a promise, and the tests here check both halves of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import mido
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from psv.config import Config, DifficultyConfig, HandsConfig
from psv.constraints import constrain, verify_span
from psv.constraints.difficulty import PROFILES, apply_difficulty
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Score
from tests.fixtures.midi_builder import FIXTURES
from tests.test_constraints_repair import random_scores

#: Easiest to hardest. Every property below is stated over this order.
LEVELS = ["beginner", "easy", "medium", "hard", "original"]


def config_for(max_span: int = 12, difficulty: str = "medium") -> Config:
    return replace(
        Config(),
        hands=HandsConfig(max_span_semitones=max_span, overlap_tolerance_s=0.03),
        difficulty=DifficultyConfig(level=difficulty),
    )


def chord(pitches: list[int], start: float = 0.0, duration: float = 1.0) -> Part:
    return Part(
        notes=tuple(
            Note(
                pitch=p, start=start, end=start + duration, velocity=64, hand=Hand.LEFT
            )
            for p in sorted(pitches)
        ),
        hand=Hand.LEFT,
    )


# -- profiles ------------------------------------------------------------


def test_every_configurable_level_has_a_profile() -> None:
    from psv.config import DIFFICULTY_LEVELS

    assert set(PROFILES) == set(DIFFICULTY_LEVELS)


def test_profiles_get_stricter_as_difficulty_drops() -> None:
    caps = [PROFILES[level].max_simultaneous for level in LEVELS]
    assert caps[-1] is None, "original imposes no cap"
    numeric = [c for c in caps if c is not None]
    assert numeric == sorted(numeric)


@pytest.mark.feature("F-25")
def test_original_changes_nothing() -> None:
    score = read_midi(FIXTURES["orchestral"]())
    thinned, removed = apply_difficulty(score, "original")
    assert removed == ()
    assert thinned.notes == score.notes


def test_an_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown difficulty"):
        apply_difficulty(Score(), "impossible")


# -- what thinning actually does -----------------------------------------


@pytest.mark.feature("F-25")
def test_a_dense_chord_is_thinned_to_the_cap() -> None:
    score = Score(parts=(chord([60, 62, 64, 65, 67, 69]),))
    thinned, removed = apply_difficulty(score, "beginner")
    assert len(thinned.notes) == 2
    assert len(removed) == 4


@pytest.mark.feature("F-25")
def test_thinning_keeps_the_melody_and_the_bass() -> None:
    """Outer voices carry the tune and the harmony's foundation. What a
    listener misses least is whatever sits between them."""
    score = Score(parts=(chord([48, 55, 60, 64, 67, 72]),))
    thinned, _ = apply_difficulty(score, "beginner")
    assert sorted(n.pitch for n in thinned.notes) == [48, 72]


@pytest.mark.feature("F-25")
def test_a_chord_already_inside_the_cap_is_untouched() -> None:
    score = Score(parts=(chord([60, 64]),))
    thinned, removed = apply_difficulty(score, "beginner")
    assert removed == ()
    assert thinned.notes == score.notes


@pytest.mark.feature("F-25")
def test_ornaments_are_stripped_at_the_easiest_levels() -> None:
    """A very short note sounding under a held one is decoration."""
    score = Score(
        parts=(
            Part(
                notes=(
                    Note(pitch=60, start=0.0, end=2.0, velocity=64, hand=Hand.LEFT),
                    Note(pitch=62, start=0.5, end=0.53, velocity=64, hand=Hand.LEFT),
                ),
                hand=Hand.LEFT,
            ),
        )
    )
    thinned, removed = apply_difficulty(score, "beginner")
    assert [n.pitch for n in removed] == [62]
    assert [n.pitch for n in thinned.notes] == [60]


@pytest.mark.feature("F-25")
def test_a_lone_short_note_is_never_removed() -> None:
    """Stripping the only voice would leave a hole, not a simpler piece."""
    score = Score(
        parts=(
            Part(
                notes=(
                    Note(pitch=60, start=0.0, end=0.02, velocity=64, hand=Hand.LEFT),
                ),
                hand=Hand.LEFT,
            ),
        )
    )
    thinned, removed = apply_difficulty(score, "beginner")
    assert removed == ()
    assert len(thinned.notes) == 1


@pytest.mark.feature("F-25")
def test_hands_are_thinned_independently() -> None:
    """A four-note left hand and a one-note right hand is not a five-note
    chord; the cap is per hand because reach is per hand."""
    score = Score(
        parts=(
            chord([48, 52, 55, 59]),
            Part(
                notes=(
                    Note(pitch=84, start=0.0, end=1.0, velocity=64, hand=Hand.RIGHT),
                ),
                hand=Hand.RIGHT,
            ),
        )
    )
    thinned, _ = apply_difficulty(score, "beginner")
    right = [n for n in thinned.notes if n.hand is Hand.RIGHT]
    assert len(right) == 1, "the right hand was within its cap"


# -- the separation from span --------------------------------------------


@pytest.mark.feature("F-25")
@pytest.mark.parametrize("level", LEVELS)
def test_span_holds_at_every_difficulty(level: str) -> None:
    """The point of the whole design: a harder setting gives more notes and
    faster passages, never a wider stretch."""
    score = read_midi(FIXTURES["wide-span-chord"]())
    result = constrain(score, config_for(max_span=12, difficulty=level))
    assert verify_span(result.score, 12) == []


@pytest.mark.feature("F-25")
@settings(max_examples=120, deadline=None)
@given(
    score=random_scores(),
    level=st.sampled_from(LEVELS),
    max_span=st.integers(min_value=1, max_value=18),
)
def test_no_difficulty_can_widen_a_reach(
    score: Score, level: str, max_span: int
) -> None:
    result = constrain(score, config_for(max_span, level))
    assert verify_span(result.score, max_span) == []


@pytest.mark.feature("F-25")
def test_difficulty_only_ever_removes_notes() -> None:
    """Structural, not a promise: thinning has no code path that edits a note,
    which is why it cannot affect reach."""
    score = read_midi(FIXTURES["orchestral"]())
    for level in LEVELS:
        thinned, removed = apply_difficulty(score, level)
        kept = set(thinned.notes)
        assert kept <= set(score.notes)
        assert len(kept) + len(removed) == len(score.notes)


@pytest.mark.feature("F-25")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
def test_harder_levels_keep_more_of_a_real_piece(
    song_id: str, load_song: Callable[[str], mido.MidiFile]
) -> None:
    score = read_midi(load_song(song_id))
    counts = [len(apply_difficulty(score, level)[0].notes) for level in LEVELS]
    assert counts == sorted(counts), f"not monotonic across {LEVELS}: {counts}"
    assert counts[0] < counts[-1], "the levels should actually differ"
    assert counts[-1] == len(score.notes)
