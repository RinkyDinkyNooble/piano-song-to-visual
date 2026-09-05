"""The theme layer: background gradients, border shade, bar gradients, themes.

Everything here changes how the picture looks and nothing changes what it says.
So the tests come in two kinds: the setting reaches the pixels, and the default
still draws exactly what it drew before. The second kind is the important one.
`render_frame` is pure, which is what lets "exactly" mean byte-for-byte.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from psv.config import Config, ConfigError, VisualConfig
from psv.model import Note, Part, Score
from psv.presets import THEME_DESCRIPTIONS, THEMES, apply_theme
from psv.render.color import parse_hex
from psv.render.frame import Layout, background_column, render_frame
from psv.render.geometry import KeyboardGeometry
from tests.test_render_frame import NO_LANES, SMALL, small_config

COOL = ("#0b1026", "#1b2350")


def long_note_score(pitch: int = 60) -> Score:
    """One note tall enough on screen to have a top and a bottom."""
    return Score(parts=(Part(notes=(Note(pitch=pitch, start=1.0, end=2.5),)),))


def bar_column(frame: np.ndarray, pitch: int, config: VisualConfig) -> np.ndarray:
    layout = Layout.from_config(config, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, config.height - layout.keyboard_top
    )
    return frame[: layout.keyboard_top, round(geometry.key_centre(pitch))]


def lit_pixels(
    frame: np.ndarray, pitch: int, config: VisualConfig
) -> list[tuple[int, ...]]:
    """The bar's own pixels down its middle, background excluded."""
    background = parse_hex(config.background)
    column = bar_column(frame, pitch, config)
    return [
        tuple(int(c) for c in pixel)
        for pixel in column
        if tuple(int(c) for c in pixel) != background
    ]


# -- the background gradient ---------------------------------------------


