"""Draw one frame.

``render_frame`` is a pure function: same score, same config, same time, same
pixels, every run and every platform. That is what makes the renderer testable
against committed reference images, and it is why nothing here reads a clock, a
random seed, or the filesystem.

The layout, left to right and bottom to top:

* a keyboard along the bottom, with the keys currently sounding lit up
* pedal lanes to the right of it, when any are configured
* the falling area above both, carrying note bars and the alignment grid

Everything that carries meaning is on its own visual channel. Hue says which
hand, brightness says how loud, bar width says black key or white, and the grid
is faint enough to read past. See ``color.py`` for why saturation is left alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from psv.config import VisualConfig
from psv.model import Hand, Note, Pedal, PedalEvent, Score
from psv.render.color import (
    RGB,
    blend,
    note_color,
    parse_hex,
    pedal_color,
    scale,
)
from psv.render.geometry import KeyboardGeometry

#: RGB, uint8. A frame is (height, width, 3).
Frame = np.ndarray

#: How much of the frame the keyboard takes up along the bottom.
KEYBOARD_HEIGHT_FRACTION = 0.16

#: Width of one pedal lane, as a fraction of the whole frame.
PEDAL_LANE_FRACTION = 0.028

#: Gap between the keyboard and the pedal lanes, as a fraction of the frame.
PEDAL_GUTTER_FRACTION = 0.008

#: How much darker a note bar's outline is than the bar itself. Dark enough to
#: read as an edge, still the bar's own hue, so which hand is playing survives.
BORDER_DARKENING = 0.45

#: A border may take at most this fraction of a bar's width or height. A short
#: note at speed is only a few pixels tall, and an outline that swallowed it
#: would cost exactly the thing the outline is for.
BORDER_MAX_SHARE = 0.34

#: How much of its own colour a note keeps when the other hand has the focus.
#: Faint enough to read past, strong enough to still say which hand and how
#: loud: you are practising one hand, not pretending the other does not exist.
MUTED_HAND_MIX = 0.26

#: Pedals in the order they sit under your feet, left to right. Fewer lanes
#: means dropping from the left, so a single lane is the sustain pedal: the one
#: that is both reliably present in MIDI and the one most players actually use.
PEDAL_ORDER: tuple[Pedal, ...] = (Pedal.SOFT, Pedal.SOSTENUTO, Pedal.SUSTAIN)


@dataclass(frozen=True, slots=True)
class Palette:
    """The parts of the picture that are not note colours.

    Deliberately grey. Anything with a hue back here would compete with the
    hues that say which hand is playing.
    """

    background: RGB = (16, 16, 16)
    white_key: RGB = (238, 238, 238)
    black_key: RGB = (28, 28, 28)
    key_edge: RGB = (70, 70, 70)
    strike_line: RGB = (94, 94, 94)
    grid: RGB = (152, 152, 152)
    lane: RGB = (26, 26, 26)
    lane_edge: RGB = (56, 56, 56)


def lanes_for(count: int) -> tuple[Pedal, ...]:
    """Which pedals get a lane, given how many lanes are configured."""
    if count <= 0:
        return ()
    return PEDAL_ORDER[-count:]


@dataclass(frozen=True, slots=True)
class Layout:
    """Where everything sits, and how fast notes fall."""

    width: int
    height: int
    keyboard_top: int
    keyboard_width: int
    lookahead_s: float
    pedals: tuple[Pedal, ...] = ()

    @property
    def fall_height(self) -> int:
        return self.keyboard_top

    @property
    def pixels_per_second(self) -> float:
        return self.fall_height / self.lookahead_s

    @property
    def pedal_area_left(self) -> int:
        return self.width - self.pedal_area_width

    @property
    def pedal_area_width(self) -> int:
        return self.width - self.keyboard_width

    @property
    def gutter(self) -> int:
        """Blank space separating the keyboard from the pedal lanes."""
        return round(self.width * PEDAL_GUTTER_FRACTION) if self.pedals else 0

    @property
    def lane_width(self) -> float:
        if not self.pedals:
            return 0.0
        return (self.pedal_area_width - self.gutter) / len(self.pedals)

    def lane_span(self, pedal: Pedal) -> tuple[float, float]:
        """Left and right edge of one pedal's lane."""
        index = self.pedals.index(pedal)
        start = self.pedal_area_left + self.gutter + index * self.lane_width
        return start, start + self.lane_width

    def time_to_y(self, time: float, now: float) -> float:
        """Where a moment in the music sits on screen at wall-clock ``now``."""
        return self.keyboard_top - (time - now) * self.pixels_per_second

    @classmethod
    def from_config(cls, config: VisualConfig, pedal_lanes: int = 0) -> Layout:
        keyboard_height = max(1, round(config.height * KEYBOARD_HEIGHT_FRACTION))
        pedals = lanes_for(pedal_lanes)
        area = (
            round(
                config.width
                * (PEDAL_LANE_FRACTION * len(pedals) + PEDAL_GUTTER_FRACTION)
            )
            if pedals
            else 0
        )
        return cls(
            width=config.width,
            height=config.height,
            keyboard_top=config.height - keyboard_height,
            keyboard_width=config.width - area,
            lookahead_s=config.lookahead_s,
            pedals=pedals,
        )


