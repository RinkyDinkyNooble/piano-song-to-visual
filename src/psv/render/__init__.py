"""Falling-notes rendering.

`geometry` is pure arithmetic, `frame` is a pure function over a Score, and
`video` is the only part that touches the filesystem or an encoder.
"""

from psv.render.color import note_color, pedal_color, velocity_brightness
from psv.render.frame import (
    Layout,
    Palette,
    lanes_for,
    render_frame,
    visible_notes,
    visible_pedals,
)
from psv.render.geometry import KeyboardGeometry, white_index
from psv.render.video import VideoWriteError, iter_frames, render_video

__all__ = [
    "KeyboardGeometry",
    "Layout",
    "Palette",
    "VideoWriteError",
    "iter_frames",
    "lanes_for",
    "note_color",
    "pedal_color",
    "render_frame",
    "render_video",
    "velocity_brightness",
    "visible_notes",
    "visible_pedals",
    "white_index",
]
