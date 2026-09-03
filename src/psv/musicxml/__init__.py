"""Reading MusicXML, which says what MIDI can only imply.

A piano score's staves *are* its hands, and the file states which staff every
note is on. Dynamics and pedal are written as notation rather than inferred.
See `read` for what that buys and what this reader does not yet do.
"""

from psv.musicxml.read import (
    DYNAMICS,
    MusicXmlReadError,
    read_musicxml,
    read_musicxml_file,
)

__all__ = [
    "DYNAMICS",
    "MusicXmlReadError",
    "read_musicxml",
    "read_musicxml_file",
]
