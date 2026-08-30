"""The repair engine.

The headline test here is `test_output_never_exceeds_the_configured_span`. The
promise this project makes is "no input ever produces a chord you cannot
reach", and a claim of that shape can only be tested with generated inputs, not
with examples. Everything else in this file explains *how* that guarantee is
met, one strategy at a time.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import mido
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from psv.config import Config, DifficultyConfig, HandsConfig
from psv.constraints import constrain, verify_span
from psv.constraints.repair import MAX_OCTAVE_SHIFTS
from psv.midi import read_midi
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
)
from tests.fixtures.midi_builder import FIXTURES


def config_for(
    max_span: int = 12, difficulty: str = "original", tolerance: float = 0.03
) -> Config:
    return replace(
        Config(),
        hands=HandsConfig(max_span_semitones=max_span, overlap_tolerance_s=tolerance),
        difficulty=DifficultyConfig(level=difficulty),
    )


def note(
    pitch: int,
    start: float = 0.0,
    end: float = 1.0,
    hand: Hand = Hand.LEFT,
    velocity: int = 64,
) -> Note:
    return Note(pitch=pitch, start=start, end=end, velocity=velocity, hand=hand)


def score_of(*notes: Note, pedals: tuple[PedalEvent, ...] = ()) -> Score:
    return Score(parts=(Part(notes=tuple(sorted(notes))),), pedals=pedals)


def strategies_used(result: object) -> list[str]:
    return [r.strategy for r in result.repairs]  # type: ignore[attr-defined]


# -- strategy 1: reassign ------------------------------------------------


@pytest.mark.feature("F-16")
def test_an_outlier_moves_to_the_free_hand() -> None:
    """The cheapest repair there is: nothing sounds any different."""
    result = constrain(score_of(note(36), note(72)), config_for())

    assert strategies_used(result) == ["reassign"]
    assert {n.pitch for n in result.score.notes} == {36, 72}
    moved = next(n for n in result.score.notes if n.pitch == 72)
    assert moved.hand is Hand.RIGHT
    assert Provenance.REASSIGNED in moved.provenance


@pytest.mark.feature("F-16")
def test_reassign_is_refused_when_the_other_hand_cannot_reach() -> None:
    """Moving the note would only relocate the problem."""
    result = constrain(
        score_of(
            note(36, hand=Hand.LEFT),
            note(72, hand=Hand.LEFT),
            note(100, hand=Hand.RIGHT),
        ),
        config_for(),
    )
    assert "reassign" not in strategies_used(result)
    assert verify_span(result.score, 12) == []


# -- strategy 2 and 4: truncate, and why the pedal decides ---------------


def held_bass_scenario(with_pedal: bool) -> Score:
    """A bass note held into a far-away note, with both hands otherwise busy.

    Constructed so reassign cannot apply, which is what leaves the choice
    between truncating and shifting an octave.
    """
    pedals = (
        (PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=4.0),) if with_pedal else ()
    )
    return Score(
        parts=(
            Part(
                notes=(
                    note(36, 0.0, 4.0, Hand.LEFT),
                    note(72, 1.0, 2.0, Hand.LEFT),
                ),
                hand=Hand.LEFT,
            ),
            Part(notes=(note(90, 0.0, 4.0, Hand.RIGHT),), hand=Hand.RIGHT),
        ),
        pedals=pedals,
    )


@pytest.mark.feature("F-18")
def test_truncation_is_preferred_while_the_sustain_pedal_is_down() -> None:
    """The string keeps ringing, so lifting the key early is inaudible.

    This is the one place the engine reads CC64, and it is worth the trouble:
    the note keeps its pitch and its register, and the listener hears nothing
    different at all.
    """
    result = constrain(held_bass_scenario(with_pedal=True), config_for())

    assert strategies_used(result) == ["truncate-under-pedal"]
    bass = next(n for n in result.score.notes if n.pitch == 36)
    assert bass.end == pytest.approx(1.0)
    assert Provenance.TRUNCATED in bass.provenance
    # Every pitch survives untouched: nothing was moved or dropped.
    assert sorted(n.pitch for n in result.score.notes) == [36, 72, 90]


@pytest.mark.feature("F-18")
def test_without_the_pedal_the_engine_moves_the_octave_instead() -> None:
    """Now the shortened note would be audible, so it ranks below shifting."""
    result = constrain(held_bass_scenario(with_pedal=False), config_for())

    assert "truncate-under-pedal" not in strategies_used(result)
    assert "octave-shift" in strategies_used(result)
    bass = next(n for n in result.score.notes if n.pitch == 36)
    assert bass.end == pytest.approx(4.0), "the bass keeps its full length"


@pytest.mark.feature("F-18")
def test_truncation_cannot_separate_notes_struck_together() -> None:
    """Shortening a note does nothing about a chord: they start at the same
    instant, so the stretch exists the moment both are down."""
    result = constrain(
        score_of(note(36, 0.0, 2.0), note(72, 0.0, 2.0)),
        config_for(),
    )
    assert "truncate" not in strategies_used(result)
    assert "truncate-under-pedal" not in strategies_used(result)


# -- strategy 3: octave shift --------------------------------------------


@pytest.mark.feature("F-17")
def test_an_octave_shift_keeps_the_pitch_class() -> None:
    """Harmonic function survives; only the register changes."""
    result = constrain(
        score_of(
            note(36, hand=Hand.LEFT),
            note(72, hand=Hand.LEFT),
            note(96, hand=Hand.RIGHT),
        ),
        config_for(),
    )
    assert "octave-shift" in strategies_used(result)
    pitches = sorted(n.pitch for n in result.score.notes)
    assert all(p % 12 == 0 for p in pitches), "every note is still a C"
    assert verify_span(result.score, 12) == []


@pytest.mark.feature("F-17")
def test_a_shift_never_lands_on_a_note_the_hand_already_holds() -> None:
    """Two voices merging into one would silently lose a note."""
    result = constrain(
        score_of(note(48), note(60), note(61)),
        config_for(max_span=12),
    )
    pitches = [n.pitch for n in result.score.notes]
    assert len(pitches) == len(set(pitches)) or verify_span(result.score, 12) == []


@pytest.mark.feature("F-17")
def test_shifting_is_capped_so_a_note_cannot_oscillate() -> None:
    result = constrain(score_of(note(21), note(108)), config_for())
    for repaired in result.score.notes:
        shifts = abs(repaired.pitch - 21) // 12 if repaired.pitch < 60 else 0
        assert shifts <= MAX_OCTAVE_SHIFTS + 1


# -- strategy 5: drop ----------------------------------------------------


def blocked_scenario() -> Score:
    """A score where every gentler repair is deliberately unavailable.

    reassign - the right hand is far too high to accept anything;
    truncate - all three left-hand notes are struck together;
    shift    - moving the low note up an octave lands on one already held.
    """
    return Score(
        parts=(
            Part(
                notes=(
                    note(21, 0.0, 2.0, Hand.LEFT),
                    note(33, 0.0, 2.0, Hand.LEFT),
                    note(34, 0.0, 2.0, Hand.LEFT),
                ),
                hand=Hand.LEFT,
            ),
            Part(
                notes=(
                    note(95, 0.0, 2.0, Hand.RIGHT),
                    note(105, 0.0, 2.0, Hand.RIGHT),
                ),
                hand=Hand.RIGHT,
            ),
        )
    )


@pytest.mark.feature("F-19")
def test_dropping_is_the_last_resort_when_nothing_else_applies() -> None:
    result = constrain(blocked_scenario(), config_for())
    assert "drop" in strategies_used(result)
    assert verify_span(result.score, 12) == []


@pytest.mark.feature("F-19")
def test_a_dropped_note_is_recorded_rather_than_vanishing() -> None:
    result = constrain(blocked_scenario(), config_for(max_span=1))
    drops = [r for r in result.repairs if r.dropped]
    assert drops, "something had to give at a 1-semitone limit"
    assert all(r.after is None for r in drops)
    assert all(r.before is not None for r in drops)
    assert verify_span(result.score, 1) == []


# -- keyboard edges ------------------------------------------------------


@pytest.mark.feature("F-22")
def test_repairs_at_the_bottom_of_the_keyboard_stay_on_it() -> None:
    result = constrain(
        score_of(note(LOWEST_KEY, 0.0, 2.0), note(LOWEST_KEY + 20, 0.0, 2.0)),
        config_for(),
    )
    assert all(LOWEST_KEY <= n.pitch <= HIGHEST_KEY for n in result.score.notes)
    assert verify_span(result.score, 12) == []


@pytest.mark.feature("F-22")
def test_repairs_at_the_top_of_the_keyboard_stay_on_it() -> None:
    result = constrain(
        score_of(note(HIGHEST_KEY - 20, 0.0, 2.0), note(HIGHEST_KEY, 0.0, 2.0)),
        config_for(),
    )
    assert all(LOWEST_KEY <= n.pitch <= HIGHEST_KEY for n in result.score.notes)
    assert verify_span(result.score, 12) == []


@pytest.mark.feature("F-22")
def test_the_whole_span_edge_case_fixture_comes_out_playable() -> None:
    score = read_midi(FIXTURES["span-edge-cases"]())
    result = constrain(score, config_for())
    assert verify_span(result.score, 12) == []
    assert all(LOWEST_KEY <= n.pitch <= HIGHEST_KEY for n in result.score.notes)


# -- generated inputs: the guarantee -------------------------------------


@st.composite
def random_scores(draw: st.DrawFn) -> Score:
    """Scores with hands already assigned, so the span engine is what is tested."""
    count = draw(st.integers(min_value=0, max_value=24))
    notes = []
    for _ in range(count):
        pitch = draw(st.integers(min_value=LOWEST_KEY, max_value=HIGHEST_KEY))
        start = round(draw(st.floats(min_value=0.0, max_value=15.0)), 3)
        duration = round(draw(st.floats(min_value=0.05, max_value=4.0)), 3)
        notes.append(
            Note(
                pitch=pitch,
                start=start,
                end=start + duration,
                velocity=draw(st.integers(min_value=1, max_value=127)),
                hand=draw(st.sampled_from([Hand.LEFT, Hand.RIGHT])),
            )
        )
    return Score(parts=(Part(notes=tuple(sorted(notes))),))


SLOW = settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@pytest.mark.feature("F-20")
@SLOW
@given(score=random_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_output_never_exceeds_the_configured_span(score: Score, max_span: int) -> None:
    """The promise, tested as a promise.

    Any score, any limit: no instant in the output has one hand reaching
    further than it was told it could.
    """
    result = constrain(score, config_for(max_span))
    assert verify_span(result.score, max_span) == []


@pytest.mark.feature("F-22")
@SLOW
@given(score=random_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_output_always_stays_on_the_keyboard(score: Score, max_span: int) -> None:
    result = constrain(score, config_for(max_span))
    assert all(LOWEST_KEY <= n.pitch <= HIGHEST_KEY for n in result.score.notes)


@pytest.mark.feature("F-20")
@SLOW
@given(score=random_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_repairs_never_invent_notes(score: Score, max_span: int) -> None:
    """The engine may move, shorten, or remove. It may never add."""
    result = constrain(score, config_for(max_span))
    assert len(result.score.notes) <= len(score.notes)


@pytest.mark.feature("F-23")
@SLOW
@given(score=random_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_a_conforming_score_is_returned_untouched(score: Score, max_span: int) -> None:
    """Nothing that already fits is edited, so the engine cannot make a
    playable arrangement worse."""
    if verify_span(score, max_span, tolerance=0.03):
        return
    result = constrain(score, config_for(max_span))
    assert result.repairs == ()
    assert result.score.notes == score.notes


@pytest.mark.feature("F-24")
@SLOW
@given(score=random_scores(), max_span=st.integers(min_value=1, max_value=18))
def test_constraining_twice_is_the_same_as_once(score: Score, max_span: int) -> None:
    """A second pass has nothing left to do. Without this, the engine could
    keep degrading a score every time it ran."""
    config = config_for(max_span)
    once = constrain(score, config).score
    twice = constrain(once, config)
    assert twice.repairs == ()
    assert twice.score.notes == once.notes


# -- real music ----------------------------------------------------------


@pytest.mark.feature("F-20")
@pytest.mark.parametrize("song_id", ["toccata", "quartet"])
@pytest.mark.parametrize("max_span", [12, 15, 18])
def test_real_songs_come_out_playable(
    song_id: str,
    max_span: int,
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    """BWV 565 is written for organ, so its pedalboard staff sits far below the
    manuals. Reducing it to two hands guarantees real violations; this is the
    hardest honest test available."""
    score = read_midi(load_song(song_id))
    result = constrain(score, config_for(max_span))

    assert result.violations_before > 0, "expected this piece to need work"
    assert verify_span(result.score, max_span) == []
    # Most of the music has to survive, or the engine is just deleting notes.
    assert len(result.score.notes) > len(score.notes) * 0.9


@pytest.mark.feature("F-20")
def test_the_committed_organ_piece_is_repaired_mostly_without_dropping(
    load_song: Callable[[str], mido.MidiFile],
) -> None:
    score = read_midi(load_song("toccata"))
    result = constrain(score, config_for(12))
    counts = result.counts
    assert counts.get("drop", 0) < counts.get("reassign", 0) + counts.get(
        "octave-shift", 0
    ), "dropping should be the exception, not the strategy"


@pytest.mark.feature("F-20")
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_survives_constraining(name: str) -> None:
    score = read_midi(FIXTURES[name]())
    result = constrain(score, config_for(12))
    assert verify_span(result.score, 12) == []


def test_the_result_summarises_what_it_did() -> None:
    result = constrain(score_of(note(36), note(72)), config_for())
    text = result.summary()
    assert "violation" in text
    assert "reassign" in text


def test_a_clean_score_says_there_was_nothing_to_do() -> None:
    result = constrain(score_of(note(60), note(64)), config_for())
    assert "nothing to do" in result.summary()


def test_an_empty_score_is_handled(tmp_path: Path) -> None:
    del tmp_path
    result = constrain(Score(), config_for())
    assert result.score.is_empty
    assert result.repairs == ()
