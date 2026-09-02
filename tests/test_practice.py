"""The practice settings: tempo, section, count-in, metronome, one hand.

What ties these together is what they must *not* do. None of them may change the
arrangement. A piece practised at half speed, one hand, from bar 20, has to be
the same notes in the same order as the whole thing at full speed, or the
practice was of something other than the piece.
"""

from __future__ import annotations

import itertools

import pytest

from psv.config import PracticeConfig
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Pedal, PedalEvent, Score
from psv.practice import (
    bar_window,
    click_times,
    count_in_seconds,
    for_hand,
    prepare,
    time_scaled,
)
from psv.tempo import TempoMap
from tests.fixtures.midi_builder import FIXTURES

TAIL = 1.0


def two_hand_score() -> Score:
    return read_midi(FIXTURES["two-hands"]())


# -- tempo ---------------------------------------------------------------


@pytest.mark.feature("F-51")
def test_half_speed_takes_twice_as_long() -> None:
    score = two_hand_score()
    slowed = time_scaled(score, 0.5)
    assert slowed.duration == pytest.approx(score.duration * 2)


@pytest.mark.feature("F-51")
def test_slowing_down_changes_no_pitch_and_drops_no_note() -> None:
    """The whole point: the same piece, arriving more slowly."""
    score = two_hand_score()
    slowed = time_scaled(score, 0.6)
    assert [note.pitch for note in slowed.notes] == [note.pitch for note in score.notes]
    assert [note.velocity for note in slowed.notes] == [
        note.velocity for note in score.notes
    ]
    assert [note.hand for note in slowed.notes] == [note.hand for note in score.notes]


@pytest.mark.feature("F-51")
def test_the_tempo_map_is_scaled_with_the_notes() -> None:
    """If it were not, the beat and bar lines would drift off the notes, which
    is the one thing the grid exists to prevent."""
    score = time_scaled(two_hand_score(), 0.5)
    assert score.tempo_map.bpm_at(0.0) == pytest.approx(60.0, rel=1e-5)
    for bar, seconds in ((1, 0.0), (2, 4.0), (3, 8.0)):
        assert score.meter.bar_start(bar) == pytest.approx(seconds, abs=1e-3)


@pytest.mark.feature("F-51")
def test_pedalling_is_scaled_too() -> None:
    score = read_midi(FIXTURES["sustain-pedal"]())
    assert score.pedals
    slowed = time_scaled(score, 0.5)
    for original, scaled in zip(score.pedals, slowed.pedals, strict=True):
        assert scaled.start == pytest.approx(original.start * 2)
        assert scaled.end == pytest.approx(original.end * 2)
        assert scaled.depth == original.depth


@pytest.mark.feature("F-51")
def test_full_speed_returns_the_same_score_untouched() -> None:
    score = two_hand_score()
    assert time_scaled(score, 1.0) is score


def test_a_nonpositive_tempo_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        time_scaled(two_hand_score(), 0.0)


@pytest.mark.feature("F-51")
def test_scaling_a_score_with_a_tempo_change_keeps_the_change() -> None:
    score = read_midi(FIXTURES["tempo-changes"]())
    slowed = time_scaled(score, 0.5)
    assert len(slowed.tempo_map.changes) == len(score.tempo_map.changes)
    for original, scaled in zip(
        score.tempo_map.changes, slowed.tempo_map.changes, strict=True
    ):
        assert scaled.bpm == pytest.approx(original.bpm / 2, rel=1e-5)


# -- one hand ------------------------------------------------------------


@pytest.mark.feature("F-54")
def test_one_hand_keeps_only_that_hand() -> None:
    score = two_hand_score()
    left = for_hand(score, Hand.LEFT)
    assert left.notes
    assert {note.hand for note in left.notes} == {Hand.LEFT}
    assert len(left.notes) < len(score.notes)


@pytest.mark.feature("F-54")
def test_one_hand_keeps_the_pedalling() -> None:
    """The pedal is shared between the hands, and a passage practised without it
    sounds wrong."""
    score = read_midi(FIXTURES["sustain-pedal"]())
    score = score.with_notes(
        note.assigned_to(Hand.LEFT if note.pitch < 60 else Hand.RIGHT)
        for note in score.notes
    )
    assert for_hand(score, Hand.RIGHT).pedals == score.pedals