def render_frame(
    score: Score,
    config: VisualConfig,
    time: float,
    *,
    palette: Palette | None = None,
    pedal_lanes: int = 1,
    focus: Hand | None = None,
) -> Frame:
    """Render the piece as it looks at ``time``, in seconds.

    ``focus`` picks one hand out for practice. The other hand is still drawn,
    faintly, because knowing where it is is half the reason to practise hands
    separately; it is the soundtrack that goes quiet, not the picture.
    """
    palette = palette or Palette(background=parse_hex(config.background))
    layout = Layout.from_config(config, pedal_lanes)
    geometry = KeyboardGeometry(
        width=layout.keyboard_width,
        height=layout.height - layout.keyboard_top,
        black_bar_ratio=config.black_key_bar_width,
    )

    frame = np.empty((layout.height, layout.width, 3), dtype=np.uint8)
    frame[:, :] = palette.background

    _draw_grid(frame, score, config, layout, geometry, palette, time)
    sounding = _draw_falling_notes(
        frame, score, config, layout, geometry, palette, time, focus
    )
    active_pedals = _draw_pedal_lanes(frame, score, config, layout, palette, time)
    _draw_keyboard(frame, layout, geometry, palette, sounding, config)
    _draw_pedal_indicators(frame, layout, palette, active_pedals, config)
    return frame


