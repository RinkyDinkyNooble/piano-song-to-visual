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
from psv.model import Note, Score
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
#: How many rows that copy has, rather than how much to shrink by. A fixed
#: eighth put a 320x180 frame's bloom on a 40x22 image, where a glow eleven
#: pixels tall does not survive being sampled and bloom quietly did nothing.
BLOOM_ROWS = 135

#: How bright a pixel has to be before bloom picks it up.
BLOOM_FLOOR = 150


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
            add_light(
                frame,
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

    for note in canvas.sounding():
        left, right = canvas.geometry.key_span(note.pitch)
        glow = lighten(canvas.colour(note), 0.35)
        add_light_column(
            frame, left, canvas.line - rise, right, canvas.line, glow, above
        )
        add_light_column(
            frame, left, canvas.line, right, canvas.line + depth, glow, below
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
        add_light_column(
            frame, left, canvas.line, right, canvas.line + reach, colour, alphas
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
    """
    for note, top, bottom in canvas.falling():
        left, right = canvas.geometry.bar_span(note.pitch)
        glow = canvas.colour(note)
        for ring in range(1, 6):
            pad = canvas.up(ring * 0.00236 * k)
            alpha = 0.30 * k / (ring * 1.4)
            add_light(frame, left - pad, top - pad, right + pad, top, glow, alpha)
            add_light(frame, left - pad, bottom, right + pad, bottom + pad, glow, alpha)
            add_light(frame, left - pad, top, left, bottom, glow, alpha)
            add_light(frame, right, top, right + pad, bottom, glow, alpha)


def _box_blur(plane: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(plane, radius, mode="edge")
    out = np.cumsum(padded, axis=0)
    out = out[2 * radius :] - out[: -2 * radius]
    out = np.cumsum(out, axis=1)
    out = out[:, 2 * radius :] - out[:, : -2 * radius]
    return out / ((2 * radius) ** 2)


def bloom(frame: Frame, canvas: Canvas, k: float) -> None:
    """The brightest pixels blurred and added back.

    The expensive one, by a long way, and the only one here that cannot be made
    local: being global is what it is. It also finds the white keys, because
    they are the brightest thing on screen.
    """
    del canvas
    shrink = max(1, frame.shape[0] // BLOOM_ROWS)
    small = frame[::shrink, ::shrink].astype(np.float32)
    luma = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    bright = small * (luma > BLOOM_FLOOR)[:, :, None]
    blurred = np.dstack([_box_blur(bright[:, :, band], 2) for band in range(3)])
    blurred = np.dstack([_box_blur(blurred[:, :, band], 2) for band in range(3)])
    grown = np.repeat(np.repeat(blurred, shrink, axis=0), shrink, axis=1)
    grown = grown[: frame.shape[0], : frame.shape[1]]
    frame[:] = np.clip(
        frame.astype(np.int16) + (grown * (2.2 * k)).astype(np.int16), 0, 255
    ).astype(np.uint8)


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
