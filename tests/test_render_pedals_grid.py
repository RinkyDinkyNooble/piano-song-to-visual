"""Pedal lanes and the alignment grid.

The two things the M2 renderer deliberately left out, and the two the spec asks
for most specifically: somewhere to see the pedal, and rules to align by.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import numpy as np
import pytest

from psv.config import VisualConfig
from psv.constraints import ensure_hands
from psv.midi import read_midi
from psv.model import Pedal, PedalEvent, Score
from psv.render.frame import Layout, lanes_for, render_frame
from tests.fixtures.midi_builder import FIXTURES
from tests.test_render_frame import (
    NO_LANES,
    PLAIN,
    PLAIN_BG,
    SMALL,
    geometry_for,
    painted,
)

LANE_BG = (26, 26, 26)
LANE_EDGE = (56, 56, 56)


# -- lane layout ---------------------------------------------------------


@pytest.mark.feature("F-32")
def test_lanes_are_dropped_from_the_left_as_the_count_shrinks() -> None:
    """Lanes sit in the order the pedals sit under your feet. With one lane you
    get the sustain pedal: the one MIDI reliably carries, and the one the spec
    says is the only one worth trusting."""
    assert lanes_for(0) == ()
    assert lanes_for(1) == (Pedal.SUSTAIN,)
    assert lanes_for(2) == (Pedal.SOSTENUTO, Pedal.SUSTAIN)
    assert lanes_for(3) == (Pedal.SOFT, Pedal.SOSTENUTO, Pedal.SUSTAIN)


@pytest.mark.feature("F-32")
@pytest.mark.parametrize("lanes", [0, 1, 2, 3])
def test_the_keyboard_shrinks_to_make_room_for_the_lanes(lanes: int) -> None:
    layout = Layout.from_config(SMALL, lanes)
    assert len(layout.pedals) == lanes
    if lanes == 0:
        assert layout.keyboard_width == SMALL.width
    else:
        assert layout.keyboard_width < SMALL.width


@pytest.mark.feature("F-32")
def test_lanes_sit_right_of_the_keyboard_and_do_not_overlap() -> None:
    layout = Layout.from_config(SMALL, 3)
    spans = [layout.lane_span(pedal) for pedal in layout.pedals]
    assert spans[0][0] >= layout.keyboard_width
    for (_, right), (next_left, _) in pairwise(spans):
        assert right == pytest.approx(next_left)
    assert spans[-1][1] == pytest.approx(SMALL.width)


@pytest.mark.feature("F-32")
def test_with_no_lanes_the_keyboard_uses_the_whole_frame() -> None:
    score = read_midi(FIXTURES["three-pedals"]())
    frame = render_frame(score, SMALL, 2.0, pedal_lanes=0)
    layout = Layout.from_config(SMALL, 0)
    assert layout.keyboard_width == SMALL.width
    # The last column belongs to the keyboard, not a lane. It is the edge line
    # of the topmost white key, so check what it is not rather than how bright.
    rightmost = tuple(int(v) for v in frame[layout.keyboard_top + 4, -1])
    assert rightmost not in (LANE_BG, LANE_EDGE, PLAIN_BG)


# -- what the lanes show -------------------------------------------------


@pytest.mark.feature("F-31")
def test_a_held_pedal_lights_its_lane_footer_and_only_that_one() -> None:
    score = read_midi(FIXTURES["three-pedals"]())
    layout = Layout.from_config(SMALL, 3)
    frame = render_frame(score, SMALL, 2.0, pedal_lanes=3)
    row = layout.keyboard_top + 4

    active = {event.pedal for event in score.pedals if event.active_at(2.0)}
    assert active, "the fixture should have something down at t=2"
    assert active != set(layout.pedals), "and something up, or this proves little"

    for pedal in layout.pedals:
        left, right = layout.lane_span(pedal)
        colour = tuple(int(v) for v in frame[row, round((left + right) / 2)])
        if pedal in active:
            assert colour != LANE_EDGE, f"{pedal.name} should be lit"
        else:
            assert colour == LANE_EDGE, f"{pedal.name} should be dark"


@pytest.mark.feature("F-31")
def test_pedal_presses_fall_like_notes_do() -> None:
    """A press that has not arrived yet is drawn above the keyboard and
    descends as time advances, exactly as a note bar does."""
    score = Score(pedals=(PedalEvent(pedal=Pedal.SUSTAIN, start=2.5, end=3.0),))
    layout = Layout.from_config(SMALL, 1)
    left, right = layout.lane_span(Pedal.SUSTAIN)
    column = round((left + right) / 2)

    positions = []
    for time in (0.0, 0.5, 1.0, 1.5, 2.0):
        strip = render_frame(score, SMALL, time, pedal_lanes=1)[
            : layout.keyboard_top, column
        ]
        rows = np.flatnonzero(painted(strip, LANE_BG))
        positions.append(int(rows.max()) if rows.size else -1)

    assert positions == sorted(positions), f"pedal bar did not descend: {positions}"
    assert positions[-1] > positions[0]


@pytest.mark.feature("F-31")
def test_half_pedalling_is_visibly_dimmer_than_a_full_press() -> None:
    """Depth reaches the picture. The parser keeps it, so the renderer must use
    it or the whole half-pedal path is decoration."""
    layout = Layout.from_config(SMALL, 1)
    left, right = layout.lane_span(Pedal.SUSTAIN)
    column = round((left + right) / 2)
    row = layout.keyboard_top + 4

    def footer_brightness(depth: int) -> int:
        score = Score(
            pedals=(PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=2.0, depth=depth),)
        )
        frame = render_frame(score, SMALL, 1.0, pedal_lanes=1)
        return int(frame[row, column].max())

    assert footer_brightness(20) < footer_brightness(127)


@pytest.mark.feature("F-31")
def test_a_pedal_with_no_lane_is_not_drawn() -> None:
    """One lane means sustain only; a sostenuto press must not leak into it."""
    score = Score(pedals=(PedalEvent(pedal=Pedal.SOSTENUTO, start=0.0, end=2.0),))
    layout = Layout.from_config(SMALL, 1)
    left, right = layout.lane_span(Pedal.SUSTAIN)
    frame = render_frame(score, SMALL, 1.0, pedal_lanes=1)
    column = round((left + right) / 2)
    assert not painted(frame[: layout.keyboard_top, column], LANE_BG).any()


# -- pitch lines ---------------------------------------------------------


def grid_only(
    *, pitch_lines: str | None = None, beat_lines: str | None = None
) -> VisualConfig:
    """SMALL with only the named grid settings changed."""
    grid = SMALL.grid
    if pitch_lines is not None:
        grid = replace(grid, pitch_lines=pitch_lines)
    if beat_lines is not None:
        grid = replace(grid, beat_lines=beat_lines)
    return replace(SMALL, grid=grid)


@pytest.mark.feature("F-33")
def test_pitch_lines_land_on_octave_boundaries() -> None:
    """Vertical rules at every C, so a key can be found without counting."""
    config = grid_only(beat_lines="none")
    frame = render_frame(Score(), config, 0.0, pedal_lanes=NO_LANES)
    geometry = geometry_for(config)

    columns = set(np.flatnonzero(painted(frame[10], PLAIN_BG)).tolist())
    assert columns, "no pitch lines drawn"
    for pitch in range(24, 109, 12):
        assert round(geometry.key_span(pitch)[0]) in columns, f"no line at {pitch}"


@pytest.mark.feature("F-33")
def test_fifths_give_more_lines_than_octaves_and_none_gives_none() -> None:
    def line_count(mode: str) -> int:
        frame = render_frame(
            Score(),
            grid_only(beat_lines="none", pitch_lines=mode),
            0.0,
            pedal_lanes=NO_LANES,
        )
        return int(painted(frame[10], PLAIN_BG).sum())

    assert line_count("fifth") > line_count("octave") > line_count("none") == 0


# -- beat lines ----------------------------------------------------------


@pytest.mark.feature("F-34")
def test_beat_lines_stay_on_the_beat_through_a_tempo_change() -> None:
    """The reason the tempo map walks beats rather than stepping in seconds.

    tempo-changes runs 60 BPM for four beats and then 120. Rules spaced evenly
    in seconds would drift out of the music the moment the tempo moves.
    """
    config = grid_only(pitch_lines="none")
    score = read_midi(FIXTURES["tempo-changes"]())
    layout = Layout.from_config(config, NO_LANES)
    frame = render_frame(score, config, 2.0, pedal_lanes=NO_LANES)

    drawn = set(
        np.flatnonzero(painted(frame[: layout.keyboard_top, 5], PLAIN_BG)).tolist()
    )
    assert len(drawn) >= 2, "expected several beat lines on screen"

    for beat_time in score.tempo_map.beat_times(2.0 + config.lookahead_s):
        if beat_time < 2.0:
            continue
        row = round(layout.time_to_y(beat_time, 2.0))
        if 0 <= row < layout.keyboard_top:
            assert row in drawn, f"no beat line for beat at {beat_time:.2f}s"


@pytest.mark.feature("F-34")
def test_bar_lines_are_sparser_than_beat_lines() -> None:
    score = read_midi(FIXTURES["time-signatures"]())

    layout = Layout.from_config(SMALL, NO_LANES)

    def line_count(mode: str) -> int:
        frame = render_frame(
            score,
            grid_only(pitch_lines="none", beat_lines=mode),
            0.0,
            pedal_lanes=NO_LANES,
        )
        # Falling area only. The keyboard below it is painted regardless.
        return int(painted(frame[: layout.keyboard_top, 5], PLAIN_BG).sum())

    assert line_count("bar") < line_count("beat")
    assert line_count("none") == 0


@pytest.mark.feature("F-33")
def test_the_grid_is_faint_enough_to_read_notes_over() -> None:
    """An aid, not decoration. A grid competing with the bars would make the
    video harder to learn from, which is the opposite of the point."""
    frame = render_frame(Score(), SMALL, 0.0, pedal_lanes=NO_LANES)
    region = frame[:100]
    grid_pixels = region[painted(region, PLAIN_BG)]
    assert grid_pixels.size, "expected some grid"
    assert int(grid_pixels.max()) < 100, "grid should stay dim"


@pytest.mark.feature("F-33")
def test_zero_opacity_removes_the_grid_entirely() -> None:
    frame = render_frame(Score(), PLAIN, 0.0, pedal_lanes=NO_LANES)
    layout = Layout.from_config(PLAIN, NO_LANES)
    assert not painted(frame[: layout.keyboard_top], PLAIN_BG).any()


# -- everything is configurable ------------------------------------------


@pytest.mark.feature("F-36")
def test_swapping_the_hand_colours_changes_the_picture() -> None:
    score = ensure_hands(read_midi(FIXTURES["two-hands"]()))
    normal = render_frame(score, SMALL, 1.0)
    swapped = render_frame(
        score,
        replace(
            SMALL,
            colors=replace(
                SMALL.colors,
                left_hand=SMALL.colors.right_hand,
                right_hand=SMALL.colors.left_hand,
            ),
        ),
        1.0,
    )
    assert not np.array_equal(normal, swapped)


@pytest.mark.feature("F-36")
def test_the_black_key_bar_width_reaches_the_pixels() -> None:
    score = read_midi(FIXTURES["full-keyboard"]())
    thin = render_frame(score, replace(SMALL, black_key_bar_width=0.3), 2.0)
    wide = render_frame(score, replace(SMALL, black_key_bar_width=1.0), 2.0)
    assert painted(wide, PLAIN_BG).sum() > painted(thin, PLAIN_BG).sum()


@pytest.mark.feature("F-36")
@pytest.mark.parametrize("pitch_lines", ["octave", "fifth", "none"])
@pytest.mark.parametrize("beat_lines", ["beat", "bar", "none"])
@pytest.mark.parametrize("lanes", [0, 1, 3])
def test_every_visual_combination_renders(
    pitch_lines: str, beat_lines: str, lanes: int
) -> None:
    """A config matrix. None of these may crash or produce a blank frame."""
    score = read_midi(FIXTURES["three-pedals"]())
    config = grid_only(pitch_lines=pitch_lines, beat_lines=beat_lines)
    config.validate()
    frame = render_frame(score, config, 1.0, pedal_lanes=lanes)
    assert frame.shape == (SMALL.height, SMALL.width, 3)
    assert frame.max() > 0
