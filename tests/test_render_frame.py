"""Frame rendering: timing, determinism, and reference images.

`render_frame` is pure, which is the whole reason these tests can exist. A
timing bug is caught here as a pixel row rather than as a video that feels
slightly off.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from psv.config import Config, GridConfig, VisualConfig
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Score
from psv.render.color import parse_hex
from psv.render.frame import Layout, Palette, render_frame, visible_notes
from psv.render.geometry import KeyboardGeometry
from tests.fixtures.midi_builder import FIXTURES

REFERENCE_DIR = Path(__file__).resolve().parent / "assets" / "reference"

#: Small on purpose. Reference images stay tiny in git, and a frame renders in
#: milliseconds, which is what makes this a usable debugging loop.
SMALL = VisualConfig(width=320, height=180, fps=10, lookahead_s=3.0)


def small_config(**overrides: object) -> VisualConfig:
    config = replace(SMALL, **overrides)  # type: ignore[arg-type]
    config.validate()
    return config


def one_note_score(pitch: int = 60, start: float = 1.0, end: float = 2.0) -> Score:
    return Score(parts=(Part(notes=(Note(pitch=pitch, start=start, end=end),)),))


#: Geometry tests render without lanes so the keyboard spans the full frame and
#: the arithmetic stays easy to follow. Lane behaviour has its own tests.
NO_LANES = 0

#: The grid is drawn under everything, so "is the falling area empty?" has to
#: mean "empty apart from the grid". Turning it off is clearer than thresholding.
PLAIN = replace(SMALL, grid=replace(SMALL.grid, opacity=0.0))


def geometry_for(config: VisualConfig, lanes: int = NO_LANES) -> KeyboardGeometry:
    layout = Layout.from_config(config, lanes)
    return KeyboardGeometry(layout.keyboard_width, config.height - layout.keyboard_top)


def painted(frame: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    """Mask of pixels that are not the background.

    Better than a brightness threshold now that colour carries meaning: a quiet
    black-key note is legitimately dark, and any fixed cutoff would either miss
    it or catch the grid.
    """
    return np.any(frame != np.array(background, dtype=np.uint8), axis=-1)


PLAIN_BG = parse_hex(PLAIN.background)


# -- shape and colour ----------------------------------------------------


@pytest.mark.feature("F-12")
def test_a_frame_has_the_configured_shape_and_type() -> None:
    frame = render_frame(Score(), SMALL, 0.0)
    assert frame.shape == (180, 320, 3)
    assert frame.dtype == np.uint8


@pytest.mark.feature("F-12")
def test_an_empty_score_still_draws_the_keyboard() -> None:
    frame = render_frame(Score(), PLAIN, 0.0, pedal_lanes=NO_LANES)
    layout = Layout.from_config(SMALL, NO_LANES)
    assert not painted(frame[: layout.keyboard_top], PLAIN_BG).any()
    assert frame[layout.keyboard_top :].max() > 200, "keyboard should be drawn"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("#000000", (0, 0, 0)), ("#ffffff", (255, 255, 255)), ("#4a90d9", (74, 144, 217))],
)
def test_hex_colours_parse(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_hex(text) == expected


def test_short_hex_colours_expand() -> None:
    assert parse_hex("#abc") == parse_hex("#aabbcc")


@pytest.mark.feature("F-12")
def test_the_background_comes_from_config() -> None:
    config = replace(small_config(background="#404040"), grid=PLAIN.grid)
    frame = render_frame(Score(), config, 0.0)
    assert tuple(frame[0, 0]) == (64, 64, 64)


# -- timing --------------------------------------------------------------


@pytest.mark.feature("F-12")
def test_a_note_reaches_the_keyboard_exactly_at_its_start_time() -> None:
    """The whole point of the visual: the bar touches the keys when you play."""
    score = one_note_score(start=1.0, end=2.0)
    layout = Layout.from_config(SMALL, NO_LANES)
    geometry = geometry_for(SMALL)
    column = round(geometry.key_centre(60))

    strip = render_frame(score, PLAIN, 1.0, pedal_lanes=NO_LANES)[
        : layout.keyboard_top, column
    ]
    lit = np.flatnonzero(painted(strip, PLAIN_BG))
    assert lit.size, "the note should be on screen at its start time"
    assert lit.max() == layout.keyboard_top - 1, "its bottom should touch the keyboard"


@pytest.mark.feature("F-12")
def test_a_note_is_off_screen_before_it_enters_the_window() -> None:
    """Lookahead is 3s, so a note starting at 10s must not show at t=0."""
    score = one_note_score(start=10.0, end=11.0)
    frame = render_frame(score, PLAIN, 0.0, pedal_lanes=NO_LANES)
    layout = Layout.from_config(SMALL, NO_LANES)
    assert not painted(frame[: layout.keyboard_top], PLAIN_BG).any()


@pytest.mark.feature("F-12")
def test_a_note_has_passed_once_it_is_over() -> None:
    score = one_note_score(start=1.0, end=2.0)
    frame = render_frame(score, PLAIN, 5.0, pedal_lanes=NO_LANES)
    layout = Layout.from_config(SMALL, NO_LANES)
    assert not painted(frame[: layout.keyboard_top], PLAIN_BG).any()


@pytest.mark.feature("F-12")
def test_a_bar_descends_as_time_advances() -> None:
    score = one_note_score(start=2.5, end=3.0)
    layout = Layout.from_config(SMALL, NO_LANES)
    geometry = geometry_for(SMALL)
    column = round(geometry.key_centre(60))

    positions = []
    for time in (0.0, 0.5, 1.0, 1.5, 2.0):
        strip = render_frame(score, PLAIN, time, pedal_lanes=NO_LANES)[
            : layout.keyboard_top, column
        ]
        lit = np.flatnonzero(painted(strip, PLAIN_BG))
        positions.append(lit.max() if lit.size else -1)

    assert positions == sorted(positions), f"bar did not descend: {positions}"
    assert positions[-1] > positions[0]


@pytest.mark.feature("F-12")
def test_a_longer_note_draws_a_taller_bar() -> None:
    layout = Layout.from_config(SMALL, NO_LANES)
    geometry = geometry_for(SMALL)
    column = round(geometry.key_centre(60))

    def bar_height(end: float) -> int:
        score = one_note_score(start=1.0, end=end)
        strip = render_frame(score, PLAIN, 0.5, pedal_lanes=NO_LANES)[
            : layout.keyboard_top, column
        ]
        return int(painted(strip, PLAIN_BG).sum())

    assert bar_height(2.0) > bar_height(1.2)


@pytest.mark.feature("F-12")
def test_notes_land_on_their_own_key_and_not_a_neighbour() -> None:
    layout = Layout.from_config(SMALL, NO_LANES)
    geometry = geometry_for(SMALL)
    row = layout.keyboard_top - 2

    for pitch in (21, 36, 60, 61, 88, 108):
        frame = render_frame(
            one_note_score(pitch, 1.0, 2.0), PLAIN, 1.0, pedal_lanes=NO_LANES
        )
        lit = np.flatnonzero(painted(frame[row], PLAIN_BG))
        assert lit.size, f"pitch {pitch} drew nothing"
        centre = (lit.min() + lit.max()) / 2
        assert centre == pytest.approx(geometry.key_centre(pitch), abs=1.5)


@pytest.mark.feature("F-12")
def test_a_note_off_the_88_keys_is_not_drawn() -> None:
    """`psv inspect` reports these. Drawing one would put a bar nowhere valid."""
    frame = render_frame(
        one_note_score(pitch=12, start=1.0, end=2.0), PLAIN, 1.0, pedal_lanes=NO_LANES
    )
    layout = Layout.from_config(SMALL, NO_LANES)
    assert not painted(frame[: layout.keyboard_top], PLAIN_BG).any()


@pytest.mark.feature("F-12")
def test_a_sounding_note_highlights_its_key() -> None:
    score = one_note_score(start=1.0, end=2.0)
    layout = Layout.from_config(SMALL, NO_LANES)
    keyboard_row = layout.keyboard_top + 4

    before = render_frame(score, PLAIN, 0.5, pedal_lanes=NO_LANES)[keyboard_row]
    during = render_frame(score, PLAIN, 1.5, pedal_lanes=NO_LANES)[keyboard_row]
    assert not np.array_equal(before, during), "the key should light up"


def test_visible_notes_matches_the_lookahead_window() -> None:
    score = Score(
        parts=(
            Part(
                notes=(
                    Note(pitch=60, start=0.0, end=0.5),
                    Note(pitch=62, start=2.0, end=2.5),
                    Note(pitch=64, start=10.0, end=10.5),
                )
            ),
        )
    )
    pitches = [note.pitch for note in visible_notes(score, SMALL, 0.0)]
    assert pitches == [60, 62]


# -- determinism ---------------------------------------------------------


@pytest.mark.feature("F-13")
def test_the_same_inputs_give_identical_pixels() -> None:
    score = read_midi(FIXTURES["dynamic-levels"]())
    first = render_frame(score, SMALL, 3.0)
    second = render_frame(score, SMALL, 3.0)
    assert np.array_equal(first, second)


@pytest.mark.feature("F-13")
@settings(max_examples=40, deadline=None)
@given(
    time=st.floats(min_value=0.0, max_value=12.0),
    width=st.sampled_from([160, 320, 640]),
    height=st.sampled_from([120, 180, 360]),
)
def test_rendering_is_deterministic_for_any_time_and_size(
    time: float, width: int, height: int
) -> None:
    """Purity is what the reference-image tests rest on. If a frame could ever
    differ between two identical calls, every one of them becomes flaky."""
    score = read_midi(FIXTURES["two-hands"]())
    config = small_config(width=width, height=height)
    assert np.array_equal(
        render_frame(score, config, time), render_frame(score, config, time)
    )


@pytest.mark.feature("F-13")
def test_rendering_does_not_mutate_the_score() -> None:
    score = read_midi(FIXTURES["orchestral"]())
    before = score.notes
    render_frame(score, SMALL, 1.0)
    assert score.notes == before


@pytest.mark.feature("F-13")
def test_a_custom_palette_is_honoured() -> None:
    palette = Palette(background=(1, 2, 3))
    frame = render_frame(Score(), PLAIN, 0.0, palette=palette, pedal_lanes=NO_LANES)
    assert tuple(frame[0, 0]) == (1, 2, 3)


# -- reference images ----------------------------------------------------

#: References render with all three lanes so the pedal area is pinned too.
REFERENCE_LANES = 3

REFERENCE_CASES = [
    ("full-keyboard", 2.0),
    ("two-hands", 1.0),
    ("dynamic-levels", 2.0),
    ("wide-span-chord", 0.5),
    # Every M4 channel at once: three pedal lanes, both hand hues, the grid.
    ("three-pedals", 2.0),
    ("half-pedal", 3.0),
]


@pytest.mark.feature("F-12")
@pytest.mark.parametrize(("fixture", "time"), REFERENCE_CASES)
def test_frames_match_their_committed_reference(fixture: str, time: float) -> None:
    """Pins the rendering. Any change to layout or drawing shows up here as a
    pixel diff; regenerate with `python scripts/make_references.py` once the
    change is deliberate.
    """
    from PIL import Image

    path = REFERENCE_DIR / f"{fixture}-{time:g}s.png"
    assert path.exists(), f"missing reference {path.name}; run make_references.py"

    score = read_midi(FIXTURES[fixture]())
    rendered = render_frame(score, SMALL, time, pedal_lanes=REFERENCE_LANES)
    expected = np.array(Image.open(path).convert("RGB"))

    assert rendered.shape == expected.shape
    differing = int(np.count_nonzero(np.any(rendered != expected, axis=2)))
    assert differing == 0, f"{differing} pixels differ from {path.name}"


def test_the_default_config_renders_a_full_size_frame() -> None:
    """Guards against the shipped defaults being unrenderable."""
    config = Config.load(None).visual
    frame = render_frame(read_midi(FIXTURES["single-note"]()), config, 0.0)
    assert frame.shape == (config.height, config.width, 3)


# -- practising one hand -------------------------------------------------


def two_hand_note_score() -> Score:
    """One note in each hand, at the same moment, an octave and a half apart."""
    return Score(
        parts=(
            Part(
                notes=(
                    Note(pitch=48, start=1.0, end=2.0, hand=Hand.LEFT),
                    Note(pitch=72, start=1.0, end=2.0, hand=Hand.RIGHT),
                ),
            ),
        )
    )


def _bar_colour(frame: np.ndarray, pitch: int, config: VisualConfig) -> np.ndarray:
    layout = Layout.from_config(config)
    geometry = KeyboardGeometry(
        width=layout.keyboard_width,
        height=layout.height - layout.keyboard_top,
        black_bar_ratio=config.black_key_bar_width,
    )
    left, right = geometry.bar_span(pitch)
    column = round((left + right) / 2)
    # Just above the keyboard, where a note starting now has its bottom edge.
    pixel: np.ndarray = frame[layout.keyboard_top - 3, column]
    return pixel


@pytest.mark.feature("F-54")
def test_focusing_a_hand_dims_the_other_and_leaves_it_visible() -> None:
    """Faint, not gone. Knowing where the other hand is is half the reason to
    practise hands separately."""
    score = two_hand_note_score()
    both = render_frame(score, SMALL, 1.0, pedal_lanes=0)
    right = render_frame(score, SMALL, 1.0, pedal_lanes=0, focus=Hand.RIGHT)

    background = np.array(parse_hex(SMALL.background), dtype=np.int16)
    muted = _bar_colour(right, 48, SMALL).astype(np.int16)
    full = _bar_colour(both, 48, SMALL).astype(np.int16)

    assert int(np.abs(muted - background).sum()) > 0, "the muted hand is still drawn"
    assert int(np.abs(muted - background).sum()) < int(np.abs(full - background).sum())


@pytest.mark.feature("F-54")
def test_focusing_a_hand_leaves_that_hand_exactly_as_it_was() -> None:
    score = two_hand_note_score()
    both = render_frame(score, SMALL, 1.0, pedal_lanes=0)
    right = render_frame(score, SMALL, 1.0, pedal_lanes=0, focus=Hand.RIGHT)
    assert np.array_equal(_bar_colour(both, 72, SMALL), _bar_colour(right, 72, SMALL))


def test_focusing_stays_a_pure_function_of_its_inputs() -> None:
    score = two_hand_note_score()
    first = render_frame(score, SMALL, 1.5, focus=Hand.LEFT)
    second = render_frame(score, SMALL, 1.5, focus=Hand.LEFT)
    assert np.array_equal(first, second)


# -- borders on the note bars --------------------------------------------


def repeated_note_score(count: int = 6, length: float = 0.25) -> Score:
    """The same key struck several times in a row, with no gap between."""
    return Score(
        parts=(
            Part(
                notes=tuple(
                    Note(
                        pitch=60,
                        start=index * length,
                        end=index * length + length,
                        velocity=90,
                        hand=Hand.RIGHT,
                    )
                    for index in range(count)
                )
            ),
        )
    )


#: The border tests read raw pixel values down one column, so the grid has to be
#: off: a faint rule crossing the bar is a third shade that has nothing to do
#: with what is being measured.
NO_GRID = GridConfig(pitch_lines="none", beat_lines="none", opacity=0.0)


def _bar_column(frame: np.ndarray, pitch: int, config: VisualConfig) -> np.ndarray:
    """The pixels straight down the middle of one pitch's falling bar."""
    layout = Layout.from_config(config)
    geometry = KeyboardGeometry(
        width=layout.keyboard_width,
        height=layout.height - layout.keyboard_top,
        black_bar_ratio=config.black_key_bar_width,
    )
    left, right = geometry.bar_span(pitch)
    column = round((left + right) / 2)
    return frame[: layout.keyboard_top, column]


