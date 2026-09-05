"""What a colour is, and how to read one out of a config file.

Separate from `psv.render.color`, which is about turning a *note* into a colour
and needs the config to do it. These are the primitives underneath: no config,
no notes, nothing to import. That is what lets `psv.config` validate a colour
and the renderer parse the same string without one importing the other.

They were written twice before this module existed, once in each place, which
is two chances for "what counts as a colour" to drift apart.
"""

from __future__ import annotations

#: Red, green, blue, each 0-255.
RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)

_HEX_DIGITS = "0123456789abcdefABCDEF"


def is_hex(value: str) -> bool:
    """Whether ``value`` is a colour `parse_hex` can read."""
    if not isinstance(value, str) or not value.startswith("#"):
        return False
    digits = value[1:]
    return len(digits) in {3, 6} and all(c in _HEX_DIGITS for c in digits)


def parse_hex(colour: str) -> RGB:
    """``#4a90d9`` or ``#abc`` to an RGB triple."""
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def is_grayscale(colour: RGB) -> bool:
    return colour[0] == colour[1] == colour[2]
