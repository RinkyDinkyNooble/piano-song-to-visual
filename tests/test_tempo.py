"""Tempo map arithmetic, checked against hand-computed values."""

from __future__ import annotations

import pytest

from psv.tempo import DEFAULT_US_PER_BEAT, Meter, TempoMap, TimeSignature

TPB = 480


def test_default_tempo_is_120_bpm() -> None:
    tempo_map = TempoMap.from_changes(TPB, [])
    assert tempo_map.is_constant
    assert tempo_map.changes[0].us_per_beat == DEFAULT_US_PER_BEAT
    assert tempo_map.changes[0].bpm == pytest.approx(120.0)


def test_one_beat_at_120_bpm_is_half_a_second() -> None:
    tempo_map = TempoMap.constant(TPB, 120.0)
    assert tempo_map.tick_to_seconds(TPB) == pytest.approx(0.5)
    assert tempo_map.tick_to_seconds(4 * TPB) == pytest.approx(2.0)


def test_one_beat_at_60_bpm_is_one_second() -> None:
    tempo_map = TempoMap.constant(TPB, 60.0)
    assert tempo_map.tick_to_seconds(TPB) == pytest.approx(1.0)


@pytest.mark.feature("F-05")
def test_tempo_change_shifts_only_later_time() -> None:
    """Four beats at 60 BPM (4s), then four at 120 BPM (2s), totalling 6s."""
    tempo_map = TempoMap.from_changes(TPB, [(0, 1_000_000), (4 * TPB, 500_000)])
    assert tempo_map.tick_to_seconds(0) == pytest.approx(0.0)
    assert tempo_map.tick_to_seconds(4 * TPB) == pytest.approx(4.0)
    assert tempo_map.tick_to_seconds(8 * TPB) == pytest.approx(6.0)


@pytest.mark.feature("F-05")
def test_seconds_to_tick_inverts_tick_to_seconds() -> None:
    tempo_map = TempoMap.from_changes(
        TPB, [(0, 1_000_000), (4 * TPB, 500_000), (10 * TPB, 250_000)]
    )
    for tick in (0, 100, TPB, 4 * TPB, 7 * TPB, 10 * TPB, 25 * TPB):
        seconds = tempo_map.tick_to_seconds(tick)
        assert tempo_map.seconds_to_tick(seconds) == pytest.approx(tick, abs=1)


def test_unsorted_input_is_ordered() -> None:
    tempo_map = TempoMap.from_changes(TPB, [(4 * TPB, 500_000), (0, 1_000_000)])
    assert [c.tick for c in tempo_map.changes] == [0, 4 * TPB]


def test_missing_tempo_at_tick_zero_is_filled_with_the_default() -> None:
    tempo_map = TempoMap.from_changes(TPB, [(4 * TPB, 250_000)])
    assert tempo_map.changes[0].tick == 0
    assert tempo_map.changes[0].us_per_beat == DEFAULT_US_PER_BEAT


def test_two_tempi_on_the_same_tick_keeps_the_later_one() -> None:
    tempo_map = TempoMap.from_changes(TPB, [(0, 1_000_000), (0, 250_000)])
    assert len(tempo_map.changes) == 1
    assert tempo_map.changes[0].us_per_beat == 250_000


def test_bpm_at_reports_the_tempo_in_force() -> None:
    tempo_map = TempoMap.from_changes(TPB, [(0, 1_000_000), (4 * TPB, 500_000)])
    assert tempo_map.bpm_at(0.0) == pytest.approx(60.0)
    assert tempo_map.bpm_at(3.9) == pytest.approx(60.0)
    assert tempo_map.bpm_at(4.0) == pytest.approx(120.0)
    assert tempo_map.bpm_at(100.0) == pytest.approx(120.0)


@pytest.mark.feature("F-05")
def test_beat_times_stay_on_the_beat_through_a_tempo_change() -> None:
    """The renderer's vertical grid comes from this.

    Beats 0-3 last a second each, then beats 4 onward last half a second. Lines
    spaced evenly in seconds would drift; these must not.
    """
    tempo_map = TempoMap.from_changes(TPB, [(0, 1_000_000), (4 * TPB, 500_000)])
    times = list(tempo_map.beat_times(until_seconds=6.0))
    assert times == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0, 4.5, 5.0, 5.5, 6.0])


def test_beat_times_rejects_a_nonpositive_step() -> None:
    tempo_map = TempoMap.constant(TPB)
    with pytest.raises(ValueError, match="step must be positive"):
        list(tempo_map.beat_times(1.0, step=0.0))