@pytest.mark.feature("F-54")
def test_asking_for_a_hand_nothing_is_assigned_to_gives_an_empty_score() -> None:
    """A raw MIDI that has not been arranged has no hands at all. Better an
    empty score the caller can notice than a silent wrong answer."""
    score = read_midi(FIXTURES["single-note"]())
    assert for_hand(score, Hand.LEFT).is_empty


# -- section practice ----------------------------------------------------


@pytest.mark.feature("F-52")
def test_a_bar_range_covers_both_ends() -> None:
    """Bars 2 to 3 of four-four at 120 bpm: two bars of two seconds each."""
    start, duration = bar_window(two_hand_score(), 2, 3, tail=TAIL)
    assert start == pytest.approx(2.0)
    assert duration == pytest.approx(4.0 + TAIL)


@pytest.mark.feature("F-52")
def test_a_single_bar_is_a_range_of_one() -> None:
    start, duration = bar_window(two_hand_score(), 3, 3, tail=TAIL)
    assert start == pytest.approx(4.0)
    assert duration == pytest.approx(2.0 + TAIL)


@pytest.mark.feature("F-52")
def test_a_bar_range_follows_a_meter_change() -> None:
    """4/4 to 3/4 at beat 8. Bar 3 is the first three-four bar, so bars 3-4 are
    three seconds, not four."""
    score = read_midi(FIXTURES["time-signatures"]())
    start, duration = bar_window(score, 3, 4, tail=0.0)
    assert start == pytest.approx(4.0)
    assert duration == pytest.approx(3.0)


def test_a_backwards_bar_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="runs backwards"):
        bar_window(two_hand_score(), 9, 2, tail=TAIL)


def test_bar_zero_is_rejected() -> None:
    with pytest.raises(ValueError, match="numbered from 1"):
        bar_window(two_hand_score(), 0, 4, tail=TAIL)


# -- count-in and metronome ----------------------------------------------


@pytest.mark.feature("F-53")
def test_a_count_in_is_a_whole_bar_of_silence_at_the_playing_tempo() -> None:
    score = two_hand_score()
    assert count_in_seconds(score, 0.0, 2) == pytest.approx(4.0)
    assert count_in_seconds(score, 0.0, 0) == pytest.approx(0.0)


@pytest.mark.feature("F-53")
def test_a_count_in_uses_the_tempo_where_the_music_starts() -> None:
    """Counting into bar 40 of a piece that has slowed down has to count at the
    speed you are about to play, not the speed of bar 1."""
    score = read_midi(FIXTURES["tempo-changes"]())
    at_60_bpm = count_in_seconds(score, 0.0, 1)
    at_180_bpm = count_in_seconds(score, score.duration - 0.1, 1)
    assert at_60_bpm > at_180_bpm


@pytest.mark.feature("F-53")
def test_count_in_clicks_run_ahead_of_the_music_and_accent_the_downbeat() -> None:
    clicks = click_times(two_hand_score(), music_start=0.0, end=0.0, count_in_bars=1)
    assert [click.time for click in clicks] == pytest.approx([-2.0, -1.5, -1.0, -0.5])
    assert [click.accent for click in clicks] == [True, False, False, False]


@pytest.mark.feature("F-53")
def test_the_metronome_accents_every_downbeat_and_nothing_else() -> None:
    clicks = click_times(two_hand_score(), music_start=0.0, end=4.0, metronome=True)
    accented = [click.time for click in clicks if click.accent]
    assert accented == pytest.approx([0.0, 2.0])
    assert len(clicks) == 8


@pytest.mark.feature("F-53")
def test_the_metronome_follows_a_tempo_change() -> None:
    """Beats get shorter part way through, so the clicks must get closer
    together. Clicks spaced in seconds would slide off the beat."""
    score = read_midi(FIXTURES["tempo-changes"]())
    clicks = click_times(score, music_start=0.0, end=6.0, metronome=True)
    gaps = [
        round(later.time - earlier.time, 6)
        for earlier, later in itertools.pairwise(clicks)
    ]
    assert len(set(gaps)) > 1


