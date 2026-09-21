"""Background gradients: colour stops, midpoint hints, shapes, and Oklab.

Modelled on CSS Images, which has already settled every question a gradient
raises, rather than invented here:

* **Stops** are colours at positions from 0 to 1. A stop left without a
  position is placed by the CSS "fixup" rules: the first at 0, the last at 1,
  one before an earlier position moved up to it, and any run left without
  positions spread evenly between its neighbours. Two stops at one position
  make a hard edge.
* **Hints** move where the halfway colour between two stops falls. CSS gives
  the weight at a point as ``C = P ** log_H(0.5)``, where ``P`` is how far the
  point is between the two stops and ``H`` is where the hint is. A hint of
  0.5 is an ordinary even blend.
* **Colour space.** CSS Images 4 blends in Oklab by default, because blending
  gamma-encoded sRGB goes dark and muddy between two hues: halfway from red
  to green is brown. Oklab's conversion is Björn Ottosson's, published as
  public domain. sRGB is kept for matching the older two-colour gradients.
* **Shapes.** A linear gradient runs along an angle, 0 pointing up and
  turning clockwise, and its length is ``|W sin A| + |H cos A|`` so that the
  corners land exactly on 0 and 1. A radial one is an ellipse reaching the
  farthest corner, with the aspect ratio of the frame about its centre. A
  conic one sweeps round the centre from the angle.

A gradient does not change during a render, so it is worked out once per size
and cached. `render_frame` stays a pure function of the score and the time:
the cache only saves repeating arithmetic whose answer cannot differ.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import lru_cache

import numpy as np

from psv.config import GradientConfig, GradientStop
from psv.rgb import parse_hex

#: How finely the colour along the gradient is sampled before being spread over
#: the frame. The samples are interpolated between, so this bounds how well the
#: curve of a hint or of Oklab is followed, not how many colours come out.
SAMPLES = 4096

#: A sine or cosine smaller than this is treated as zero, so a gradient at 0,
#: 90, 180 or 270 degrees is worked out as a single column or row.
_AXIS = 1e-9


# -- colour spaces ---------------------------------------------------------


def srgb_to_linear(encoded: np.ndarray) -> np.ndarray:
    """Undo the sRGB transfer curve. Channels from 0 to 1."""
    return np.where(
        encoded <= 0.04045, encoded / 12.92, ((encoded + 0.055) / 1.055) ** 2.4
    )


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """Apply the sRGB transfer curve. Channels from 0 to 1."""
    clipped = np.clip(linear, 0.0, 1.0)
    encoded: np.ndarray = np.where(
        clipped <= 0.0031308,
        clipped * 12.92,
        1.055 * clipped ** (1 / 2.4) - 0.055,
    )
    return encoded


_LMS_FROM_LINEAR = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ]
)
_OKLAB_FROM_LMS = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ]
)
_LMS_FROM_OKLAB = np.array(
    [
        [1.0, 0.3963377774, 0.2158037573],
        [1.0, -0.1055613458, -0.0638541728],
        [1.0, -0.0894841775, -1.2914855480],
    ]
)
_LINEAR_FROM_LMS = np.array(
    [
        [4.0767416621, -3.3077115913, 0.2309699292],
        [-1.2684380046, 2.6097574011, -0.3413193965],
        [-0.0041960863, -0.7034186147, 1.7076147010],
    ]
)


def linear_srgb_to_oklab(linear: np.ndarray) -> np.ndarray:
    """Linear sRGB to Oklab, along the last axis."""
    lms = linear @ _LMS_FROM_LINEAR.T
    lab: np.ndarray = np.cbrt(lms) @ _OKLAB_FROM_LMS.T
    return lab


def oklab_to_linear_srgb(lab: np.ndarray) -> np.ndarray:
    """Oklab to linear sRGB, along the last axis."""
    lms = (lab @ _LMS_FROM_OKLAB.T) ** 3
    linear: np.ndarray = lms @ _LINEAR_FROM_LMS.T
    return linear


def _into(space: str, encoded: np.ndarray) -> np.ndarray:
    if space == "srgb":
        return encoded
    linear = srgb_to_linear(encoded)
    return linear if space == "linear-srgb" else linear_srgb_to_oklab(linear)


def _out_of(space: str, values: np.ndarray) -> np.ndarray:
    if space == "srgb":
        clipped: np.ndarray = np.clip(values, 0.0, 1.0)
        return clipped
    linear = values if space == "linear-srgb" else oklab_to_linear_srgb(values)
    return linear_to_srgb(linear)


# -- along the gradient ----------------------------------------------------


def stop_positions(stops: Sequence[GradientStop]) -> list[float]:
    """Every stop's position, after the CSS fixup rules."""
    positions: list[float | None] = [stop.at for stop in stops]
    if positions[0] is None:
        positions[0] = 0.0
    if positions[-1] is None:
        positions[-1] = 1.0

    # No stop may sit before one earlier in the list.
    highest = -math.inf
    for index, position in enumerate(positions):
        if position is None:
            continue
        highest = max(highest, position)
        positions[index] = highest

    # Runs without positions are spread evenly between their neighbours.
    index = 0
    while index < len(positions):
        if positions[index] is not None:
            index += 1
            continue
        end = index
        while positions[end] is None:
            end += 1
        before = positions[index - 1]
        after = positions[end]
        assert before is not None and after is not None
        gaps = end - index + 1
        for offset in range(1, gaps):
            positions[index + offset - 1] = before + (after - before) * offset / gaps
        index = end

    return [float(position) for position in positions if position is not None]