@pytest.mark.feature("F-71")
def test_a_gradient_background_runs_between_its_two_colours() -> None:
    """The grid is off here so the sampled rows are background and nothing else."""
    config = small_config(
        gradient_top=COOL[0],
        gradient_bottom=COOL[1],
        grid=replace(SMALL.grid, opacity=0.0),
    )
    frame = render_frame(Score(), config, 0.0, pedal_lanes=NO_LANES)

    assert tuple(int(c) for c in frame[0, 0]) == parse_hex(COOL[0])
    middle = int(frame[config.height // 2, 0, 2])
    assert middle > int(frame[0, 0, 2]), "the light end is not where it was put"


@pytest.mark.feature("F-71")
def test_no_gradient_leaves_the_flat_background_untouched() -> None:
    """Off means off, at the level of pixels rather than of looks."""
    score = long_note_score()
    plain = render_frame(score, SMALL, 0.6, pedal_lanes=NO_LANES)
    unset = render_frame(
        score,
        small_config(gradient_top="", gradient_bottom=""),
        0.6,
        pedal_lanes=NO_LANES,
    )
    assert np.array_equal(plain, unset)


@pytest.mark.feature("F-71")
def test_the_grid_stays_visible_at_both_ends_of_a_gradient() -> None:
    """Mixed with the background a row at a time. A grid colour worked out once
    against a nominal background would vanish into the dark end of a steep
    gradient and glare at the light end."""
    config = small_config(gradient_top="#000000", gradient_bottom="#b0b0b0")
    frame = render_frame(Score(), config, 0.0, pedal_lanes=NO_LANES)
    column = background_column(config, config.height)
    assert column is not None

    for row in (2, config.height // 2 - 2):
        line = frame[row]
        background = column[row]
        assert np.any(np.any(line != background, axis=-1)), f"no grid on row {row}"


@pytest.mark.feature("F-71")
def test_a_grid_crossing_is_no_brighter_than_the_lines_that_cross() -> None:
    """The grid is drawn, not composited. Compositing blends twice where a beat
    line meets a pitch line and leaves a brighter dot at every intersection."""
    config = small_config(gradient_top=COOL[0], gradient_bottom=COOL[1])
    frame = render_frame(long_note_score(), config, 0.0, pedal_lanes=NO_LANES)
    column = background_column(config, config.height)
    assert column is not None

    layout = Layout.from_config(config, NO_LANES)
    grid_rows = [
        row
        for row in range(layout.keyboard_top)
        if np.count_nonzero(np.any(frame[row] != column[row], axis=-1))
        > config.width // 2
    ]
    assert grid_rows, "no beat line found"
    row = grid_rows[0]
    shades = {tuple(int(c) for c in pixel) for pixel in frame[row]}
    assert len(shades) == 1, f"the beat line is not one colour: {sorted(shades)}"


@pytest.mark.feature("F-71")
@pytest.mark.parametrize(
    "body",
    ["[visual]\ngradient_top = '#101010'\n", "[visual]\ngradient_bottom = '#101010'\n"],
)
def test_half_a_gradient_is_an_error(tmp_path: Path, body: str) -> None:
    path = tmp_path / "psv.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match="go together"):
        Config.load(path)


@pytest.mark.feature("F-71")
def test_a_gradient_may_have_a_hue_where_the_flat_background_may_not() -> None:
    """The grayscale rule protects the practice default. Setting a gradient is
    itself the opt-in, so it is not subject to the same rule."""
    coloured = small_config(gradient_top="#140e28", gradient_bottom="#46205a")
    coloured.validate()

    with pytest.raises(ConfigError, match="grayscale"):
        replace(SMALL, background="#140e28").validate()


# -- the border shade ----------------------------------------------------


@pytest.mark.feature("F-72")
def test_the_default_border_shade_darkens_as_it_always_did() -> None:
    config = small_config(width=560, height=360, note_border=0.0016)
    assert config.note_border_shade == -0.45

    frame = render_frame(long_note_score(), config, 0.6, pedal_lanes=NO_LANES)
    lit = lit_pixels(frame, 60, config)
    brightest = max(lit, key=sum)
    darkest = min(lit, key=sum)
    assert sum(darkest) < sum(brightest), "no darker edge drawn"


@pytest.mark.feature("F-72")
def test_a_positive_border_shade_lights_the_edge_instead() -> None:
    """The outline going the other way is what makes a bar look lit from inside
    rather than cut out of the background."""
    config = small_config(width=560, height=360, note_border=0.0026)
    frame = render_frame(long_note_score(), config, 0.6, pedal_lanes=NO_LANES)
    dark_edge = min(lit_pixels(frame, 60, config), key=sum)

    lifted = replace(config, note_border_shade=0.7)
    frame = render_frame(long_note_score(), lifted, 0.6, pedal_lanes=NO_LANES)
    lit_edge = max(lit_pixels(frame, 60, lifted), key=sum)
    assert sum(lit_edge) > sum(dark_edge), "the edge is not the brightest part"


@pytest.mark.feature("F-72")
def test_a_border_shade_of_zero_leaves_no_visible_edge() -> None:
    """Zero is the bar's own colour, so the outline is there and invisible."""
    config = small_config(width=560, height=360, note_border=0.0026)
    plain = replace(config, note_border=0.0)
    same_shade = replace(config, note_border_shade=0.0)

    assert np.array_equal(
        render_frame(long_note_score(), plain, 0.6, pedal_lanes=NO_LANES),
        render_frame(long_note_score(), same_shade, 0.6, pedal_lanes=NO_LANES),
    )


# -- the bar gradient ----------------------------------------------------


@pytest.mark.feature("F-73")
def test_a_bar_gradient_of_zero_is_the_flat_fill() -> None:
    score = long_note_score()
    assert np.array_equal(
        render_frame(score, SMALL, 0.6, pedal_lanes=NO_LANES),
        render_frame(score, small_config(bar_gradient=0.0), 0.6, pedal_lanes=NO_LANES),
    )


@pytest.mark.feature("F-73")
@pytest.mark.parametrize("gradient", [0.6, -0.6])
def test_a_bar_gradient_ramps_along_the_bar(gradient: float) -> None:
    config = small_config(width=560, height=360, bar_gradient=gradient)
    frame = render_frame(long_note_score(), config, 0.6, pedal_lanes=NO_LANES)
    lit = lit_pixels(frame, 60, config)

    quarter, three_quarters = lit[len(lit) // 4], lit[3 * len(lit) // 4]
    if gradient > 0:
        assert sum(quarter) < sum(three_quarters), "the bright end is not the bottom"
    else:
        assert sum(quarter) > sum(three_quarters), "the bright end is not the top"


@pytest.mark.feature("F-73")
def test_a_bar_leaving_the_top_of_the_screen_keeps_its_gradient() -> None:
    """The ramp is worked out over the bar's whole extent and then clipped. If
    it were fitted to the visible part instead, a bar's shading would slide as
    it scrolled off, which is exactly the kind of motion nobody asked for."""
    config = small_config(width=560, height=360, bar_gradient=0.6)
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=6.0),)),))

    early = render_frame(score, config, 0.0, pedal_lanes=NO_LANES)
    later = render_frame(score, config, 3.0, pedal_lanes=NO_LANES)
    fully_visible = lit_pixels(early, 60, config)
    half_gone = lit_pixels(later, 60, config)
    assert fully_visible[-1] == half_gone[-1], "the bottom of the bar changed colour"