@pytest.mark.feature("F-53")
def test_no_clicks_when_neither_is_asked_for() -> None:
    assert click_times(two_hand_score(), music_start=0.0, end=10.0) == ()


# -- the four together ---------------------------------------------------


@pytest.mark.feature("F-52")
def test_a_section_at_a_slower_tempo_is_measured_in_the_slowed_time() -> None:
    """Bars are musical positions, so bar 2 at half speed is twice as far in as
    bar 2 at full speed, and just as long."""
    score = two_hand_score()
    full = prepare(score, PracticeConfig(), bars=(2, 3), tail=TAIL)
    half = prepare(score, PracticeConfig(tempo=0.5), bars=(2, 3), tail=TAIL)
    assert half.start == pytest.approx(full.start * 2)
    assert half.duration is not None and full.duration is not None
    assert half.duration - TAIL == pytest.approx((full.duration - TAIL) * 2)


@pytest.mark.feature("F-53")
def test_the_count_in_opens_the_window_early_rather_than_moving_the_music() -> None:
    """The notes keep the times they had. If the count-in shifted the score
    instead, the picture and the soundtrack could disagree about where the music
    starts."""
    score = two_hand_score()
    plain = prepare(score, PracticeConfig(), bars=(2, 2), tail=TAIL)
    counted = prepare(score, PracticeConfig(count_in_bars=1), bars=(2, 2), tail=TAIL)
    assert counted.start == pytest.approx(plain.start - 2.0)
    assert counted.duration is not None and plain.duration is not None
    assert counted.duration == pytest.approx(plain.duration + 2.0)
    assert counted.score.notes == plain.score.notes


@pytest.mark.feature("F-54")
def test_the_focused_hand_is_the_one_that_sounds_and_both_are_drawn() -> None:
    score = two_hand_score()
    show = prepare(score, PracticeConfig(hands="right"), tail=TAIL)
    assert show.focus is Hand.RIGHT
    assert {note.hand for note in show.audio_score.notes} == {Hand.RIGHT}
    assert len(show.score.notes) == len(score.notes)


def test_both_hands_means_no_focus_and_no_filtering() -> None:
    show = prepare(two_hand_score(), PracticeConfig(), tail=TAIL)
    assert show.focus is None
    assert show.audio_score is show.score


def test_default_settings_leave_the_window_open_and_say_nothing() -> None:
    show = prepare(two_hand_score(), PracticeConfig(), tail=TAIL)
    assert show.start == 0.0
    assert show.duration is None
    assert show.clicks == ()
    assert show.label == ""


def test_the_label_names_every_setting_that_was_applied() -> None:
    show = prepare(
        two_hand_score(),
        PracticeConfig(tempo=0.75, hands="left", count_in_bars=2, metronome=True),
        bars=(20, 40),
        tail=TAIL,
    )
    assert show.label == (
        "0.75x tempo, bars 20-40, left hand, 2-bar count-in, metronome"
    )


def test_a_single_bar_is_labelled_as_one_bar() -> None:
    show = prepare(two_hand_score(), PracticeConfig(), bars=(31, 31), tail=TAIL)
    assert show.label == "bar 31"


def test_an_empty_score_survives_every_setting() -> None:
    """Nothing here may divide by a duration that is zero."""
    empty = Score(
        parts=(Part(),),
        tempo_map=TempoMap.constant(480, 120.0),
    )
    show = prepare(
        empty,
        PracticeConfig(tempo=0.5, hands="left", count_in_bars=1, metronome=True),
        tail=TAIL,
    )
    assert show.audio_score.is_empty
    assert show.clicks


def test_a_metronome_over_a_zero_length_window_produces_nothing() -> None:
    score = two_hand_score()
    assert click_times(score, music_start=5.0, end=5.0, metronome=True) == ()


def test_scaling_keeps_a_pedal_event_valid() -> None:
    """PedalEvent rejects an end before its start, so a scale that reordered
    them would raise rather than produce a bad score."""
    score = Score(
        parts=(Part(notes=(Note(pitch=60, start=0.0, end=1.0),)),),
        pedals=(PedalEvent(Pedal.SUSTAIN, 0.0, 1.0, 64),),
    )
    scaled = time_scaled(score, 0.25)
    assert scaled.pedals[0].end == pytest.approx(4.0)
