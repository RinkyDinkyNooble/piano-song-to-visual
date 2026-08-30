"""Falling-notes rendering.

`geometry` is pure arithmetic, `frame` is a pure function over a Score, and
`video` is the only part that touches the filesystem or an encoder.
"""

from psv.render.frame import Layout, Palette, render_frame, visible_notes
from psv.render.geometry import KeyboardGeometry, white_index
from psv.render.video import VideoWriteError, iter_frames, render_video

__all__ = [
    "KeyboardGeometry",
    "Layout",
    "Palette",
    "VideoWriteError",
    "iter_frames",
    "render_frame",
    "render_video",
    "visible_notes",
    "white_index",
]