# -- themes --------------------------------------------------------------


@pytest.mark.feature("F-74")
@pytest.mark.parametrize("name", sorted(THEMES))
def test_every_theme_validates_and_changes_the_picture(name: str) -> None:
    base = replace(Config(), visual=SMALL)
    themed = apply_theme(base, name)
    themed.validate()

    score = long_note_score()
    assert not np.array_equal(
        render_frame(score, base.visual, 0.6, pedal_lanes=NO_LANES),
        render_frame(score, themed.visual, 0.6, pedal_lanes=NO_LANES),
    )


@pytest.mark.feature("F-74")
@pytest.mark.parametrize("name", sorted(THEMES))
def test_every_theme_keeps_the_hands_apart(name: str) -> None:
    """Hue carries which hand. A theme may change the colours; it may not spend
    the thing they are for."""
    colours = apply_theme(Config(), name).visual.colors
    left = parse_hex(colours.left_hand)
    right = parse_hex(colours.right_hand)
    distance = sum(abs(a - b) for a, b in zip(left, right, strict=True))
    assert distance > 120, f"{name} draws both hands nearly the same colour"


@pytest.mark.feature("F-74")
def test_every_theme_is_described() -> None:
    assert set(THEMES) == set(THEME_DESCRIPTIONS)


@pytest.mark.feature("F-74")
def test_an_unknown_theme_lists_the_real_ones() -> None:
    with pytest.raises(ConfigError, match="unknown theme"):
        apply_theme(Config(), "chartreuse")


@pytest.mark.feature("F-74")
def test_every_theme_can_be_written_out_as_a_config_file(tmp_path: Path) -> None:
    """A theme is a shortcut, not a capability. Anything one of them sets has to
    be something you could have typed yourself, or `--theme` becomes the only
    way to reach part of the renderer and the four of them become the whole
    palette anyone gets."""
    for name in sorted(THEMES):
        settings = dict(THEMES[name])
        colours = settings.pop("colors", {})
        lines = ["[visual]"]
        lines += [f"{key} = {value!r}" for key, value in sorted(settings.items())]
        lines += ["", "[visual.colors]"]
        lines += [f"{key} = {value!r}" for key, value in sorted(colours.items())]

        path = tmp_path / f"{name}.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert Config.load(path).visual == apply_theme(Config(), name).visual, name


@pytest.mark.feature("F-74")
def test_a_theme_leaves_everything_but_the_look_alone() -> None:
    """A preset changes how the piece is played, a theme only how it looks. The
    two compose because neither touches the other's settings."""
    base = Config()
    themed = apply_theme(base, "neon")
    assert themed.hands == base.hands
    assert themed.practice == base.practice
    assert themed.audio == base.audio
    assert themed.visual.width == base.visual.width


# -- rounded ends --------------------------------------------------------


def flat_bar_config(note_radius: float = 0.0) -> VisualConfig:
    """A bar with no border and no gradient, so it is one flat colour.

    Rounding is measured as area, and area is only readable when every pixel of
    the bar is the same colour. The border is a *shade* of that colour, so with
    it on, a row of pure border and a row of fill are two different brightnesses
    and coverage stops meaning what it says.
    """
    return replace(
        small_config(),
        width=1920,
        height=1080,
        note_border=0.0,
        note_radius=note_radius,
    )


