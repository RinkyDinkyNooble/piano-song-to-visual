"""Reading MusicXML, which says what MIDI can only imply.

A piano score's staves *are* its hands, and the file states which staff every
note is on. Dynamics and pedal are written as notation rather than inferred.
Repeats are unrolled into a linear timeline before anything else, which is
`repeats`. See `read` for what the format buys and where the reader stops.
"""

from psv.musicxml.read import (
    DYNAMICS,
    MusicXmlReadError,
    read_musicxml,
    read_musicxml_file,
)
from psv.musicxml.repeats import MeasureMarks, measure_marks, play_order

__all__ = [
    "DYNAMICS",
    "MeasureMarks",
    "MusicXmlReadError",
    "measure_marks",
    "play_order",
    "read_musicxml",
    "read_musicxml_file",
]
