"""Hand-span and difficulty enforcement.

The custom part of this project. `span` finds the moments a hand is asked to
stretch too far, `repair` fixes them, and `verify_span` proves the result.

See docs/CONSTRAINT-ENGINE.md for how and why.
"""

from psv.constraints.difficulty import PROFILES, apply_difficulty
from psv.constraints.hands import assign_by_register, ensure_hands, has_hands
from psv.constraints.repair import (
    ConstrainResult,
    ConstraintError,
    Repair,
    constrain,
)
from psv.constraints.salience import Salience
from psv.constraints.span import (
    Violation,
    detect_violations,
    verify_span,
    widest_span_per_hand,
)

__all__ = [
    "PROFILES",
    "ConstrainResult",
    "ConstraintError",
    "Repair",
    "Salience",
    "Violation",
    "apply_difficulty",
    "assign_by_register",
    "constrain",
    "detect_violations",
    "ensure_hands",
    "has_hands",
    "verify_span",
    "widest_span_per_hand",
]
