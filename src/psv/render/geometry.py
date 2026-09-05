"""Where every key sits on screen.

Pure arithmetic, no pixels touched. Keeping it separate means the layout can be
tested exhaustively over all 88 keys without rendering anything, and a geometry
bug shows up as a failing number rather than as a picture that looks slightly
wrong.

The keyboard runs A0 (MIDI 21) to C8 (MIDI 108): 52 white keys and 36 black
ones. White keys tile the full width edge to edge; black keys straddle the seam
between the two white keys they sit between.
"""

from __future__ import annotations

from dataclasses import dataclass

from psv.model import HIGHEST_KEY, LOWEST_KEY, is_black_key

#: Number of white keys on an 88-key piano.
WHITE_KEY_COUNT = 52

#: How far each pitch class sits along its octave, counted in white keys.
#: Black pitch classes are absent: they are positioned from the white key below.
_WHITE_ORDINAL = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}

#: White-key index of A0, subtracted so the lowest key lands at index 0.
_A0_OFFSET = 12

#: A black key is narrower than a white one. Real pianos sit near 0.55 to 0.6.
BLACK_KEY_WIDTH_RATIO = 0.58

#: Black keys are shorter than white ones, as on a real keyboard.
BLACK_KEY_LENGTH_RATIO = 0.62

#: Slice taken off each falling bar so neighbouring notes stay visually
#: separate. Without it, a chord of adjacent notes renders as one wide block.
BAR_GAP_RATIO = 0.14


def white_index(pitch: int) -> int:
    """How many white keys sit below this one, counting from A0.

    Defined for black keys too, where it gives the index of the white key
    immediately below, which is what positions the black key.
    """
    if is_black_key(pitch):
        pitch -= 1
    octave, pitch_class = divmod(pitch, 12)
    return octave * 7 + _WHITE_ORDINAL[pitch_class] - _A0_OFFSET


@dataclass(frozen=True, slots=True)
class KeyboardGeometry:
    """Pixel positions for a keyboard drawn across ``width`` pixels."""

    width: int
    height: int
    #: Bars for black-key notes are drawn this fraction of a white bar's width.
    black_bar_ratio: float = 0.6

    @property
    def white_width(self) -> float:
        return self.width / WHITE_KEY_COUNT

    @property
    def black_width(self) -> float:
        return self.white_width * BLACK_KEY_WIDTH_RATIO

    @property
    def black_height(self) -> float:
        return self.height * BLACK_KEY_LENGTH_RATIO

    def contains(self, pitch: int) -> bool:
        return LOWEST_KEY <= pitch <= HIGHEST_KEY

    def key_span(self, pitch: int) -> tuple[float, float]:
        """Left and right edge of the physical key, in pixels.

        White keys tile edge to edge. A black key is centred on the seam between
        the white key below it and the next one up.
        """
        index = white_index(pitch)
        if is_black_key(pitch):
            seam = (index + 1) * self.white_width
            half = self.black_width / 2
            return seam - half, seam + half
        left = index * self.white_width
        return left, left + self.white_width

    def visible_span(self, pitch: int, depth: float) -> tuple[float, float]:
        """The key's left and right edge ``depth`` pixels down from the top.

        A white key is not a rectangle. For the length of the black keys it is
        only the tab between them, and it widens to its full width below their
        ends. Anything drawn on a white key at full width for that whole length
        is drawn over its neighbours: the key looks like it has grown sideways
        under the black keys, which do not move.

        A black key is a rectangle, and is returned unchanged.
        """
        left, right = self.key_span(pitch)
        if is_black_key(pitch) or depth >= self.black_height:
            return left, right
        if pitch > LOWEST_KEY and is_black_key(pitch - 1):
            left = max(left, self.key_span(pitch - 1)[1])
        if pitch < HIGHEST_KEY and is_black_key(pitch + 1):
            right = min(right, self.key_span(pitch + 1)[0])
        return left, right

    def key_centre(self, pitch: int) -> float:
        left, right = self.key_span(pitch)
        return (left + right) / 2

    def bar_span(self, pitch: int) -> tuple[float, float]:
        """Left and right edge of the falling note bar, in pixels.

        Both widths are measured from the *white* bar width, so
        ``black_bar_ratio`` means exactly what the config says it means: the
        width of a black-key bar as a fraction of a white-key bar. Measuring the
        black bar from the narrower physical black key instead would shrink it
        twice and make the configured number a lie.

        The gap is what keeps two adjacent notes in a chord readable as two
        notes rather than one blob.
        """
        centre = self.key_centre(pitch)
        base = self.white_width * (1.0 - BAR_GAP_RATIO)
        width = base * self.black_bar_ratio if is_black_key(pitch) else base
        half = width / 2
        return centre - half, centre + half

    def white_pitches(self) -> tuple[int, ...]:
        return tuple(
            pitch
            for pitch in range(LOWEST_KEY, HIGHEST_KEY + 1)
            if not is_black_key(pitch)
        )

    def black_pitches(self) -> tuple[int, ...]:
        return tuple(
            pitch for pitch in range(LOWEST_KEY, HIGHEST_KEY + 1) if is_black_key(pitch)
        )
