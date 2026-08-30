"""MIDI input and output.

``read`` is the only place in the codebase that knows what a tick is; ``write``
is the only place that turns a Score back into one.
"""

from psv.midi.read import read_midi, read_midi_file
from psv.midi.write import score_to_midi, write_midi_file

__all__ = ["read_midi", "read_midi_file", "score_to_midi", "write_midi_file"]