@pytest.mark.feature("F-58")
def test_repeated_notes_on_one_key_are_separated() -> None:
    """Without this they draw as one continuous block: there is already a gap
    between adjacent pitches, so a chord reads as separate notes, but nothing
    separated consecutive notes in the same column. Six fast repeats looked like
    one long note, which defeats the point of the video."""
    score = repeated_note_score()
    config = small_config(width=560, height=360, note_border=0.0016, grid=NO_GRID)

    column = _bar_column(render_frame(score, config, 0.0, pedal_lanes=0), 60, config)
    shades = {tuple(pixel) for pixel in column}
    assert len(shades) >= 3, "bar colour, border colour, and background"


@pytest.mark.feature("F-58")
def test_a_border_of_zero_leaves_the_bar_solid() -> None:
    """Off means off. The old picture has to remain reachable."""
    score = repeated_note_score()
    config = small_config(width=560, height=360, note_border=0.0, grid=NO_GRID)

    column = _bar_column(render_frame(score, config, 0.0, pedal_lanes=0), 60, config)
    lit = {tuple(p) for p in column if tuple(p) != (16, 16, 16)}
    assert len(lit) == 1, "one bar colour and nothing else"


@pytest.mark.feature("F-58")
def test_a_border_never_swallows_a_short_bar() -> None:
    """A short note at speed is a few pixels tall. An outline that consumed it
    would cost exactly the thing the outline is for."""
    score = repeated_note_score(count=40, length=0.03)
    config = small_config(width=320, height=180, note_border=0.02, grid=NO_GRID)

    column = _bar_column(render_frame(score, config, 0.0, pedal_lanes=0), 60, config)
    background = tuple(parse_hex(config.background))
    assert any(tuple(pixel) != background for pixel in column), "notes still drawn"


@pytest.mark.feature("F-58")
def test_the_border_keeps_the_hand_hue() -> None:
    """A darker shade of the bar's own colour, not a neutral outline: which hand
    is playing has to survive being drawn at the edge."""
    from psv.render.color import scale
    from psv.render.frame import BORDER_DARKENING

    score = repeated_note_score()
    config = small_config(width=560, height=360, note_border=0.0016, grid=NO_GRID)
    column = _bar_column(render_frame(score, config, 0.0, pedal_lanes=0), 60, config)

    background = tuple(parse_hex(config.background))
    lit = [tuple(p) for p in column if tuple(p) != background]
    brightest = max(lit, key=lambda pixel: sum(int(c) for c in pixel))
    assert scale(brightest, 1.0 - BORDER_DARKENING) in lit
