"""Turning a note into a colour.

Two things have to be readable at once, and they are given separate channels so
neither hides the other:

* **Which hand plays it** is the hue. Left and right get distinct colour
  families, and the hue is never touched by anything else.
* **How loud it is** is the brightness, scaled between the configured ``quiet``
  and ``loud`` multipliers.

Saturation is deliberately left alone. Washing quiet notes out toward grey would
look pretty and would destroy the more important signal: at pianissimo you would
no longer be able to tell which hand is playing. Hue stays at full strength all
the way down.

Black-key bars are darkened on top of whatever colour comes out of that, so the
two channels compose rather than compete.
"""

from __future__ import annotations

from psv.config import ColorConfig
from psv.model import Hand, Note
from psv.rgb import RGB, WHITE, is_grayscale, parse_hex

__all__ = [
    "RGB",
    "WHITE",
    "blend",
    "hand_color",
    "is_grayscale",
    "note_color",
    "parse_hex",
    "pedal_color",
    "scale",
    "shaded",
    "velocity_brightness",
]

#: MIDI velocity runs 1 to 127, so there are 126 steps between the extremes.
_VELOCITY_RANGE = 126.0


def scale(colour: RGB, factor: float) -> RGB:
    """Multiply a colour toward black.

    Multiplying can only ever reduce a channel, so a colour built this way can
    never clip past 255 however the config is set.
    """
    return (
        round(colour[0] * factor),
        round(colour[1] * factor),
        round(colour[2] * factor),
    )


def shaded(colour: RGB, shade: float) -> RGB:
    """Move a colour toward black or toward white, keeping its hue.

    Negative darkens, positive lightens, zero leaves it alone. Used for the
    note-bar outline, where staying a shade of the bar's own colour is what
    keeps which hand is playing readable at the border.
    """
    if shade < 0:
        return scale(colour, 1.0 + shade)
    return blend(colour, WHITE, shade)


def velocity_brightness(velocity: int, config: ColorConfig) -> float:
    """Map MIDI velocity onto the configured brightness range.

    Linear and monotonic: a louder note is never drawn darker than a quieter
    one. Velocities outside 1-127 are clamped rather than allowed to overshoot
    the range.
    """
    clamped = min(max(velocity, 1), 127)
    position = (clamped - 1) / _VELOCITY_RANGE
    return config.quiet + position * (config.loud - config.quiet)


def hand_color(hand: Hand, config: ColorConfig) -> RGB:
    """The hue family for a hand.

    An unassigned note gets a neutral colour rather than being forced into one
    hand or the other, so a score that has not been through hand assignment
    still renders and visibly says so.
    """
    if hand is Hand.LEFT:
        return parse_hex(config.left_hand)
    if hand is Hand.RIGHT:
        return parse_hex(config.right_hand)
    return parse_hex(config.unassigned)


def note_color(note: Note, config: ColorConfig, black_key_darkening: float) -> RGB:
    """The final bar colour: hue from the hand, brightness from the velocity.

    ``black_key_darkening`` is applied last, on top of the dynamics colour, so a
    black-key note is darker than the white-key note of the same loudness rather
    than being painted a different colour entirely.
    """
    base = hand_color(note.hand, config)
    lit = scale(base, velocity_brightness(note.velocity, config))
    if note.is_black_key:
        return scale(lit, 1.0 - black_key_darkening)
    return lit


def pedal_color(depth: int, config: ColorConfig) -> RGB:
    """Pedal lane colour, with depth shown the same way loudness is.

    Half-pedalling is real technique and the parser keeps the depth, so a
    shallow press is drawn dimmer than a full one instead of looking identical.
    """
    base = parse_hex(config.pedal)
    clamped = min(max(depth, 1), 127)
    position = (clamped - 1) / _VELOCITY_RANGE
    return scale(base, config.quiet + position * (config.loud - config.quiet))


def blend(under: RGB, over: RGB, alpha: float) -> RGB:
    """Composite ``over`` onto ``under``. Used for the faint grid lines."""
    alpha = min(max(alpha, 0.0), 1.0)
    return (
        round(under[0] + (over[0] - under[0]) * alpha),
        round(under[1] + (over[1] - under[1]) * alpha),
        round(under[2] + (over[2] - under[2]) * alpha),
    )
