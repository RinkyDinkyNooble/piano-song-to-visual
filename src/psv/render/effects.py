"""Optional visual effects, off by default.

A practice aid and a piece of spectacle want opposite things. This module is the
second one, and nothing in it runs unless it was asked for by name.

Two rules shape every effect here, and they rule out whole categories.

**Nothing may read the previous frame.** ``render_frame`` is a pure function of
the score and a time, which is what the reference images and the determinism
guarantee rest on. So there is no motion blur, no accumulating trail, and no
particle system carrying state between frames.

**Spans are rendered by separate processes.** Frame-to-frame state would break at
every span boundary and leave a visible seam every few seconds. A second and
unrelated reason for the same rule, which is a good sign it is the right one.

The way round both is to derive the effect from the score rather than from
history. A trail is "notes that crossed the line in the last 400 ms", which is a
pure function of time. A spark's position comes from hashing the note and the
spark index, so it is deterministic, identical in every process, and free.

Everything is measured in fractions of the frame height rather than in pixels.
A glow rising 47 pixels above the strike line is a different effect at 720p and
at 1080p, and a config value that means something different per resolution is a
config value nobody can set once.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from psv.config import VisualConfig
from psv.model import Note, Score, is_black_key
from psv.render.color import RGB, note_color, parse_hex
from psv.render.geometry import KeyboardGeometry

if TYPE_CHECKING:  # pragma: no cover - import cycle, types only
    from psv.render.frame import Frame, Layout

#: How far the background lifts at a full-strength pulse, in grey levels.
PULSE_LIFT = 34

#: How long a note keeps lifting the background after it lands.
PULSE_DECAY_S = 0.32

#: Onset weight at which the pulse is already at full strength. Normalised so
#: one note at a normal velocity is a visible lift: dividing by the weight of a
#: chord meant a single-line passage moved the background by six grey levels,
#: which is not an effect.
PULSE_FULL = 1.2

#: How long a strike flash lasts.
FLASH_S = 0.13

#: How long a note keeps a trail after it lands.
TRAIL_S = 0.40

#: How long sparks live.
SPARK_S = 0.45

#: Bloom works on a shrunken copy. It is low-frequency by definition, so the
#: picture is the same and the cost is not: 162 ms at full resolution against
#: 26 ms at an eighth. It cannot go lower, because past that the cost is the
#: full-frame composite rather than the blur.
#:
#: The shrink has to average the block rather than sample it. See `bloom`.
#:
#: How many rows that copy has, rather than how much to shrink by. A fixed
#: eighth put a 320x180 frame's bloom on a 40x22 image, where a glow eleven
#: pixels tall does not survive being sampled and bloom quietly did nothing.
BLOOM_ROWS = 135

#: How bright a pixel has to be before bloom picks it up at all, and how
#: much brighter it has to be before it blooms at full strength. The gap
#: between them is a soft knee: a pixel just over the floor contributes a
#: little and one at white contributes all of itself.
#:
#: A hard threshold made bloom pop on. A bar brightens with velocity and fades
#: with a theme's `quiet`, so it crosses any fixed line mid-fall, and at that
#: moment the whole glow appeared at once. Blooming the light *above* the floor
#: rather than the whole pixel is also what real bloom does: it is the excess
#: that spills, not the thing itself.
BLOOM_FLOOR = 105.0

#: Radius of each of the two box blurs, in shrunken pixels.
#:
#: Free, near enough: measured at 1080p, radius 5 costs 0.5 ms more than radius
#: 2, because the cost is all in the full-size composite rather than in the
#: blur. Two box blurs in a row approximate a Gaussian well enough that nothing
#: here needs a real one.
BLOOM_BLUR = 5

#: How hard the blurred light is added back.
#:
#: Higher than the 2.2 this used before the soft knee, and by about the amount
#: the knee takes away: a bar at luma 210 used to contribute all of itself and
#: now contributes (210 - 105) / 150 of itself, so the gain has to make that up
#: or turning the knee on would have read as turning bloom down.
BLOOM_GAIN = 3.6

#: Luma weights, Rec. 601. Pillow's ``convert("L")`` uses these same weights,
#: which is what lets the knee be a lookup table on one channel instead of a
#: dot product over three. A test pins the two together.
LUMA = (0.299, 0.587, 0.114)

#: The soft knee as a 256-entry table: luma in, how much of the pixel blooms
#: out, 0 to 255. Built once, applied by Pillow in C.
KNEE_LUT: tuple[int, ...] = tuple(
    min(255, round(255 * max(0.0, value - BLOOM_FLOOR) / (255.0 - BLOOM_FLOOR)))
    for value in range(256)
)

#: How far a halo reaches past its bar at full intensity, as a fraction of the
#: frame height. Split into shells, one per whole pixel it covers.
HALO_SPREAD = 0.0118

#: Most shells a halo is ever drawn in. Past this the shells are thinner than
#: the eye can use and the cost is real: a halo is four rectangles per shell per
#: visible bar.
HALO_SHELLS = 5


@dataclass(frozen=True, slots=True)
class Canvas:
    """What an effect needs to know about the frame it is drawing into.

    Assembled once per frame by the renderer and passed to every effect, so an
    effect never recomputes the layout and never sees the config's raw numbers
    without the frame size they have to be read against.
    """

    score: Score
    config: VisualConfig
    layout: Layout
    geometry: KeyboardGeometry
    time: float

    @property
    def line(self) -> int:
        """Where notes land: the top edge of the keyboard."""
        return self.layout.keyboard_top

    @property
    def keys_height(self) -> int:
        return self.layout.height - self.layout.keyboard_top

    def up(self, fraction: float) -> float:
        """A distance given as a fraction of the frame height, in pixels."""
        return fraction * self.layout.height

    def struck(self, window: float) -> Iterator[tuple[Note, float]]:
        """Notes that landed within ``window`` seconds, and how old each is.

        Age runs 0 at the moment it lands to 1 at the end of the window, so
        every effect fades on the same scale.
        """
        for note in self.score.notes_between(self.time - window, self.time + 1e-3):
            age = self.time - note.start
            if 0.0 <= age <= window and self.geometry.contains(note.pitch):
                yield note, age / window

    def sounding(self) -> Iterator[Note]:
        for note in self.score.notes_between(self.time, self.time + 1e-3):
            if note.start <= self.time < note.end and self.geometry.contains(
                note.pitch
            ):
                yield note

    def falling(self) -> Iterator[tuple[Note, float, float]]:
        """Every visible bar, with the top and bottom it was drawn at."""
        window_end = self.time + self.layout.lookahead_s
        for note in self.score.notes_between(self.time, window_end):
            if not self.geometry.contains(note.pitch):
                continue
            bottom = min(self.layout.time_to_y(note.start, self.time), self.line)
            top = self.layout.time_to_y(note.end, self.time)
            if bottom <= 0 or top >= self.line:
                continue
            yield note, top, bottom

    def colour(self, note: Note) -> RGB:
        return note_color(note, self.config.colors, self.config.black_key_darkening)


# -- drawing -------------------------------------------------------------


def _region(frame: Frame, x0: float, y0: float, x1: float, y1: float) -> Frame | None:
    height, width = frame.shape[:2]
    left, right = max(0, round(x0)), min(width, round(x1))
    top, bottom = max(0, round(y0)), min(height, round(y1))
    if right <= left or bottom <= top:
        return None
    return frame[top:bottom, left:right]


def add_light(
    frame: Frame,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    colour: RGB,
    alpha: float,
) -> None:
    """Add light to a rectangle, clipped to the frame.

    Additive rather than a blend, because these are lights on a dark picture: a
    glow over an already-lit key has to brighten it, and an alpha blend of the
    key's own colour would be invisible.
    """
    if alpha <= 0.0:
        return
    region = _region(frame, x0, y0, x1, y1)
    if region is None:
        return
    tint = np.array(colour, dtype=np.float32) * float(alpha)
    region[:] = np.clip(region.astype(np.float32) + tint, 0, 255).astype(np.uint8)


def add_light_column(
    frame: Frame,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    colour: RGB,
    alphas: np.ndarray,
) -> None:
    """Add light down a rectangle, a different strength on every row.

    One array operation rather than a loop of thin rectangles. Written as a loop
    first, which made a glow cost the same at every intensity, because the loop
    ran the same number of times whatever the alpha was.

    ``alphas`` covers the unclipped rectangle and is sliced to whatever is on
    screen, so a glow half off the bottom keeps the shape it would have had.
    """
    height, width = frame.shape[:2]
    full_top, full_bottom = round(y0), round(y1)
    left, right = max(0, round(x0)), min(width, round(x1))
    top, bottom = max(0, full_top), min(height, full_bottom)
    if right <= left or bottom <= top:
        return
    rows = alphas[top - full_top : bottom - full_top]
    region = frame[top:bottom, left:right]
    tint = np.asarray(colour, dtype=np.float32) * rows.astype(np.float32)[:, None]
    region[:] = np.clip(region.astype(np.float32) + tint[:, None, :], 0, 255).astype(
        np.uint8
    )


def _black_key_gaps(
    canvas: Canvas, pitch: int, x0: float, x1: float
) -> list[tuple[float, float]]:
    """The parts of ``x0``-``x1`` that no *other* key's black key covers.

    The note's own key is never an occluder. A black key's own light belongs on
    it; it is the neighbours it must not paint over.

    Only the immediate neighbours are checked. Everything that draws on the
    keyboard here is centred on one key and no wider than a key plus a little,
    so a black key two away cannot be in the way.
    """
    spans: list[tuple[float, float]] = []
    cursor = x0
    for other in range(pitch - 2, pitch + 3):
        if other == pitch or not is_black_key(other):
            continue
        if not canvas.geometry.contains(other):
            continue
        left, right = canvas.geometry.key_span(other)
        if right <= cursor or left >= x1:
            continue
        if left > cursor:
            spans.append((cursor, left))
        cursor = max(cursor, right)
    if cursor < x1:
        spans.append((cursor, x1))
    return spans


def _behind_keys(
    canvas: Canvas, pitch: int, x0: float, y0: float, x1: float, y1: float
) -> Iterator[tuple[float, float, float, float]]:
    """Cut a rectangle into the pieces a black key does not cover.

    The keyboard is drawn whites first so blacks sit on top of them, and effects
    run after the keyboard, which puts them on top of everything. Anything an
    effect draws down onto the keys therefore has to do the occluding itself, or
    a struck white key paints its trail across the front half of both its black
    neighbours.

    Above the keys and below the black keys' ends the rectangle is whole; only
    the band where black keys actually are gets cut.
    """
    black_end = canvas.line + 1 + canvas.geometry.black_height

    # Above the keyboard, whole. Clamped to y1 as well as to the keyboard line:
    # a rectangle that ends before the keys must not be stretched down to them,
    # which is what a halo's lower edge does on every bar still falling.
    above = min(y1, canvas.line)
    if y0 < above:
        yield x0, y0, x1, above

    # Through the black keys, cut around them.
    band_top = max(y0, canvas.line)
    band_bottom = min(y1, black_end)
    if band_top < band_bottom:
        for left, right in _black_key_gaps(canvas, pitch, x0, x1):
            yield left, band_top, right, band_bottom

    # Past their ends, whole again.
    if y1 > black_end:
        yield x0, max(y0, black_end), x1, y1


def add_light_behind_keys(
    frame: Frame,
    canvas: Canvas,
    pitch: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    colour: RGB,
    alpha: float,
) -> None:
    """`add_light`, but passing behind the black keys."""
    for left, top, right, bottom in _behind_keys(canvas, pitch, x0, y0, x1, y1):
        add_light(frame, left, top, right, bottom, colour, alpha)


def add_light_column_behind_keys(
    frame: Frame,
    canvas: Canvas,
    pitch: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    colour: RGB,
    alphas: np.ndarray,
) -> None:
    """`add_light_column`, but passing behind the black keys.

    Each piece takes the slice of ``alphas`` for the rows it covers, so the
    fade down the streak is the one it would have had undivided rather than
    restarting in every piece.
    """
    base = round(y0)
    full = max(1e-6, x1 - x0)
    for left, top, right, bottom in _behind_keys(canvas, pitch, x0, y0, x1, y1):
        first, last = round(top) - base, round(bottom) - base
        if last <= first:
            continue
        # Conserve the light rather than the brightness. Squeezed from a bar
        # into the tab between two black keys, the same alpha over 40% of the
        # width stops reading as a wash and starts reading as a hard line.
        share = (right - left) / full
        add_light_column(
            frame, left, top, right, bottom, colour, alphas[first:last] * share
        )


def lighten(colour: RGB, amount: float) -> RGB:
    """Toward white, keeping the hue that says which hand is playing."""
    return (
        int(colour[0] + (255 - colour[0]) * amount),
        int(colour[1] + (255 - colour[1]) * amount),
        int(colour[2] + (255 - colour[2]) * amount),
    )


def spark_random(*seed: int) -> float:
    """Repeatable randomness from integers, in 0 to 1.

    FNV-1a. Not a good hash in general, good enough for scattering sparks, and
    it is the reason particles can be stateless: the same note and the same
    spark index give the same number in every process and on every run.
    """
    value = 2166136261
    for part in seed:
        value = ((value ^ (int(part) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    return value / 0xFFFFFFFF


# -- the effects ---------------------------------------------------------


def strike_flash(frame: Frame, canvas: Canvas, k: float) -> None:
    """A burst at the line as a note lands, fading over about 130 ms.

    The one effect here with real learning value: it confirms the moment a note
    was supposed to be played, which is the thing you are trying to feel.
    """
    for note, aged in canvas.struck(FLASH_S):
        fade = (1.0 - aged) ** 1.6
        left, right = canvas.geometry.bar_span(note.pitch)
        centre, half = (left + right) / 2, (right - left) / 2
        glow = lighten(canvas.colour(note), 0.7)
        for step in range(6):
            spread = 1.0 + step * 0.55
            height = canvas.up(0.0097 + step * 0.0153 * k)
            add_light_behind_keys(
                frame,
                canvas,
                note.pitch,
                centre - half * spread,
                canvas.line - height,
                centre + half * spread,
                canvas.line + height * 0.35,
                glow,
                0.42 * k * fade / (step + 1),
            )


def key_glow(frame: Frame, canvas: Canvas, k: float) -> None:
    """The pressed key lit beyond the highlight it already gets."""
    if k <= 0.0:
        return
    rise = max(1, int(canvas.up(0.065 * k)))
    depth = max(1, int(canvas.keys_height * 0.6))
    # Frame-invariant, so worked out once rather than per sounding note.
    above = 0.55 * k * (1 - np.arange(rise - 1, -1, -1) / rise) ** 2
    below = 0.30 * k * (1 - np.arange(depth) / depth)

    # Where the black keys end, and so where a white key stops being a tab
    # between them and becomes its full width.
    waist = min(depth, max(0, int(canvas.geometry.black_height)))

    for note in canvas.sounding():
        left, right = canvas.geometry.key_span(note.pitch)
        glow = lighten(canvas.colour(note), 0.35)
        add_light_column(
            frame, left, canvas.line - rise, right, canvas.line, glow, above
        )

        # Down the key, following its shape rather than its bounding box. A
        # white key lit at full width for the length of the black keys spills
        # over the half of each neighbour that is sitting in front of it.
        narrow_left, narrow_right = canvas.geometry.visible_span(note.pitch, 0.0)
        add_light_column(
            frame,
            narrow_left,
            canvas.line,
            narrow_right,
            canvas.line + waist,
            glow,
            below[:waist],
        )
        if depth > waist:
            add_light_column(
                frame,
                left,
                canvas.line + waist,
                right,
                canvas.line + depth,
                glow,
                below[waist:],
            )


def trail(frame: Frame, canvas: Canvas, k: float) -> None:
    """A fading streak down the key for a note that has just landed.

    Lightened and added rather than blended: the key underneath is already lit
    in the note's own colour, so blending that colour over it draws nothing.
    """
    if k <= 0.0:
        return
    for note, aged in canvas.struck(TRAIL_S):
        fade = (1.0 - aged) ** 1.2
        left, right = canvas.geometry.bar_span(note.pitch)
        colour = lighten(canvas.colour(note), 0.5)
        reach = max(1, round(canvas.keys_height * (0.4 + 1.1 * aged)))
        alphas = 0.75 * k * fade * (1 - np.arange(reach) / reach) ** 1.4
        add_light_column_behind_keys(
            frame,
            canvas,
            note.pitch,
            left,
            canvas.line,
            right,
            canvas.line + reach,
            colour,
            alphas,
        )


def particles(frame: Frame, canvas: Canvas, k: float) -> None:
    """Sparks thrown from the strike point.

    Every spark's whole life comes out of a hash of the note and the spark
    index, so nothing is carried between frames. The birth delay matters more
    than it looks: without it every spark of a note is the same age and the
    spray draws as a clean arc rather than as sparks.
    """
    count = max(1, int(18 * k))
    for index, (note, aged) in enumerate(canvas.struck(SPARK_S)):
        age = aged * SPARK_S
        fade = (1.0 - aged) ** 1.3
        centre = canvas.geometry.key_centre(note.pitch)
        glow = lighten(canvas.colour(note), 0.8)
        for spark in range(count):
            lived = age - 0.09 * spark_random(note.pitch, index, spark, 4)
            if lived <= 0:
                continue
            angle = (spark_random(note.pitch, index, spark, 1) - 0.5) * math.pi * 0.9
            speed = canvas.up(0.188 + 0.417 * spark_random(note.pitch, index, spark, 2))
            grain = spark_random(note.pitch, index, spark, 3)
            size = canvas.up(0.0028 + 0.0042 * grain)
            x = centre + math.sin(angle) * speed * lived
            y = (
                canvas.line
                - math.cos(angle) * speed * lived * 0.85
                + canvas.up(0.639) * lived * lived
            )
            if y > canvas.line:
                # Below the line it would be drawn over the keyboard, which is
                # a different effect and a worse one.
                continue
            add_light(frame, x, y, x + size, y + size, glow, min(1.0, 1.4 * fade * k))


def halo(frame: Frame, canvas: Canvas, k: float) -> None:
    """A soft edge around every falling bar.

    The one with negative learning value: it smears adjacent notes together,
    which is exactly what the gap between bars exists to prevent.

    **Shells that abut, not rectangles that nest.** Each shell used to be drawn
    from the bar's edge out to its own distance, so every shell covered all the
    ones inside it and the pixel against the bar received all five alphas while
    the outermost received one. That is a hard rim about five times brighter
    than the falloff asks for, and where a pitch repeats quickly the rims of
    consecutive bars join into an unbroken line down the screen, far more
    visible than the bars. Each shell now covers only its own band.

    **The shells are sized in whole pixels.** At a modest intensity the whole
    glow is a few pixels wide, so five shells of it are sub-pixel: rounding
    threw most of them away and stacked the rest on one column, which is the
    other half of the same artefact. The count falls with the spread so a shell
    is never thinner than a pixel.

    The ring follows the bar's corners. Drawn as four full-width strips it is a
    rectangle, and a rectangle of light around a rounded bar puts the corners
    back: the bar reads as square again, with its own corners merely darker than
    the glow around them. So each strip stops short by however far the rounding
    reaches in, leaving the light to trace the straight edges only.
    """
    spread = canvas.up(HALO_SPREAD * k)
    shells = max(1, min(HALO_SHELLS, int(spread)))
    if spread <= 0.0:
        return

    for note, top, bottom in canvas.falling():
        left, right = canvas.geometry.bar_span(note.pitch)
        inset = _corner_inset(canvas.config.note_radius, left, right, top, bottom)
        glow = canvas.colour(note)
        for shell in range(shells):
            inner = spread * shell / shells
            outer = spread * (shell + 1) / shells
            # The falloff the nested version was trying to have, now that each
            # band is drawn once and only once.
            alpha = 0.30 * k / (shell + 1)
            near, far = left - outer + inset, right + outer - inset
            add_light(frame, near, top - outer, far, top - inner, glow, alpha)
            add_light_behind_keys(
                frame,
                canvas,
                note.pitch,
                near,
                bottom + inner,
                far,
                bottom + outer,
                glow,
                alpha,
            )
            add_light(
                frame,
                left - outer,
                top + inset,
                left - inner,
                bottom - inset,
                glow,
                alpha,
            )
            add_light(
                frame,
                right + inner,
                top + inset,
                right + outer,
                bottom - inset,
                glow,
                alpha,
            )


def _corner_inset(
    radius: float, left: float, right: float, top: float, bottom: float
) -> float:
    """How far a bar's corner rounding reaches in from each edge, in pixels.

    The same cap the renderer applies when it rounds the corners: a fraction of
    the bar's width, and never more than half of either side.
    """
    if radius <= 0.0:
        return 0.0
    width, height = right - left, bottom - top
    return max(0.0, min(width * radius, width / 2, height / 2))


def _box_blur(plane: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(plane, radius, mode="edge")
    out = np.cumsum(padded, axis=0)
    out = out[2 * radius :] - out[: -2 * radius]
    out = np.cumsum(out, axis=1)
    out = out[:, 2 * radius :] - out[:, : -2 * radius]
    return out / ((2 * radius) ** 2)


def _bright_part(area: Frame, shrink: int) -> np.ndarray:
    """The light worth spreading, shrunk, as float.

    Three C loops and no full-resolution numpy: a lookup table turns luma into
    how much of a pixel blooms, a multiply applies it, and a box reduce shrinks
    the result. The numpy equivalent of the same arithmetic is 33 ms against
    16 ms here, and the point-sampled version it replaces was 0.1 ms and wrong.

    ``convert("L")`` uses the Rec. 601 weights `LUMA` names, so the table is
    indexed by the same luma the float version computed.
    """
    from PIL import Image, ImageChops

    picture = Image.fromarray(area, mode="RGB")
    weight = picture.convert("L").point(KNEE_LUT)
    lit = ImageChops.multiply(picture, Image.merge("RGB", (weight, weight, weight)))
    if shrink > 1:
        lit = lit.reduce(shrink)
    return np.asarray(lit, dtype=np.float32)


def bloom(frame: Frame, canvas: Canvas, k: float) -> None:
    """The light above the strike line, blurred and added back.

    The expensive one by a long way, and the only one that cannot be made
    local: being global is what it is.

    **Only the falling area.** The white keys are the brightest thing on screen
    by a wide margin, so a bloom over the whole frame is mostly a bloom of the
    keyboard, which washes the picture and glows the one part of it that is not
    music. Reading and writing above ``canvas.line`` leaves the keys alone and
    blooms the notes, which is the light worth spreading. It also makes the
    effect cheaper, since the keyboard is a sixth of the frame.

    **The knee runs before the shrink, and the shrink averages.** Both halves
    of that are load-bearing and neither works alone. Taking every sixth pixel
    made a bar's glow depend on where the sampling grid happened to fall on it,
    so two identical notes a semitone apart bloomed by amounts that differed by
    a third. Averaging the block instead fixes that only if what is averaged is
    linear in the light, and the knee is not: a block half covered by a bar
    averages to a dimmer pixel, which the knee then discounts again, so a
    partly covered block loses most of its light twice over. Weighing each full
    resolution pixel first and averaging afterwards conserves it.
    """
    area = frame[: canvas.line]
    if area.shape[0] < 2:
        return
    shrink = max(1, min(area.shape[0] // BLOOM_ROWS, *area.shape[:2]))
    bright = _bright_part(area, shrink)
    blurred = np.dstack(
        [_box_blur(bright[:, :, band], BLOOM_BLUR) for band in range(3)]
    )
    blurred = np.dstack(
        [_box_blur(blurred[:, :, band], BLOOM_BLUR) for band in range(3)]
    )
    light = np.clip(blurred * (BLOOM_GAIN * k), 0, 255).astype(np.uint8)
    grown = _upscale(light, area.shape[1], area.shape[0])
    area[:] = np.clip(area.astype(np.int16) + grown.astype(np.int16), 0, 255).astype(
        np.uint8
    )


def _upscale(light: np.ndarray, width: int, height: int) -> np.ndarray:
    """Smoothly stretch the shrunken bloom back to full size.

    Repeating pixels is what made bloom arrive as hard squares: at 1080p the
    shrink is 6, so every blurred pixel became a 6x6 block, and against a near
    black background the eye picks those out easily. Blurring harder first only
    lowered the contrast between neighbouring blocks; it did not stop them being
    blocks.

    Bilinear in numpy costs 57 ms and a full-resolution smoothing pass 61 ms,
    both measured, against 6.3 ms for the repeat. Pillow's C resize does the
    same job in 9.5 ms, so it wins on the only axis that was ever in question.

    The gain is applied before this, not after, so what is being stretched is
    the light to add, already in 0-255. That is why it can travel as bytes: the
    values are about to be added to a uint8 frame regardless.

    Stretching in float instead was tried and dropped. It costs 17 ms more a
    frame and changes at most one grey level on 2% of the pixels, because the
    frame these values are added to is itself 8-bit: there is no finer step for
    the extra precision to land on. The long flat runs in a shallow falloff are
    the same length either way, so they are a flat light field rather than a
    quantisation band.
    """
    from PIL import Image

    stretched = Image.fromarray(light, mode="RGB").resize(
        (width, height), Image.Resampling.BILINEAR
    )
    return np.asarray(stretched)


#: Effects that draw into a finished frame, in the order they are named in the
#: config.
#:
#: The order matters less than it looks. All of these but `bloom` only add
#: light, and addition commutes, so a halo under particles really is the same
#: picture as particles under a halo until something saturates. `bloom` reads
#: the frame it is given, so where it sits in the list changes what it finds.
#: That is why this is a list and not a set.
PAINTERS: dict[str, Callable[[Frame, Canvas, float], None]] = {
    "strike_flash": strike_flash,
    "key_glow": key_glow,
    "trail": trail,
    "particles": particles,
    "halo": halo,
    "bloom": bloom,
}


def pulse_lift(score: Score, time: float, intensity: float) -> float:
    """How far the background lifts right now, in grey levels.

    ``pulse`` is the one effect that does not draw. It changes the colour the
    background is about to be filled with, which is why it is free: the fill
    happens either way. Brightening the finished frame instead would cost a
    full-frame blend and would lift the notes and the keyboard along with the
    background, which is not the effect.

    Driven by what was played rather than by the tempo map. Pulsing on the beat
    is a metronome you can see: it fires whether or not anything was played and
    is indifferent to how hard. The grid already draws the beat, and does it
    without moving.
    """
    weight = 0.0
    for note in score.notes_between(time - PULSE_DECAY_S, time + 1e-3):
        age = time - note.start
        if 0.0 <= age <= PULSE_DECAY_S:
            aged = age / PULSE_DECAY_S
            weight += (note.velocity / 127.0) * (1.0 - aged) ** 2
    if weight <= 0.0:
        return 0.0
    return PULSE_LIFT * min(1.0, weight / PULSE_FULL) * intensity


#: Every effect there is, including the one that does not draw. This is what
#: the config validates a `kind` against.
KINDS: tuple[str, ...] = (*PAINTERS, "pulse")


def background_for(config: VisualConfig, score: Score, time: float) -> str:
    """The background colour this frame should be filled with.

    Unchanged unless a `pulse` effect is configured, which is the only thing
    that can move it.
    """
    lift = 0.0
    for effect in config.effects:
        if effect.kind == "pulse":
            lift += pulse_lift(score, time, effect.intensity)
    if lift <= 0.0:
        return config.background
    # The background is required to be grayscale, so one channel says it all.
    grey = parse_hex(config.background)[0]
    value = min(255, grey + round(lift))
    return f"#{value:02x}{value:02x}{value:02x}"


def apply_effects(
    frame: Frame,
    score: Score,
    config: VisualConfig,
    time: float,
    layout: Layout,
    geometry: KeyboardGeometry,
) -> None:
    """Draw every configured effect over a finished frame, in order."""
    if not config.effects:
        return
    canvas = Canvas(
        score=score,
        config=config,
        layout=layout,
        geometry=geometry,
        time=time,
    )
    for effect in config.effects:
        painter = PAINTERS.get(effect.kind)
        if painter is not None:
            painter(frame, canvas, effect.intensity)
