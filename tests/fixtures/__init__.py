"""Synthetic test fixtures.

`midi_builder` is the source of truth for every generated MIDI used in tests.
Real songs live under `tests/assets/`; see `tests/assets/songs.toml`.
"""

from tests.fixtures.midi_builder import FIXTURES, MidiBuilder

__all__ = ["FIXTURES", "MidiBuilder"]
