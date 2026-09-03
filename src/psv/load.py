"""Reading a score, whichever format it arrived in.

Two front ends now produce a `Score`: MIDI and MusicXML. Everything downstream
takes a `Score` and does not care which one it came from, so this is the one
place that has to tell them apart.

Told apart by content rather than by extension. A `.xml` may be MusicXML or may
be something else entirely, `.mxl` is a zip, and a file exported from notation
software is as likely to arrive named `.xml` as `.musicxml`. Reading the first
few bytes is both more reliable and harder to get wrong than a suffix table.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from psv.midi import read_midi_file
from psv.midi.read import MidiReadError
from psv.model import Score
from psv.musicxml import read_musicxml_file
from psv.musicxml.read import MusicXmlReadError

#: Every MIDI file starts with this chunk type.
_MIDI_MAGIC = b"MThd"

#: How much of the head to read when deciding. Enough to see past a byte-order
#: mark, an XML declaration, and a DOCTYPE line.
_SNIFF = 4096


class ScoreReadError(ValueError):
    """A file could not be read as any format this tool understands."""


def score_format(path: Path | str) -> str:
    """``"midi"``, ``"musicxml"``, or raises.

    Exposed separately from `read_score` so an error message, or a test, can
    say what a file was taken to be without parsing the whole thing.
    """
    source = Path(path).expanduser()
    try:
        # `with`, not a bare open(): an unclosed handle raises ResourceWarning
        # from a destructor on POSIX, which this project treats as an error and
        # which then fails whichever test the collector happens to interrupt.
        with source.open("rb") as handle:
            head = handle.read(_SNIFF)
    except OSError as exc:
        raise ScoreReadError(f"could not read {source}: {exc}") from exc

    if head.startswith(_MIDI_MAGIC):
        return "midi"
    if zipfile.is_zipfile(source):
        # A compressed MusicXML. Anything else zipped is not a score.
        return "musicxml"

    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<"):
        return "musicxml"

    raise ScoreReadError(
        f"{source} is neither MIDI nor MusicXML (starts with {head[:8]!r})"
    )


def read_score(path: Path | str) -> Score:
    """Read a MIDI or MusicXML file into a Score."""
    kind = score_format(path)
    try:
        if kind == "midi":
            return read_midi_file(path)
        return read_musicxml_file(path)
    except (MidiReadError, MusicXmlReadError) as exc:
        raise ScoreReadError(str(exc)) from exc