def _fill(
    frame: Frame,
    left: float,
    top: float,
    right: float,
    bottom: float,
    colour: RGB,
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


# -- the alignment grid --------------------------------------------------


def _draw_grid(
    frame: Frame,
    score: Score,
    config: VisualConfig,
    layout: Layout,
    geometry: KeyboardGeometry,
    palette: Palette,
    time: float,
) -> None:
    """Faint rules for reading the picture, drawn under everything else.

    Beat lines run horizontally, one per beat, so two notes an octave and a half
    apart can be seen to land together. Pitch lines run vertically at keyboard
    landmarks, so you can find which key a bar is heading for without counting.
    """
    grid = config.grid
    if grid.opacity <= 0:
        return
    colour = blend(palette.background, palette.grid, grid.opacity)

    if grid.pitch_lines != "none":
        step = 12 if grid.pitch_lines == "octave" else 7
        for pitch in range(24, 109, step):
            if not geometry.contains(pitch):
                continue
            left, _ = geometry.key_span(pitch)
            _fill(frame, left, 0, left + 1, layout.keyboard_top, colour)

    if grid.beat_lines != "none":
        for line_time in _beat_line_times(score, grid.beat_lines, time, layout):
            y = layout.time_to_y(line_time, time)
            _fill(frame, 0, y, layout.width, y + 1, colour)


def _beat_line_times(
    score: Score, mode: str, time: float, layout: Layout
) -> Iterator[float]:
    """When the horizontal rules in view fall.

    Bar lines come from the bar index rather than from a fixed number of beats,
    so a piece that changes meter part way through keeps its lines on its bars
    instead of drifting off them from the change onward.
    """
    until = time + layout.lookahead_s
    if mode == "bar":
        for _, seconds in score.meter.bar_times(until, since_seconds=max(0.0, time)):
            yield seconds
        return
    for seconds in score.tempo_map.beat_times(until):
        if seconds >= time:
            yield seconds


# -- notes ---------------------------------------------------------------


def _draw_falling_notes(
    frame: Frame,
    score: Score,
    config: VisualConfig,
    layout: Layout,
    geometry: KeyboardGeometry,
    palette: Palette,
    time: float,
    focus: Hand | None = None,
) -> dict[int, RGB]:
    """Draw the visible bars; return the pitches sounding now and their colours.

    A note reaches the keyboard exactly at its start time, so its bar bottom is
    at the keyboard's top edge when ``time`` equals ``note.start``.
    """
    window_end = time + layout.lookahead_s
    sounding: dict[int, RGB] = {}

    for note in score.notes_between(time, window_end):
        if not geometry.contains(note.pitch):
            # Off the 88 keys entirely. `psv inspect` reports these; drawing
            # them would put a bar somewhere it does not belong.
            continue

        colour = note_color(note, config.colors, config.black_key_darkening)
        if focus is not None and note.hand is not focus:
            colour = blend(palette.background, colour, MUTED_HAND_MIX)
        if note.start <= time < note.end:
            sounding[note.pitch] = colour

        bottom = layout.time_to_y(note.start, time)
        top = layout.time_to_y(note.end, time)
        if bottom <= 0 or top >= layout.keyboard_top:
            continue

        left, right = geometry.bar_span(note.pitch)
        _draw_bar(
            frame,
            left,
            top,
            right,
            min(bottom, layout.keyboard_top),
            colour,
            border=round(config.width * config.note_border),
        )

    return sounding


def _draw_bar(
    frame: Frame,
    left: float,
    top: float,
    right: float,
    bottom: float,
    colour: RGB,
    border: int,
) -> None:
    """One note bar, outlined in a darker shade of its own colour.

    The outline is what separates consecutive notes on the same key. Drawn
    inside the bar rather than around it, so a note still occupies exactly the
    pixels its timing says it does and the bar's bottom edge stays on the
    keyboard at the moment the note starts.
    """
    if border <= 0:
        _fill(frame, left, top, right, bottom, colour)
        return

    # Never let the outline eat the bar it is outlining.
    thickness = min(
        border,
        int((right - left) * BORDER_MAX_SHARE),
        int((bottom - top) * BORDER_MAX_SHARE),
    )
    if thickness <= 0:
        _fill(frame, left, top, right, bottom, colour)
        return

    _fill(frame, left, top, right, bottom, scale(colour, 1.0 - BORDER_DARKENING))
    _fill(
        frame,
        left + thickness,
        top + thickness,
        right - thickness,
        bottom - thickness,
        colour,
    )


# -- pedals --------------------------------------------------------------


def _draw_pedal_lanes(
    frame: Frame,
    score: Score,
    config: VisualConfig,
    layout: Layout,
    palette: Palette,
    time: float,
) -> dict[Pedal, RGB]:
    """Pedal presses fall down their own lanes, exactly as notes do.

    Depth is shown as brightness, the same channel loudness uses, so a
    half-pedal reads as a dimmer bar rather than looking identical to a full
    one.
    """
    if not layout.pedals:
        return {}

    for pedal in layout.pedals:
        left, right = layout.lane_span(pedal)
        _fill(frame, left, 0, right, layout.height, palette.lane)
        _fill(frame, left, 0, left + 1, layout.height, palette.lane_edge)

    window_end = time + layout.lookahead_s
    active: dict[Pedal, RGB] = {}

    for event in score.pedals:
        if event.pedal not in layout.pedals:
            continue
        if event.start >= window_end or event.end <= time:
            continue

        colour = pedal_color(event.depth, config.colors)
        if event.active_at(time):
            active[event.pedal] = colour

        bottom = layout.time_to_y(event.start, time)
        top = layout.time_to_y(event.end, time)
        left, right = layout.lane_span(event.pedal)
        inset = (right - left) * 0.18
        _fill(
            frame,
            left + inset,
            top,
            right - inset,
            min(bottom, layout.keyboard_top),
            colour,
        )

    return active


def _draw_pedal_indicators(
    frame: Frame,
    layout: Layout,
    palette: Palette,
    active: dict[Pedal, RGB],
    config: VisualConfig,
) -> None:
    """The lane footers, which light while their pedal is held."""
    del config
    if not layout.pedals:
        return
    top = layout.keyboard_top + 1
    for pedal in layout.pedals:
        left, right = layout.lane_span(pedal)
        colour = active.get(pedal, palette.lane_edge)
        inset = (right - left) * 0.18
        _fill(frame, left + inset, top, right - inset, layout.height, colour)


# -- keyboard ------------------------------------------------------------


def _draw_keyboard(
    frame: Frame,
    layout: Layout,
    geometry: KeyboardGeometry,
    palette: Palette,
    sounding: dict[int, RGB],
    config: VisualConfig,
) -> None:
    """Draw the keyboard, whites first so blacks sit on top of them.

    A sounding key is lit in the same colour as its falling bar, so the eye can
    follow one note from the top of the screen down onto the key.
    """
    del config
    top = layout.keyboard_top
    bottom = layout.height

    _fill(frame, 0, top, layout.keyboard_width, top + 1, palette.strike_line)

    key_top = top + 1
    for pitch in geometry.white_pitches():
        left, right = geometry.key_span(pitch)
        colour = sounding.get(pitch, palette.white_key)
        _fill(frame, left, key_top, right, bottom, colour)
        _fill(frame, right - 1, key_top, right, bottom, palette.key_edge)

    black_bottom = key_top + geometry.black_height
    for pitch in geometry.black_pitches():
        left, right = geometry.key_span(pitch)
        _fill(
            frame,
            left,
            key_top,
            right,
            black_bottom,
            sounding.get(pitch, palette.black_key),
        )


def visible_notes(score: Score, config: VisualConfig, time: float) -> tuple[Note, ...]:
    """The notes a frame at ``time`` would consider drawing.

    Exposed so a timing bug can be diagnosed against a note list rather than
    against pixels.
    """
    layout = Layout.from_config(config)
    return score.notes_between(time, time + layout.lookahead_s)


def visible_pedals(
    score: Score, config: VisualConfig, time: float, pedal_lanes: int = 1
) -> tuple[PedalEvent, ...]:
    """The pedal events a frame at ``time`` would draw."""
    layout = Layout.from_config(config, pedal_lanes)
    window_end = time + layout.lookahead_s
    return tuple(
        event
        for event in score.pedals
        if event.pedal in layout.pedals
        and event.start < window_end
        and event.end > time
    )