def ramp(config: GradientConfig, samples: int = SAMPLES) -> np.ndarray:
    """The colour at evenly spaced points from 0 to 1, as sRGB from 0 to 1.

    Shape ``(samples, 3)``. Before the first stop and after the last, the
    colour holds, as CSS says it does.
    """
    stops = config.stops
    positions = np.array(stop_positions(stops))
    encoded = np.array([parse_hex(stop.color) for stop in stops], dtype=np.float64)
    colours = _into(config.space, encoded / 255.0)

    t = np.linspace(0.0, 1.0, samples)
    # The segment each sample falls in: the last stop at or before it. With two
    # stops at one position, `side="right"` puts the edge sample on the later
    # stop, which is the hard edge CSS describes.
    upper = np.clip(np.searchsorted(positions, t, side="right"), 1, len(stops) - 1)
    lower = upper - 1
    start, end = positions[lower], positions[upper]
    span = end - start
    safe = np.where(span > 0, span, 1.0)
    progress = np.where(span > 0, np.clip((t - start) / safe, 0.0, 1.0), 1.0)
    progress = np.where(t < positions[0], 0.0, progress)

    hints = np.array([stop.hint for stop in stops])[lower]
    exponent = np.log(0.5) / np.log(hints)
    weight = progress**exponent

    mixed = colours[lower] + (colours[upper] - colours[lower]) * weight[:, None]
    return np.asarray(_out_of(config.space, mixed))


# -- across the frame ------------------------------------------------------


def position_map(config: GradientConfig, width: int, height: int) -> np.ndarray:
    """How far along the gradient every pixel is.

    Shape ``(height, 1)`` for a gradient that only runs up or down the frame,
    ``(1, width)`` for one that only runs across it, and ``(height, width)``
    otherwise, so the common cases cost a column or a row rather than a frame.
    """
    xs = np.arange(width, dtype=np.float64)[None, :] + 0.5
    ys = np.arange(height, dtype=np.float64)[:, None] + 0.5
    angle = math.radians(config.angle)

    if config.shape == "linear":
        across, down = math.sin(angle), -math.cos(angle)
        length = abs(width * across) + abs(height * down)
        if abs(across) < _AXIS:
            return ((ys - height / 2) * down) / length + 0.5
        if abs(down) < _AXIS:
            return ((xs - width / 2) * across) / length + 0.5
        return ((xs - width / 2) * across + (ys - height / 2) * down) / length + 0.5

    cx, cy = config.center_x * width, config.center_y * height
    dx, dy = xs - cx, ys - cy

    if config.shape == "conic":
        # atan2(dx, -dy) is 0 straight up and grows clockwise, which is the
        # convention the angle is given in.
        swept = np.arctan2(dx, -dy) - angle
        return np.mod(swept, 2 * math.pi) / (2 * math.pi)

    return _radial(dx, dy, cx, cy, width, height)


def _radial(
    dx: np.ndarray, dy: np.ndarray, cx: float, cy: float, width: int, height: int
) -> np.ndarray:
    """An ellipse through the farthest corner, shaped like the closest sides."""
    corners = [(x - cx, y - cy) for x in (0, width) for y in (0, height)]
    near_x, near_y = min(cx, width - cx), min(cy, height - cy)
    if near_x <= 0 or near_y <= 0:
        # A centre on an edge has no closest-side ellipse; use a circle.
        radius = max(math.hypot(x, y) for x, y in corners)
        circle: np.ndarray = np.hypot(dx, dy) / radius
        return circle
    scale = max(math.hypot(x / near_x, y / near_y) for x, y in corners)
    rx, ry = near_x * scale, near_y * scale
    ellipse: np.ndarray = np.hypot(dx / rx, dy / ry)
    return ellipse


def gradient_float(config: GradientConfig, width: int, height: int) -> np.ndarray:
    """The gradient as sRGB from 0 to 1, float32, shaped as `position_map` is."""
    colours = ramp(config)
    where = np.clip(position_map(config, width, height), 0.0, 1.0)
    grid = np.linspace(0.0, 1.0, len(colours))
    channels = [
        np.interp(where, grid, colours[:, channel]).astype(np.float32)
        for channel in range(3)
    ]
    return np.stack(channels, axis=-1)


@lru_cache(maxsize=4)
def gradient_image(config: GradientConfig, width: int, height: int) -> np.ndarray:
    """The gradient as 8-bit RGB, ready to fill a frame from. Read only.

    Always three dimensional, ``(height, 1 or width, 3)``, so it fills a frame
    by broadcasting whichever way it runs.
    """
    values = gradient_float(config, width, height)
    if values.shape[0] == 1:
        values = np.broadcast_to(values, (height, values.shape[1], 3))
    image = np.rint(values * 255.0).astype(np.uint8)
    image.flags.writeable = False
    return image
