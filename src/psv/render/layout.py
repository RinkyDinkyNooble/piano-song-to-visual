"""Where everything in a frame sits, before anything is drawn.

The keyboard along the bottom, the pedal lanes to its right, and how fast the
music falls toward them. Kept apart from `frame`, which draws, so that the
effects can be told where things are without importing the thing that calls
them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from psv.config import VisualConfig
from psv.model import Pedal

#: RGB, uint8. A frame is (height, width, 3).
Frame = np.ndarray

#: How much of the frame the keyboard takes up along the bottom.
KEYBOARD_HEIGHT_FRACTION = 0.16

#: Width of one pedal lane, as a fraction of the whole frame.
PEDAL_LANE_FRACTION = 0.028

#: Gap between the keyboard and the pedal lanes, as a fraction of the frame.
PEDAL_GUTTER_FRACTION = 0.008

#: Pedals in the order they sit under your feet, left to right. Fewer lanes
#: means dropping from the left, so a single lane is the sustain pedal: the one
#: that is both reliably present in MIDI and the one most players actually use.
PEDAL_ORDER: tuple[Pedal, ...] = (Pedal.SOFT, Pedal.SOSTENUTO, Pedal.SUSTAIN)


def lanes_for(count: int) -> tuple[Pedal, ...]:
    """Which pedals get a lane, given how many lanes are configured."""
    if count <= 0:
        return ()
    return PEDAL_ORDER[-count:]


@dataclass(frozen=True, slots=True)
class Layout:
    """Where everything sits, and how fast notes fall."""

    width: int
    height: int
    keyboard_top: int
    keyboard_width: int
    lookahead_s: float
    pedals: tuple[Pedal, ...] = ()

    @property
    def fall_height(self) -> int:
        return self.keyboard_top

    @property
    def pixels_per_second(self) -> float:
        return self.fall_height / self.lookahead_s

    @property
    def pedal_area_left(self) -> int:
        return self.width - self.pedal_area_width

    @property
    def pedal_area_width(self) -> int:
        return self.width - self.keyboard_width

    @property
    def gutter(self) -> int:
        """Blank space separating the keyboard from the pedal lanes."""
        return round(self.width * PEDAL_GUTTER_FRACTION) if self.pedals else 0

    @property
    def lane_width(self) -> float:
        if not self.pedals:
            return 0.0
        return (self.pedal_area_width - self.gutter) / len(self.pedals)

    def lane_span(self, pedal: Pedal) -> tuple[float, float]:
        """Left and right edge of one pedal's lane."""
        index = self.pedals.index(pedal)
        start = self.pedal_area_left + self.gutter + index * self.lane_width
        return start, start + self.lane_width

    def time_to_y(self, time: float, now: float) -> float:
        """Where a moment in the music sits on screen at wall-clock ``now``."""
        return self.keyboard_top - (time - now) * self.pixels_per_second

    @classmethod
    def from_config(cls, config: VisualConfig, pedal_lanes: int = 0) -> Layout:
        keyboard_height = max(1, round(config.height * KEYBOARD_HEIGHT_FRACTION))
        pedals = lanes_for(pedal_lanes)
        area = (
            round(
                config.width
                * (PEDAL_LANE_FRACTION * len(pedals) + PEDAL_GUTTER_FRACTION)
            )
            if pedals
            else 0
        )
        return cls(
            width=config.width,
            height=config.height,
            keyboard_top=config.height - keyboard_height,
            keyboard_width=config.width - area,
            lookahead_s=config.lookahead_s,
            pedals=pedals,
        )
