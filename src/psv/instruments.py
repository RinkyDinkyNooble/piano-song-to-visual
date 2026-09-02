"""What `audio.program` can be set to.

The setting is a bare General MIDI program number, which is unhelpful on its
own: finding anything past the few you already know means guessing a number and
re-rendering. This module turns the number back into a name.

Two sources, and the SoundFont wins where it exists. GM is a convention, not a
guarantee, and a SoundFont is free to put anything at any program number. The
names inside the file are what will actually sound.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: The General MIDI Level 1 sound set, in program order. Index is the program
#: number, so `GM_PROGRAMS[0]` is program 0.
GM_PROGRAMS: tuple[str, ...] = (
    "Acoustic Grand Piano",
    "Bright Acoustic Piano",
    "Electric Grand Piano",
    "Honky-tonk Piano",
    "Electric Piano 1 (Rhodes)",
    "Electric Piano 2 (FM)",
    "Harpsichord",
    "Clavinet",
    "Celesta",
    "Glockenspiel",
    "Music Box",
    "Vibraphone",
    "Marimba",
    "Xylophone",
    "Tubular Bells",
    "Dulcimer",
    "Drawbar Organ",
    "Percussive Organ",
    "Rock Organ",
    "Church Organ",
    "Reed Organ",
    "Accordion",
    "Harmonica",
    "Tango Accordion",
    "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)",
    "Electric Guitar (clean)",
    "Electric Guitar (muted)",
    "Overdriven Guitar",
    "Distortion Guitar",
    "Guitar Harmonics",
    "Acoustic Bass",
    "Electric Bass (finger)",
    "Electric Bass (pick)",
    "Fretless Bass",
    "Slap Bass 1",
    "Slap Bass 2",
    "Synth Bass 1",
    "Synth Bass 2",
    "Violin",
    "Viola",
    "Cello",
    "Contrabass",
    "Tremolo Strings",
    "Pizzicato Strings",
    "Orchestral Harp",
    "Timpani",
    "String Ensemble 1",
    "String Ensemble 2",
    "Synth Strings 1",
    "Synth Strings 2",
    "Choir Aahs",
    "Voice Oohs",
    "Synth Voice",
    "Orchestra Hit",
    "Trumpet",
    "Trombone",
    "Tuba",
    "Muted Trumpet",
    "French Horn",
    "Brass Section",
    "Synth Brass 1",
    "Synth Brass 2",
    "Soprano Sax",
    "Alto Sax",
    "Tenor Sax",
    "Baritone Sax",
    "Oboe",
    "English Horn",
    "Bassoon",
    "Clarinet",
    "Piccolo",
    "Flute",
    "Recorder",
    "Pan Flute",
    "Blown Bottle",
    "Shakuhachi",
    "Whistle",
    "Ocarina",
    "Lead 1 (square)",
    "Lead 2 (sawtooth)",
    "Lead 3 (calliope)",
    "Lead 4 (chiff)",
    "Lead 5 (charang)",
    "Lead 6 (voice)",
    "Lead 7 (fifths)",
    "Lead 8 (bass + lead)",
    "Pad 1 (new age)",
    "Pad 2 (warm)",
    "Pad 3 (polysynth)",
    "Pad 4 (choir)",
    "Pad 5 (bowed)",
    "Pad 6 (metallic)",
    "Pad 7 (halo)",
    "Pad 8 (sweep)",
    "FX 1 (rain)",
    "FX 2 (soundtrack)",
    "FX 3 (crystal)",
    "FX 4 (atmosphere)",
    "FX 5 (brightness)",
    "FX 6 (goblins)",
    "FX 7 (echoes)",
    "FX 8 (sci-fi)",
    "Sitar",
    "Banjo",
    "Shamisen",
    "Koto",
    "Kalimba",
    "Bagpipe",
    "Fiddle",
    "Shanai",
    "Tinkle Bell",
    "Agogo",
    "Steel Drums",
    "Woodblock",
    "Taiko Drum",
    "Melodic Tom",
    "Synth Drum",
    "Reverse Cymbal",
    "Guitar Fret Noise",
    "Breath Noise",
    "Seashore",
    "Bird Tweet",
    "Telephone Ring",
    "Helicopter",
    "Applause",
    "Gunshot",
)

#: Programs worth trying on a piano piece, and why. Everything else in GM is
#: reachable; these are the ones that do not sound absurd under two hands.
SUGGESTED: dict[int, str] = {
    0: "the default, and what most SoundFonts sample best",
    1: "brighter, cuts through a busy texture",
    2: "electric grand",
    4: "Rhodes, the classic electric piano",
    5: "FM electric piano",
    6: "harpsichord: no dynamics, so velocity stops meaning anything",
    11: "vibraphone",
    19: "church organ, for the organ repertoire this tool arranges from",
}

#: A SoundFont preset record is this many bytes: 20 name, 2 preset, 2 bank,
#: then indices this module does not need.
_PHDR_RECORD = 38

#: Refuse to walk a file claiming more presets than any real SoundFont has.
_MAX_PRESETS = 8192


class SoundFontError(ValueError):
    """A SoundFont could not be read far enough to list its presets."""


@dataclass(frozen=True, slots=True)
class Preset:
    """One sound in a SoundFont, at its bank and program number."""

    bank: int
    program: int
    name: str

    def __lt__(self, other: Preset) -> bool:
        return (self.bank, self.program) < (other.bank, other.program)


def gm_name(program: int) -> str:
    if not 0 <= program < len(GM_PROGRAMS):
        raise ValueError(f"not a General MIDI program: {program}")
    return GM_PROGRAMS[program]


def soundfont_presets(path: Path | str) -> list[Preset]:
    """The presets a `.sf2` actually contains, in bank and program order.

    A SoundFont is untrusted input, so this reads the chunk headers rather than
    trusting them: every offset is checked against the real file length, the
    preset count is capped, and anything unexpected raises `SoundFontError`
    instead of seeking to wherever the file asked.

    Only the `phdr` chunk is read. Everything else in a SoundFont is sample
    data, which is FluidSynth's business rather than this module's.
    """
    data = Path(path).expanduser().read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"sfbk":
        raise SoundFontError(f"not a SoundFont: {path}")

    phdr = _find_phdr(data)
    if phdr is None:
        raise SoundFontError(f"no preset list in {path}")

    start, size = phdr
    count = size // _PHDR_RECORD
    if count < 1 or count > _MAX_PRESETS:
        raise SoundFontError(f"implausible preset count in {path}: {count}")

    presets: list[Preset] = []
    for index in range(count):
        offset = start + index * _PHDR_RECORD
        raw_name, program, bank = struct.unpack_from("<20sHH", data, offset)
        name = raw_name.split(b"\0", 1)[0].decode("latin-1").strip()
        # The last record is a terminator named EOP and is not a real preset.
        if name == "EOP":
            break
        presets.append(Preset(bank=bank, program=program, name=name))
    return sorted(presets)


def _find_phdr(data: bytes) -> tuple[int, int] | None:
    """Walk the RIFF chunks to the preset header, checking every length.

    Structure is RIFF/sfbk containing LIST chunks, one of which is `pdta`, which
    contains `phdr`. Walking rather than seeking to a remembered offset is what
    keeps a malformed file from steering the read.
    """
    end = len(data)
    position = 12  # past "RIFF", the size, and "sfbk"

    while position + 8 <= end:
        tag = data[position : position + 4]
        (size,) = struct.unpack_from("<I", data, position + 4)
        body = position + 8
        if size < 0 or body + size > end:
            raise SoundFontError("chunk runs past the end of the file")

        if tag == b"LIST" and data[body : body + 4] == b"pdta":
            inner = body + 4
            inner_end = body + size
            while inner + 8 <= inner_end:
                sub = data[inner : inner + 4]
                (sub_size,) = struct.unpack_from("<I", data, inner + 4)
                if inner + 8 + sub_size > inner_end:
                    raise SoundFontError("preset chunk runs past its parent")
                if sub == b"phdr":
                    return inner + 8, sub_size
                inner += 8 + sub_size + (sub_size & 1)  # chunks are word-aligned
            return None

        position = body + size + (size & 1)
    return None
