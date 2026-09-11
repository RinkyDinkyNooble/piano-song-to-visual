"""Making a line of text fit the space there is for it.

One function, because there were three copies of the bug it fixes: the title
card, and two scripts outside the package that draw their own cards. All three
picked a type size as a fraction of the frame height and drew at it, so a long
title ran off the side of a frame nobody looked at until the render finished.

Shrink first, wrap second. That order is the whole design. A title set a little
smaller still reads as a title; the same title broken across two lines changes
what the card looks like, so it is what happens when shrinking has run out
rather than the first thing tried.

Measuring is left to the caller, through `measure`. The card draws its letters
one at a time to get tracking Pillow has no setting for, so the width of a
string is not something this module could work out on its own — and taking a
callable means the arithmetic can be tested without a font file, on a machine
where the fonts differ, or where there are none.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: How far below the asked-for size the text may be shrunk before wrapping is
#: tried instead, as a fraction of it. Below about this the line stops looking
#: like the thing it was meant to be and starts looking like a mistake.
FLOOR_SHARE = 0.55

#: No text is set smaller than this, whatever the frame size.
MIN_SIZE = 10


@dataclass(frozen=True, slots=True)
class Fitted:
    """Text that will fit, and the size to set it at."""

    lines: tuple[str, ...]
    size: int
    #: False when even the smallest size and the allowed number of lines were
    #: not enough. The text is still returned, because a title card that is
    #: slightly too wide beats a render that stopped.
    fits: bool = True


def fit_text(
    text: str,
    *,
    width: float,
    size: int,
    measure: Callable[[str, int], float],
    max_lines: int = 2,
    floor: int | None = None,
) -> Fitted:
    """Fit ``text`` into ``width``, starting from ``size`` and going down.

    ``measure(text, size)`` returns how wide that string is when set at that
    size, in the same units as ``width``.

    Returns the largest size at which the text fits on one line. Failing that,
    wraps at spaces into at most ``max_lines`` and returns the largest size at
    which every line fits. Failing that too, returns the smallest size with the
    best wrap it found and says `fits` is False, rather than raising: the
    caller is part way through drawing a frame.
    """
    text = text.strip()
    if not text:
        return Fitted(lines=(), size=size)

    smallest = max(MIN_SIZE, floor if floor is not None else round(size * FLOOR_SHARE))
    smallest = min(smallest, size)

    def fits_at(lines: tuple[str, ...], at: int) -> bool:
        return all(measure(line, at) <= width for line in lines)

    one = (text,)
    if fits_at(one, size):
        return Fitted(lines=one, size=size)

    found = _largest_that_fits(one, smallest, size, fits_at)
    if found is not None:
        return Fitted(lines=one, size=found)

    for count in range(2, max_lines + 1):
        wrapped = _wrap(text, count)
        if len(wrapped) < count:
            continue  # too few words to split this far; a wider wrap cannot help
        if fits_at(wrapped, size):
            return Fitted(lines=wrapped, size=size)
        found = _largest_that_fits(wrapped, smallest, size, fits_at)
        if found is not None:
            return Fitted(lines=wrapped, size=found)

    # Out of room. Hand back the most compact arrangement there was.
    best = _wrap(text, max_lines)
    return Fitted(lines=best, size=smallest, fits=False)


def _largest_that_fits(
    lines: tuple[str, ...],
    low: int,
    high: int,
    fits_at: Callable[[tuple[str, ...], int], bool],
) -> int | None:
    """Binary search for the biggest size in [low, high] that fits.

    Text width grows with type size, so the sizes that fit are a run from the
    bottom and the search is sound. Hinting can make a glyph a fraction wider
    at one size than the next up, which moves the boundary by a point and never
    breaks the shape of it.
    """
    if not fits_at(lines, low):
        return None
    best = low
    while low <= high:
        middle = (low + high) // 2
        if fits_at(lines, middle):
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _wrap(text: str, count: int) -> tuple[str, ...]:
    """Split into ``count`` lines at spaces, as evenly as the words allow.

    Only at spaces. Breaking inside a word needs a hyphenation dictionary to
    not look wrong, and a title with one very long word is rare enough to be
    worth leaving slightly too wide.
    """
    words = text.split()
    if len(words) <= 1:
        return (text,)
    count = min(count, len(words))

    # Greedy by character count, which is what "as evenly as the words allow"
    # means without measuring: aim each line at a share of the whole.
    target = len(text) / count
    lines: list[str] = []
    current: list[str] = []
    for index, word in enumerate(words):
        remaining_words = len(words) - index
        remaining_lines = count - len(lines)
        current.append(word)
        line = " ".join(current)
        must_break = remaining_words == remaining_lines and remaining_lines > 1
        if (len(line) >= target and len(lines) < count - 1) or must_break:
            lines.append(line)
            current = []
    if current:
        lines.append(" ".join(current))
    return tuple(lines)
