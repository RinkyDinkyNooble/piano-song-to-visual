"""Background gradients, checked against the CSS specifications they follow.

Where CSS Images 4 or CSS Color 4 gives a worked answer, the test uses it
rather than a number worked out here, so the implementation is held to the
standard and not to itself.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from psv.config import Config, ConfigError, GradientConfig, GradientStop, VisualConfig
from psv.model import Score
from psv.render.frame import backdrop, render_frame
from psv.render.gradient import (
    gradient_image,
    linear_srgb_to_oklab,
    oklab_to_linear_srgb,
    position_map,
    ramp,
    srgb_to_linear,
    stop_positions,
)

SMALL = VisualConfig(width=320, height=180, fps=10, lookahead_s=3.0)


def stops(*spec: tuple[str, float | None] | str) -> tuple[GradientStop, ...]:
    """Stops from ``"#rrggbb"`` or ``("#rrggbb", at)``."""
    built = []
    for item in spec:
        if isinstance(item, str):
            built.append(GradientStop(color=item))
        else:
            built.append(GradientStop(color=item[0], at=item[1]))
    return tuple(built)


def oklab_of(hex_colour: str) -> np.ndarray:
    encoded = np.array([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)]) / 255.0
    return linear_srgb_to_oklab(srgb_to_linear(encoded))


def from_oklch(lightness: float, chroma: float, hue: float) -> np.ndarray:
    radians = math.radians(hue)
    return np.array([lightness, chroma * math.cos(radians), chroma * math.sin(radians)])


# -- Oklab -----------------------------------------------------------------


@pytest.mark.feature("F-99")
@pytest.mark.parametrize(
    ("hex_colour", "oklch"),
    [
        # The values CSS Images 4 gives for these two, in its hue example.
        ("#ff0000", (0.628, 0.2577, 29.234)),
        ("#008000", (0.5198, 0.1769, 142.5)),
    ],
)
def test_oklab_agrees_with_the_values_css_publishes(
    hex_colour: str, oklch: tuple[float, float, float]
) -> None:
    assert oklab_of(hex_colour) == pytest.approx(from_oklch(*oklch), abs=6e-4)


@pytest.mark.feature("F-99")
def test_white_is_full_lightness_and_no_colour() -> None:
    assert oklab_of("#ffffff") == pytest.approx([1.0, 0.0, 0.0], abs=1e-4)


@pytest.mark.feature("F-99")
def test_oklab_converts_there_and_back() -> None:
    colours = np.random.default_rng(3).random((500, 3))
    back = oklab_to_linear_srgb(linear_srgb_to_oklab(colours))
    assert back == pytest.approx(colours, abs=1e-6)


@pytest.mark.feature("F-99")
def test_oklab_keeps_the_middle_of_a_blend_from_going_dark() -> None:
    """The reason CSS blends in Oklab: halfway from red to green in encoded
    sRGB is a muddy brown, darker than either end."""

    def middle(space: str) -> float:
        config = GradientConfig(space=space, stops=stops("#ff0000", "#00ff00"))
        colour = ramp(config)[len(ramp(config)) // 2]
        return float(linear_srgb_to_oklab(srgb_to_linear(colour))[0])

    assert middle("oklab") > middle("srgb") + 0.05


# -- stops -----------------------------------------------------------------


@pytest.mark.feature("F-99")
@pytest.mark.parametrize(
    ("given", "fixed"),
    [
        # The worked examples in CSS Images 4, section 3.5.3, in fractions.
        ((None, 0.2, None), [0.0, 0.2, 1.0]),
        ((0.4, None, None, None), [0.4, 0.6, 0.8, 1.0]),
        ((-0.5, None, None), [-0.5, 0.25, 1.0]),
        ((None, -0.5, 1.5, None), [0.0, 0.0, 1.5, 1.5]),
    ],
)
def test_stop_positions_follow_the_css_fixup_rules(
    given: tuple[float | None, ...], fixed: list[float]
) -> None:
    placed = stop_positions([GradientStop(color="#000000", at=at) for at in given])
    assert placed == pytest.approx(fixed)


@pytest.mark.feature("F-99")
@pytest.mark.parametrize("hint", [0.15, 0.3, 0.5, 0.8])
def test_the_halfway_colour_falls_where_the_hint_says(hint: float) -> None:
    config = GradientConfig(
        space="srgb",
        stops=(GradientStop("#000000", 0.0, hint), GradientStop("#ffffff", 1.0)),
    )
    colours = ramp(config)
    index = round(hint * (len(colours) - 1))
    assert colours[index][0] == pytest.approx(0.5, abs=2e-3)


@pytest.mark.feature("F-99")
def test_two_stops_at_one_place_are_a_hard_edge() -> None:
    config = GradientConfig(
        space="srgb",
        stops=stops(
            ("#000000", 0.0), ("#000000", 0.5), ("#ffffff", 0.5), ("#ffffff", 1.0)
        ),
    )
    colours = ramp(config)
    half = len(colours) // 2
    assert colours[half - 2][0] == pytest.approx(0.0)
    assert colours[half + 2][0] == pytest.approx(1.0)


@pytest.mark.feature("F-99")
def test_the_colour_holds_beyond_the_first_and_last_stop() -> None:
    config = GradientConfig(
        space="srgb", stops=stops(("#000000", 0.3), ("#ffffff", 0.7))
    )
    colours = ramp(config)
    assert colours[0][0] == pytest.approx(0.0)
    assert colours[-1][0] == pytest.approx(1.0)


# -- shapes ----------------------------------------------------------------


@pytest.mark.feature("F-99")
def test_a_diagonal_gradient_lands_exactly_on_its_corners() -> None:
    """CSS sizes the gradient line so the far corners are exactly 0 and 1."""
    t = position_map(GradientConfig(angle=45.0), 400, 100)
    assert t[-1, 0] == pytest.approx(0.0, abs=0.01), "bottom left starts it"
    assert t[0, -1] == pytest.approx(1.0, abs=0.01), "top right ends it"


@pytest.mark.feature("F-99")
@pytest.mark.parametrize(
    ("angle", "shape"), [(180.0, (180, 1)), (0.0, (180, 1)), (90.0, (1, 320))]
)
def test_a_straight_gradient_costs_a_column_or_a_row(
    angle: float, shape: tuple[int, int]
) -> None:
    assert position_map(GradientConfig(angle=angle), 320, 180).shape == shape


@pytest.mark.feature("F-99")
def test_zero_degrees_points_up_and_ninety_points_right() -> None:
    up = position_map(GradientConfig(angle=0.0), 320, 180)
    right = position_map(GradientConfig(angle=90.0), 320, 180)
    assert up[0, 0] > up[-1, 0]
    assert right[0, -1] > right[0, 0]


@pytest.mark.feature("F-99")
def test_a_radial_gradient_runs_from_its_centre_to_the_farthest_corner() -> None:
    t = position_map(GradientConfig(shape="radial", center_x=0.25), 400, 200)
    assert t[100, 100] == pytest.approx(0.0, abs=0.02)
    assert t.max() == pytest.approx(1.0, abs=0.01)
    assert t[100, 399] < t[0, 399], "the corner is farther than the edge"


@pytest.mark.feature("F-99")
def test_a_conic_gradient_sweeps_clockwise_from_its_angle() -> None:
    t = position_map(GradientConfig(shape="conic", angle=0.0), 200, 200)
    above, right, below, left = t[10, 100], t[100, 190], t[190, 100], t[100, 10]
    assert above < right < below < left


# -- rendering -------------------------------------------------------------


@pytest.mark.feature("F-99")
def test_a_gradient_fills_the_frame() -> None:
    config = replace(
        SMALL,
        grid=replace(SMALL.grid, opacity=0.0),
        gradient=GradientConfig(angle=90.0, stops=stops("#200000", "#000020")),
    )
    frame = render_frame(Score(), config, 0.0, pedal_lanes=0)
    assert frame[10, 5, 0] > frame[10, 5, 2], "red on the left"
    assert frame[10, -5, 2] > frame[10, -5, 0], "blue on the right"


@pytest.mark.feature("F-99")
def test_the_grid_is_blended_with_what_is_under_it_on_a_sideways_gradient() -> None:
    """Each line mixes with the pixels beneath it, so it stays equally faint
    across a gradient running sideways, not only down one."""
    config = replace(
        SMALL, gradient=GradientConfig(angle=90.0, stops=stops("#000000", "#b0b0b0"))
    )
    frame = render_frame(Score(), config, 0.0, pedal_lanes=0)
    under = backdrop(config, config.width, config.height)
    assert under is not None
    rows = [r for r in range(config.height) if np.any(frame[r] != under[r])]
    assert rows, "no grid line drawn"
    line, beneath = frame[rows[0]].astype(int), under[rows[0]].astype(int)
    lifted = line - beneath
    assert lifted[5].max() > lifted[-5].max(), (
        "fainter on the light side, as it should be"
    )


@pytest.mark.feature("F-99")
def test_a_gradient_is_worked_out_once_and_cannot_be_drawn_on() -> None:
    """Computed per size, not per frame: at 4K that is eight million pixels."""
    config = GradientConfig(shape="radial", stops=stops("#101010", "#303030"))
    first = gradient_image(config, 64, 48)
    assert gradient_image(config, 64, 48) is first
    assert not first.flags.writeable


# -- config ----------------------------------------------------------------


@pytest.mark.feature("F-99")
@pytest.mark.parametrize(
    ("gradient", "legacy", "complaint"),
    [
        (GradientConfig(stops=stops("#000000")), False, "at least two stops"),
        (
            GradientConfig(shape="spiral", stops=stops("#000000", "#ffffff")),
            False,
            "shape",
        ),
        (
            GradientConfig(space="hsl", stops=stops("#000000", "#ffffff")),
            False,
            "space",
        ),
        (
            GradientConfig(
                stops=(GradientStop("#000000"), GradientStop("#ffffff", hint=0.3))
            ),
            False,
            "last stop has a hint",
        ),
        (
            GradientConfig(
                stops=(GradientStop("#000000", hint=1.0), GradientStop("#ffffff"))
            ),
            False,
            "hint must be between",
        ),
        (GradientConfig(stops=stops("#000000", "#ffffff")), True, "use one of them"),
    ],
)
def test_a_gradient_that_cannot_mean_anything_is_refused(
    gradient: GradientConfig, legacy: bool, complaint: str
) -> None:
    config = replace(SMALL, gradient=gradient)
    if legacy:
        config = replace(config, gradient_top="#000000", gradient_bottom="#ffffff")
    with pytest.raises(ConfigError, match=complaint):
        config.validate()


@pytest.mark.feature("F-99")
def test_a_gradient_is_written_in_toml_as_the_readme_shows(tmp_path: Path) -> None:
    path = tmp_path / "psv.toml"
    path.write_text(
        "[visual.gradient]\n"
        'shape = "radial"\n'
        "stops = [\n"
        '  { color = "#1c0a12", at = 0.0 },\n'
        '  { color = "#2a0f3a", at = 0.45, hint = 0.3 },\n'
        '  { color = "#050203" },\n'
        "]\n",
        encoding="utf-8",
    )
    gradient = Config.load(path).visual.gradient
    assert gradient.shape == "radial"
    assert [stop.at for stop in gradient.stops] == [0.0, 0.45, None]
    assert gradient.stops[1].hint == 0.3