def bar_row(frame: np.ndarray, row: int, pitch: int, config: VisualConfig) -> float:
    """How much of one row the bar covers, in whole pixels.

    Summed coverage rather than a count of non-background pixels: a rounded
    corner is antialiased, so its edge pixels are part bar and part background,
    and counting them whole would report a rounded end as full width.
    """
    layout = Layout.from_config(config, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, config.height - layout.keyboard_top
    )
    left, right = geometry.bar_span(pitch)
    background = np.array(parse_hex(config.background), dtype=np.float64)
    full = np.abs(
        np.array(parse_hex(config.colors.unassigned), dtype=np.float64) - background
    ).sum()
    strip = frame[row, round(left) : round(right)].astype(np.float64)
    distance = np.abs(strip - background).sum(axis=1)
    return float(np.clip(distance / full, 0.0, 1.0).sum())


@pytest.mark.feature("F-82")
def test_the_default_radius_leaves_the_bar_exactly_as_it_was() -> None:
    """The important one: rounding is opt-in and costs nothing when off."""
    score = long_note_score()
    config = small_config()
    assert config.note_radius == 0.0
    square = render_frame(score, config, 1.2, pedal_lanes=NO_LANES)
    explicit = render_frame(
        score, replace(config, note_radius=0.0), 1.2, pedal_lanes=NO_LANES
    )
    assert np.array_equal(square, explicit)


@pytest.mark.feature("F-82")
def test_rounding_narrows_the_ends_and_leaves_the_middle_alone() -> None:
    """A rounded bar is a bar with its corners taken off, nothing more."""
    score = long_note_score()
    config = flat_bar_config(note_radius=0.5)
    layout = Layout.from_config(config, NO_LANES)
    frame = render_frame(score, config, 1.2, pedal_lanes=NO_LANES)

    rows = [
        row
        for row in range(layout.keyboard_top)
        if bar_row(frame, row, 60, config) > 0.5
    ]
    assert len(rows) > 8, "need a bar tall enough to have ends and a middle"

    first, middle, last = rows[0], rows[len(rows) // 2], rows[-1]
    assert bar_row(frame, first, 60, config) < bar_row(frame, middle, 60, config)
    assert bar_row(frame, last, 60, config) < bar_row(frame, middle, 60, config)


@pytest.mark.feature("F-82")
def test_a_wider_radius_takes_more_off_the_bar() -> None:
    """Total covered area falls as the radius grows, and only at the ends."""
    score = long_note_score()
    base = flat_bar_config()
    layout = Layout.from_config(base, NO_LANES)

    areas = []
    for radius in (0.0, 0.1, 0.5):
        config = replace(base, note_radius=radius)
        frame = render_frame(score, config, 1.2, pedal_lanes=NO_LANES)
        areas.append(
            sum(bar_row(frame, row, 60, config) for row in range(layout.keyboard_top))
        )

    assert areas[0] > areas[1] > areas[2]


@pytest.mark.feature("F-82")
def test_a_bar_too_short_to_round_is_drawn_square_rather_than_vanishing() -> None:
    """Radius is capped by half the bar, so a two-pixel note keeps its pixels."""
    score = Score(parts=(Part(notes=(Note(pitch=60, start=1.0, end=1.004),)),))
    config = replace(small_config(), note_radius=0.5)
    frame = render_frame(score, config, 1.0, pedal_lanes=NO_LANES)
    background = parse_hex(config.background)
    assert any(
        tuple(int(c) for c in pixel) != background for pixel in frame.reshape(-1, 3)
    ), "the note must still be drawn"


@pytest.mark.feature("F-82")
@pytest.mark.parametrize("radius", [-0.01, 0.51, 1.0])
def test_a_radius_outside_the_range_is_an_error(radius: float) -> None:
    with pytest.raises(ConfigError, match="note_radius"):
        VisualConfig(note_radius=radius).validate()