def test_invalid_ticks_per_beat_is_rejected() -> None:
    with pytest.raises(ValueError, match="ticks_per_beat must be positive"):
        TempoMap.from_changes(0, [])


def test_nonpositive_tempo_is_rejected() -> None:
    with pytest.raises(ValueError, match="not positive"):
        TempoMap.from_changes(TPB, [(0, 0)])


def test_time_signature_bar_length_in_quarter_notes() -> None:
    assert TimeSignature(0, 0.0, 4, 4).beats_per_bar == pytest.approx(4.0)
    assert TimeSignature(0, 0.0, 3, 4).beats_per_bar == pytest.approx(3.0)
    assert TimeSignature(0, 0.0, 7, 8).beats_per_bar == pytest.approx(3.5)
    assert TimeSignature(0, 0.0, 6, 8).beats_per_bar == pytest.approx(3.0)


# -- the bar index -------------------------------------------------------


def meter_of(*signatures: TimeSignature, tempo_map: TempoMap | None = None) -> Meter:
    return Meter.from_score_data(tempo_map or TempoMap.constant(TPB, 120.0), signatures)


def test_a_score_with_no_time_signature_is_assumed_to_be_four_four() -> None:
    meter = meter_of()
    assert meter.bar_beats(1) == pytest.approx(4.0)
    assert meter.bar_start(2) == pytest.approx(2.0)


def test_bars_are_numbered_from_one() -> None:
    meter = meter_of(TimeSignature(0, 0.0, 4, 4))
    assert meter.bar_start(1) == pytest.approx(0.0)
    assert meter.bar_at(0.0) == 1
    with pytest.raises(ValueError, match="numbered from 1"):
        meter.bar_start(0)


def test_bar_length_follows_the_meter() -> None:
    """Three-four bars are three quarter notes; seven-eight is three and a half."""
    assert meter_of(TimeSignature(0, 0.0, 3, 4)).bar_start(3) == pytest.approx(3.0)
    assert meter_of(TimeSignature(0, 0.0, 7, 8)).bar_start(3) == pytest.approx(3.5)


def test_a_meter_change_starts_a_new_bar() -> None:
    """4/4 for two bars, then 3/4. Bar 3 is the first three-four bar."""
    meter = meter_of(TimeSignature(0, 0.0, 4, 4), TimeSignature(8 * TPB, 4.0, 3, 4))
    assert [meter.bar_start(bar) for bar in (1, 2, 3, 4)] == pytest.approx(
        [0.0, 2.0, 4.0, 5.5]
    )
    assert meter.bar_at(4.0) == 3
    assert meter.bar_beats(3) == pytest.approx(3.0)


def test_a_meter_change_part_way_through_a_bar_still_ends_it() -> None:
    """The change lands three beats into a four-four bar. Those three beats are
    bar 2 in their own right, not the first three beats of the new meter."""
    meter = meter_of(TimeSignature(0, 0.0, 4, 4), TimeSignature(7 * TPB, 3.5, 3, 4))
    assert meter.bar_start(2) == pytest.approx(2.0)
    assert meter.bar_start(3) == pytest.approx(3.5)
    assert meter.bar_at(3.0) == 2


def test_bar_lines_stay_on_the_bars_through_a_tempo_change() -> None:
    """Beats 0-3 last a second, then half a second. Bar 2 is at 4 s, not 2 s."""
    tempo_map = TempoMap.from_changes(TPB, [(0, 1_000_000), (4 * TPB, 500_000)])
    meter = meter_of(TimeSignature(0, 0.0, 4, 4), tempo_map=tempo_map)
    assert [seconds for _, seconds in meter.bar_times(7.0)] == pytest.approx(
        [0.0, 4.0, 6.0]
    )


def test_bar_times_can_skip_to_the_bars_in_view() -> None:
    """The renderer asks per frame, so it must not count from bar 1 each time."""
    meter = meter_of(TimeSignature(0, 0.0, 4, 4))
    assert list(meter.bar_times(13.0, since_seconds=9.0)) == [
        (6, 10.0),
        (7, 12.0),
    ]


def test_two_time_signatures_on_one_tick_keep_the_later_one() -> None:
    meter = meter_of(TimeSignature(0, 0.0, 4, 4), TimeSignature(0, 0.0, 3, 4))
    assert meter.bar_beats(1) == pytest.approx(3.0)
