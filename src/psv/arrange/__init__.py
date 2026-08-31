"""Reducing a multi-instrument score to two hands.

The fuzziest stage, and the only one with no right answer. See `reduce` for what
it does and what it deliberately does not claim.
"""

from psv.arrange.reduce import (
    DEFAULT_MAX_VOICES,
    ArrangeResult,
    arrange,
    assign_hands,
    looks_arranged,
    reduce_texture,
)

__all__ = [
    "DEFAULT_MAX_VOICES",
    "ArrangeResult",
    "arrange",
    "assign_hands",
    "looks_arranged",
    "reduce_texture",
]
