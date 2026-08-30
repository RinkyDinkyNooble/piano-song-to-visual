"""Keyboard layout.

Pure arithmetic, so this can be checked exhaustively over all 88 keys rather
than sampled. A geometry bug that slips through here becomes a picture that
looks subtly wrong and is miserable to diagnose.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from psv.model import HIGHEST_KEY, LOWEST_KEY, is_black_key
from psv.render.geometry import (
    WHITE_KEY_COUNT,
    KeyboardGeometry,
    white_index,
)

ALL_KEYS = range(LOWEST_KEY, HIGHEST_KEY + 1)
WIDTH = 1040  # 20 pixels per white key, so the arithmetic is easy to check


@pytest.fixture
def geometry() -> KeyboardGeometry:
    return KeyboardGeometry(width=WIDTH, height=100)


# -- white key indexing --------------------------------------------------


@pytest.mark.feature("F-11")
def test_an_88_key_piano_has_52_white_keys() -> None:
    whites = [p for p in ALL_KEYS if not is_black_key(p)]
    assert len(whites) == WHITE_KEY_COUNT == 52
    assert len(list(ALL_KEYS)) == 88


@pytest.mark.feature("F-11")
def test_white_indices_run_zero_to_51_without_gaps() -> None:
    indices = [white_index(p) for p in ALL_KEYS if not is_black_key(p)]
    assert indices == list(range(WHITE_KEY_COUNT))


@pytest.mark.feature("F-11")
def test_the_lowest_and_highest_keys_anchor_the_keyboard() -> None:
    assert white_index(LOWEST_KEY) == 0  # A0
    assert white_index(HIGHEST_KEY) == 51  # C8


@pytest.mark.feature("F-11")
def test_a_black_key_takes_the_index_of_the_white_below_it() -> None:
    assert white_index(61) == white_index(60)  # C#4 sits above C4
    assert white_index(66) == white_index(65)  # F#4 sits above F4


@pytest.mark.parametrize(
    ("pitch", "expected"),
    [(60, 23), (69, 28), (21, 0), (108, 51), (72, 30)],
)
def test_known_pitches_land_on_known_white_indices(pitch: int, expected: int) -> None:
    assert white_index(pitch) == expected


# -- key positions -------------------------------------------------------


@pytest.mark.feature("F-11")
def test_white_keys_tile_the_full_width_without_gaps_or_overlap(
    geometry: KeyboardGeometry,
) -> None:
    spans = [geometry.key_span(p) for p in geometry.white_pitches()]
    assert spans[0][0] == pytest.approx(0.0)
    assert spans[-1][1] == pytest.approx(WIDTH)
    for (_, right), (next_left, _) in pairwise(spans):
        assert right == pytest.approx(next_left)


@pytest.mark.feature("F-11")
def test_every_key_stays_inside_the_frame(geometry: KeyboardGeometry) -> None:
    for pitch in ALL_KEYS:
        left, right = geometry.key_span(pitch)
        assert 0 <= left < right <= WIDTH


@pytest.mark.feature("F-11")
def test_keys_are_ordered_left_to_right_by_pitch(geometry: KeyboardGeometry) -> None:
    centres = [geometry.key_centre(p) for p in ALL_KEYS]
    assert centres == sorted(centres)
    assert len(set(centres)) == 88, "two keys share a centre"


@pytest.mark.feature("F-11")
def test_a_black_key_straddles_the_seam_between_its_neighbours(
    geometry: KeyboardGeometry,
) -> None:
    """C#4 sits on the boundary between C4 and D4, overlapping both."""
    _, c_right = geometry.key_span(60)
    sharp_left, sharp_right = geometry.key_span(61)
    d_left, _ = geometry.key_span(62)

    assert c_right == pytest.approx(d_left)
    assert sharp_left < c_right < sharp_right
    assert geometry.key_centre(61) == pytest.approx(c_right)


@pytest.mark.feature("F-11")
def test_black_keys_are_narrower_and_shorter_than_white_ones(
    geometry: KeyboardGeometry,
) -> None:
    assert geometry.black_width < geometry.white_width
    assert geometry.black_height < geometry.height


@pytest.mark.feature("F-11")
def test_no_two_black_keys_overlap(geometry: KeyboardGeometry) -> None:
    spans = [geometry.key_span(p) for p in geometry.black_pitches()]
    for (_, right), (next_left, _) in pairwise(spans):
        assert right <= next_left


@pytest.mark.feature("F-11")
def test_only_keys_on_the_88_are_accepted(geometry: KeyboardGeometry) -> None:
    assert geometry.contains(LOWEST_KEY)
    assert geometry.contains(HIGHEST_KEY)
    assert not geometry.contains(LOWEST_KEY - 1)
    assert not geometry.contains(HIGHEST_KEY + 1)


# -- note bars -----------------------------------------------------------


@pytest.mark.feature("F-29")
def test_black_key_bars_are_thinner_than_white_key_bars(
    geometry: KeyboardGeometry,
) -> None:
    """From the spec: black-key notes must be distinguishable by shape from far
    up the screen, before their colour is easy to judge."""
    white_left, white_right = geometry.bar_span(60)
    black_left, black_right = geometry.bar_span(61)
    assert (black_right - black_left) < (white_right - white_left)


@pytest.mark.feature("F-29")
@pytest.mark.parametrize("ratio", [0.3, 0.5, 0.6, 0.8, 1.0])
def test_the_bar_ratio_means_a_fraction_of_a_white_bar(ratio: float) -> None:
    """The config documents `black_key_bar_width` as a fraction of a white bar.

    Measuring it from the narrower physical black key instead would shrink it
    twice and make the configured number wrong.
    """
    geometry = KeyboardGeometry(width=WIDTH, height=100, black_bar_ratio=ratio)
    white_left, white_right = geometry.bar_span(60)
    black_left, black_right = geometry.bar_span(61)
    measured = (black_right - black_left) / (white_right - white_left)
    assert measured == pytest.approx(ratio)


@pytest.mark.feature("F-11")
def test_bars_are_centred_on_their_key(geometry: KeyboardGeometry) -> None:
    for pitch in ALL_KEYS:
        left, right = geometry.bar_span(pitch)
        assert (left + right) / 2 == pytest.approx(geometry.key_centre(pitch))


@pytest.mark.feature("F-11")
def test_adjacent_white_bars_do_not_touch(geometry: KeyboardGeometry) -> None:
    """Without a gap, a chord of neighbouring notes renders as one wide block."""
    spans = [geometry.bar_span(p) for p in geometry.white_pitches()]
    for (_, right), (next_left, _) in pairwise(spans):
        assert right < next_left


@pytest.mark.feature("F-11")
def test_every_bar_is_at_least_a_pixel_wide_at_a_small_frame_size() -> None:
    """The debug loop renders at 320 wide; bars must not vanish there."""
    geometry = KeyboardGeometry(width=320, height=40)
    for pitch in ALL_KEYS:
        left, right = geometry.bar_span(pitch)
        assert right - left > 0


@pytest.mark.feature("F-11")
@pytest.mark.parametrize("width", [320, 640, 1280, 1920, 3840])
def test_layout_scales_with_frame_width(width: int) -> None:
    geometry = KeyboardGeometry(width=width, height=100)
    assert geometry.white_width == pytest.approx(width / 52)
    assert geometry.key_span(HIGHEST_KEY)[1] == pytest.approx(width)
