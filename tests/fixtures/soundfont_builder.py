"""Build the smallest SoundFont that `psv instruments` can read.

A real `.sf2` is mostly sample data and runs to tens of megabytes, which is not
something to commit for a test about *names*. Only the preset header is read, so
only the preset header is built here.

The same reason the MIDI fixtures are generated rather than downloaded: the file
is the input to the test, so writing it in code makes what is being tested
visible instead of hiding it in a blob.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

#: Byte layout of one preset record, per the SoundFont 2 specification:
#: a 20-byte name, the program, the bank, and four indices nothing here uses.
_PHDR = "<20sHHHII I".replace(" ", "")


def _chunk(tag: bytes, body: bytes) -> bytes:
    """One RIFF chunk, padded to an even length as the format requires."""
    padded = body + (b"\0" if len(body) % 2 else b"")
    return tag + struct.pack("<I", len(body)) + padded


def _preset_record(bank: int, program: int, name: str) -> bytes:
    return struct.pack(
        _PHDR,
        name.encode("latin-1")[:20].ljust(20, b"\0"),
        program,
        bank,
        0,  # preset bag index
        0,  # library
        0,  # genre
        0,  # morphology
    )


def minimal_soundfont(presets: Sequence[tuple[int, int, str]]) -> bytes:
    """A `.sf2` carrying exactly these ``(bank, program, name)`` presets.

    Terminated by the specification's EOP record, which is a sentinel rather
    than a sound and must not be listed as one.
    """
    phdr = b"".join(
        _preset_record(bank, program, name) for bank, program, name in presets
    )
    phdr += _preset_record(0, 0, "EOP")

    pdta = b"pdta" + _chunk(b"phdr", phdr)
    body = b"sfbk" + _chunk(b"LIST", pdta)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def named_presets() -> bytes:
    """Names that are deliberately not the General MIDI ones for those numbers.

    That is the whole point of reading the file: a SoundFont may put anything at
    any program number, so its own names are what will actually sound.
    """
    return minimal_soundfont(
        [(0, 0, "Test Piano"), (0, 4, "Test Rhodes"), (8, 0, "Test Variation")]
    )


def not_a_soundfont() -> bytes:
    """Bytes with no RIFF header at all, for the fall-back path."""
    return b"not a soundfont at all"


#: Named the way the MIDI fixtures are, so features.toml can point at one.
SOUNDFONTS = {
    "named-presets": named_presets,
    "not-a-soundfont": not_a_soundfont,
}
