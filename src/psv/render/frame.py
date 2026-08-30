"""Draw one frame.

``render_frame`` is a pure function: same score, same config, same time, same
pixels, every run and every platform. That is what makes the renderer testable
against committed reference images, and it is why nothing here reads a clock, a
random seed, or the filesystem.

Scope for now is deliberately narrow: falling bars, the keyboard, and pressed
keys. Dynamics colour, pedal lanes, and the alignment grid come later; this
exists so the constraint engine's output can be watched rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from psv.config import VisualConfig
from psv.model import Note, Score
from psv.render.geometry import KeyboardGeometry

#: RGB, uint8. A frame is (height, width, 3).
Frame = np.ndarray


@dataclass(frozen=True, slots=True)
class Palette:
    """Provisional greys. M4 replaces the bar colours with hand and velocity."""

    background: tuple[int, int, int] = (16, 16, 16)
    white_bar: tuple[int, int, int] = (215, 219, 226)
    black_bar: tuple[int, int, int] = (150, 156, 168)
    white_key: tuple[int, int, int] = (238, 238, 238)
    black_key: tuple[int, int, int] = (28, 28, 30)
    key_edge: tuple[int, int, int] = (70, 70, 74)
    pressed: tuple[int, int, int] = (120, 170, 220)
    strike_line: tuple[int, int, int] = (90, 92, 100)


def parse_hex(colour: str) -> tuple[int, int, int]:
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the keyboard sits and how fast notes fall."""

    width: int
    height: int
    keyboard_top: int
    lookahead_s: float

    @property
    def fall_height(self) -> int:
        return self.keyboard_top

    @property
    def pixels_per_second(self) -> float:
        return self.fall_height / self.lookahead_s

    @classmethod
    def from_config(cls, config: VisualConfig) -> Layout:
        keyboard_height = max(1, round(config.height * KEYBOARD_HEIGHT_FRACTION))
        return cls(
            width=config.width,
            height=config.height,
            keyboard_top=config.height - keyboard_height,
            lookahead_s=config.lookahead_s,
        )


#: How much of the frame the keyboard takes up along the bottom.
KEYBOARD_HEIGHT_FRACTION = 0.16


def render_frame(
    score: Score,
    config: VisualConfig,
    time: float,
    *,
    palette: Palette | None = None,
) -> Frame:
    """Render the piece as it looks at ``time``, in seconds."""
    palette = palette or Palette(background=parse_hex(config.background))
    layout = Layout.from_config(config)
    geometry = KeyboardGeometry(
        width=layout.width,
        height=layout.height - layout.keyboard_top,
        black_bar_ratio=config.black_key_bar_width,
    )

    frame = np.empty((layout.height, layout.width, 3), dtype=np.uint8)
    frame[:, :] = palette.background

    sounding = _draw_falling_notes(frame, score, layout, geometry, palette, time)
    _draw_keyboard(frame, layout, geometry, palette, sounding)
    return frame


def _fill(
    frame: Frame,
    left: float,
    top: float,
    right: float,
    bottom: float,
    colour: tuple[int, int, int],
) -> None:
    """Fill a rectangle, clipped to the frame.

    Rounding once here keeps every caller working in floats, so a bar's position
    does not drift as it is passed around.
    """
    height, width = frame.shape[:2]
    x0 = max(0, round(left))
    x1 = min(width, round(right))
    y0 = max(0, round(top))
    y1 = min(height, round(bottom))
    if x1 <= x0 or y1 <= y0:
        return
    frame[y0:y1, x0:x1] = colour


def _draw_falling_notes(
    frame: Frame,
    score: Score,
    layout: Layout,
    geometry: KeyboardGeometry,
    palette: Palette,
    time: float,
) -> set[int]:
    """Draw every bar in the visible window; return the pitches sounding now.

    A note reaches the keyboard exactly at its start time, so its bar bottom is
    at the keyboard's top edge when ``time`` equals ``note.start``.
    """
    window_end = time + layout.lookahead_s
    pixels_per_second = layout.pixels_per_second
    sounding: set[int] = set()

    for note in score.notes_between(time, window_end):
        if not geometry.contains(note.pitch):
            # Off the 88 keys entirely. `psv inspect` reports these; drawing
            # them would put a bar somewhere it does not belong.
            continue
        if note.start <= time < note.end:
            sounding.add(note.pitch)

        bottom = layout.keyboard_top - (note.start - time) * pixels_per_second
        top = layout.keyboard_top - (note.end - time) * pixels_per_second
        if bottom <= 0 or top >= layout.keyboard_top:
            continue

        left, right = geometry.bar_span(note.pitch)
        colour = palette.black_bar if note.is_black_key else palette.white_bar
        # Clamp the bottom so a sounding note stops at the keyboard rather than
        # drawing over it, and keep a bar at least one pixel tall.
        _fill(
            frame,
            left,
            top,
            right,
            min(bottom, layout.keyboard_top),
            colour,
        )

    return sounding


def _draw_keyboard(
    frame: Frame,
    layout: Layout,
    geometry: KeyboardGeometry,
    palette: Palette,
    sounding: set[int],
) -> None:
    """Draw the keyboard, whites first so blacks sit on top of them."""
    top = layout.keyboard_top
    bottom = layout.height

    _fill(frame, 0, top, layout.width, top + 1, palette.strike_line)

    key_top = top + 1
    for pitch in geometry.white_pitches():
        left, right = geometry.key_span(pitch)
        colour = palette.pressed if pitch in sounding else palette.white_key
        _fill(frame, left, key_top, right, bottom, colour)
        _fill(frame, right - 1, key_top, right, bottom, palette.key_edge)

    black_bottom = key_top + geometry.black_height
    for pitch in geometry.black_pitches():
        left, right = geometry.key_span(pitch)
        colour = palette.pressed if pitch in sounding else palette.black_key
        _fill(frame, left, key_top, right, black_bottom, colour)


def visible_notes(score: Score, config: VisualConfig, time: float) -> tuple[Note, ...]:
    """The notes a frame at ``time`` would consider drawing.

    Exposed so a timing bug can be diagnosed against a note list rather than
    against pixels.
    """
    layout = Layout.from_config(config)
    return score.notes_between(time, time + layout.lookahead_s)
