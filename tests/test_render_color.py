"""Colour: hue for the hand, brightness for the loudness.

Two signals share one bar, so the tests here are mostly about them staying out
of each other's way. Hue must survive down to pianissimo, brightness must stay
monotonic, and the black-key darkening must compose with both rather than
replacing them.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from psv.config import ColorConfig, Config, ConfigError, VisualConfig
from psv.model import Hand, Note
from psv.render.color import (
    blend,
    hand_color,
    is_grayscale,
    note_color,
    parse_hex,
    pedal_color,
    scale,
    velocity_brightness,
)

COLORS = ColorConfig()
DARKEN = 0.2


def note(pitch: int = 60, velocity: int = 64, hand: Hand = Hand.RIGHT) -> Note:
    return Note(pitch=pitch, start=0.0, end=1.0, velocity=velocity, hand=hand)


def luma(colour: tuple[int, int, int]) -> float:
    red, green, blue = colour
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


# -- hue says which hand -------------------------------------------------


@pytest.mark.feature("F-27")
def test_the_hands_get_visibly_different_colours() -> None:
    left = hand_color(Hand.LEFT, COLORS)
    right = hand_color(Hand.RIGHT, COLORS)
    assert left != right
    # Far enough apart to tell at a glance, not merely unequal.
    assert sum(abs(a - b) for a, b in zip(left, right, strict=True)) > 60


@pytest.mark.feature("F-27")
def test_an_unassigned_note_is_neither_hand_s_colour() -> None:
    """A score that skipped hand assignment should look like it did, rather
    than being silently attributed to one hand."""
    neutral = hand_color(Hand.UNASSIGNED, COLORS)
    assert neutral != hand_color(Hand.LEFT, COLORS)
    assert neutral != hand_color(Hand.RIGHT, COLORS)


@pytest.mark.feature("F-27")
@pytest.mark.parametrize("velocity", [1, 20, 64, 100, 127])
def test_the_hands_stay_distinguishable_at_every_dynamic(velocity: int) -> None:
    """The reason saturation is left alone.

    Washing quiet notes toward grey would look nicer and would cost the more
    important signal: at pianissimo you could no longer tell the hands apart.
    """
    left = note_color(note(velocity=velocity, hand=Hand.LEFT), COLORS, DARKEN)
    right = note_color(note(velocity=velocity, hand=Hand.RIGHT), COLORS, DARKEN)
    assert sum(abs(a - b) for a, b in zip(left, right, strict=True)) > 20


# -- brightness says how loud --------------------------------------------


@pytest.mark.feature("F-28")
def test_brightness_spans_exactly_the_configured_range() -> None:
    assert velocity_brightness(1, COLORS) == pytest.approx(COLORS.quiet)
    assert velocity_brightness(127, COLORS) == pytest.approx(COLORS.loud)


@pytest.mark.feature("F-28")
def test_brightness_never_decreases_as_velocity_rises() -> None:
    values = [velocity_brightness(v, COLORS) for v in range(1, 128)]
    assert all(a <= b for a, b in pairwise(values))
    assert values[0] < values[-1], "the range should not be flat"


@pytest.mark.feature("F-28")
def test_note_brightness_is_monotonic_across_the_whole_velocity_range() -> None:
    """Not just the multiplier: the pixels that come out of it."""
    lumas = [luma(note_color(note(velocity=v), COLORS, DARKEN)) for v in range(1, 128)]
    assert all(a <= b for a, b in pairwise(lumas))
    assert lumas[-1] > lumas[0] * 2, "ff should be plainly brighter than pp"


@pytest.mark.feature("F-28")
def test_no_channel_ever_clips() -> None:
    """Colours are built by multiplying toward black, so 255 is unreachable
    from above however the config is set."""
    loud = replace(COLORS, quiet=1.0, loud=1.0)
    for hand in (Hand.LEFT, Hand.RIGHT, Hand.UNASSIGNED):
        for velocity in (1, 64, 127):
            colour = note_color(note(velocity=velocity, hand=hand), loud, 0.0)
            assert all(0 <= channel <= 255 for channel in colour)


@pytest.mark.feature("F-28")
def test_velocities_outside_the_midi_range_are_clamped() -> None:
    assert velocity_brightness(0, COLORS) == velocity_brightness(1, COLORS)
    assert velocity_brightness(999, COLORS) == velocity_brightness(127, COLORS)


@pytest.mark.feature("F-28")
def test_the_brightness_range_is_configurable() -> None:
    flat = replace(COLORS, quiet=0.8, loud=0.8)
    assert velocity_brightness(1, flat) == pytest.approx(0.8)
    assert velocity_brightness(127, flat) == pytest.approx(0.8)


# -- black keys ----------------------------------------------------------


@pytest.mark.feature("F-30")
def test_a_black_key_is_darker_than_the_white_key_beside_it() -> None:
    white = note_color(note(pitch=60), COLORS, DARKEN)
    black = note_color(note(pitch=61), COLORS, DARKEN)
    assert luma(black) < luma(white)


@pytest.mark.feature("F-30")
def test_the_darkening_composes_with_the_dynamics_colour() -> None:
    """Applied on top of the velocity colour, not instead of it: a loud black
    key still reads louder than a quiet one."""
    quiet_black = note_color(note(pitch=61, velocity=10), COLORS, DARKEN)
    loud_black = note_color(note(pitch=61, velocity=120), COLORS, DARKEN)
    assert luma(loud_black) > luma(quiet_black)


@pytest.mark.feature("F-30")
def test_the_darkening_keeps_the_hand_s_hue() -> None:
    """Darker, not a different colour. Hand identity has to survive it."""
    left = note_color(note(pitch=61, hand=Hand.LEFT), COLORS, DARKEN)
    right = note_color(note(pitch=61, hand=Hand.RIGHT), COLORS, DARKEN)
    assert left != right


@pytest.mark.feature("F-30")
def test_zero_darkening_leaves_a_black_key_alone() -> None:
    assert note_color(note(pitch=61), COLORS, 0.0) == note_color(
        note(pitch=60), COLORS, 0.0
    )


@pytest.mark.feature("F-30")
@pytest.mark.parametrize("darkening", [0.0, 0.2, 0.5, 1.0])
def test_more_darkening_is_always_darker(darkening: float) -> None:
    baseline = luma(note_color(note(pitch=61), COLORS, 0.0))
    assert luma(note_color(note(pitch=61), COLORS, darkening)) <= baseline


# -- pedals --------------------------------------------------------------


@pytest.mark.feature("F-31")
def test_pedal_depth_is_shown_the_way_loudness_is() -> None:
    """Half-pedalling is real technique and the parser keeps the depth, so a
    shallow press has to look different from a full one."""
    shallow = pedal_color(20, COLORS)
    full = pedal_color(127, COLORS)
    assert luma(shallow) < luma(full)


@pytest.mark.feature("F-31")
def test_pedal_colour_is_not_a_hand_colour() -> None:
    assert pedal_color(127, COLORS) != hand_color(Hand.LEFT, COLORS)
    assert pedal_color(127, COLORS) != hand_color(Hand.RIGHT, COLORS)


# -- the background ------------------------------------------------------


@pytest.mark.feature("F-35")
def test_the_default_background_is_grayscale() -> None:
    assert is_grayscale(parse_hex(VisualConfig().background))


@pytest.mark.feature("F-35")
def test_a_coloured_background_is_rejected() -> None:
    """The spec asks for grayscale, and it is right to: a hue back there
    competes with the hues carrying which-hand information."""
    with pytest.raises(ConfigError, match="grayscale"):
        replace(VisualConfig(), background="#204060").validate()


@pytest.mark.feature("F-35")
@pytest.mark.parametrize("shade", ["#000000", "#101010", "#808080", "#ffffff"])
def test_any_grey_is_accepted(shade: str) -> None:
    replace(VisualConfig(), background=shade).validate()


@pytest.mark.feature("F-35")
def test_every_non_note_colour_in_the_default_palette_is_grey() -> None:
    """Keys, grid, lanes and rules are all neutral by design."""
    from psv.render.frame import Palette

    palette = Palette()
    for name in (
        "background",
        "white_key",
        "black_key",
        "key_edge",
        "strike_line",
        "grid",
        "lane",
        "lane_edge",
    ):
        colour = getattr(palette, name)
        assert is_grayscale(colour), f"{name} is tinted: {colour}"


# -- helpers -------------------------------------------------------------


def test_scaling_toward_black_is_bounded() -> None:
    assert scale((200, 100, 50), 0.0) == (0, 0, 0)
    assert scale((200, 100, 50), 1.0) == (200, 100, 50)


def test_blending_moves_toward_the_overlay() -> None:
    assert blend((0, 0, 0), (100, 100, 100), 0.0) == (0, 0, 0)
    assert blend((0, 0, 0), (100, 100, 100), 1.0) == (100, 100, 100)
    assert blend((0, 0, 0), (100, 100, 100), 0.5) == (50, 50, 50)


def test_blend_alpha_is_clamped() -> None:
    assert blend((0, 0, 0), (100, 100, 100), 5.0) == (100, 100, 100)
    assert blend((0, 0, 0), (100, 100, 100), -1.0) == (0, 0, 0)


def test_short_and_long_hex_agree() -> None:
    assert parse_hex("#abc") == parse_hex("#aabbcc")


@pytest.mark.feature("F-36")
def test_the_shipped_default_colours_all_validate() -> None:
    Config.load(None).visual.validate()
