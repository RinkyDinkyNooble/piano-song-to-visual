"""MusicXML to Score.

MusicXML says things MIDI can only be asked to imply, and the difference is the
reason this reader exists rather than a converter to MIDI.

A piano score has two staves, and the file *says* which staff a note is on. That
is the hand, stated rather than guessed from a track name. Dynamics are written
as ``p`` and ``f`` rather than inferred from velocity bytes, and a file with no
dynamics is honestly silent about them instead of setting everything to 64.
Pedal is a direction with a start and a stop.

Written against the standard library. MusicXML is XML and a compressed one is a
zip, so `xml.etree` and `zipfile` cover it. The alternative was `music21`, which
brings fourteen packages including matplotlib to parse a text format.

**Notation time, not performance time.** A duration here is in divisions of a
quarter note, and the tempo map turns those into seconds. Repeats are unrolled
before any of that: `repeats.play_order` works out which measure is played when,
and each part is then walked in that order rather than in document order, so a
repeated section arrives twice, with two sets of times. See `repeats` for how
far that goes.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from psv.model import Hand, Note, Part, Pedal, PedalEvent, Score
from psv.musicxml.repeats import measure_marks, play_order
from psv.tempo import TempoMap, TimeSignature

log = logging.getLogger(__name__)

#: Semitones above C for each step name.
_STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

#: MIDI velocity for each dynamic mark. MusicXML also carries an explicit
#: `dynamics` percentage, which wins where a file provides one.
DYNAMICS = {
    "pppp": 10,
    "ppp": 20,
    "pp": 31,
    "p": 45,
    "mp": 58,
    "mf": 72,
    "f": 88,
    "ff": 101,
    "fff": 114,
    "ffff": 124,
}

#: Velocity for a score that never states a dynamic, matching the MIDI reader's
#: default so the two front ends produce comparable scores.
DEFAULT_VELOCITY = 64

#: Ticks per quarter note in the Score's tempo map. MusicXML counts in whatever
#: divisions a file declares, which can change mid-score, so durations are
#: converted to this fixed grid on the way in.
TICKS_PER_BEAT = 480

#: A grace note has no duration of its own. It is given this fraction of a beat
#: so it can be seen and played, rather than being a zero-length event.
GRACE_BEATS = 0.125


class MusicXmlReadError(ValueError):
    """A MusicXML file could not be read."""


@dataclass(slots=True)
class _Cursor:
    """Where the reader is, in one part.

    MusicXML is written as a stream of things that happen at a point, plus
    instructions to move that point. ``<backup>`` rewinds it, which is how a
    second voice is written after the first; ``<forward>`` skips ahead.
    """

    #: Position within the current measure, in divisions.
    position: int = 0
    #: Where the current measure began, in quarter notes since the start.
    measure_start: float = 0.0
    #: Divisions per quarter note, from the most recent `<attributes>`.
    divisions: int = 1

    def beats(self) -> float:
        return self.measure_start + self.position / self.divisions


@dataclass(slots=True)
class _Pending:
    """A note being built, before its hand and timing are known."""

    pitch: int
    start_beats: float
    end_beats: float
    velocity: int
    staff: int
    voice: str
    tied: bool = False


@dataclass(slots=True)
class _PartRead:
    notes: list[_Pending] = field(default_factory=list)
    pedals: list[tuple[float, float | None]] = field(default_factory=list)
    #: (beats, quarter-notes-per-minute) from `<sound tempo="">`.
    tempi: list[tuple[float, float]] = field(default_factory=list)
    #: (beats, numerator, denominator)
    meters: list[tuple[float, int, int]] = field(default_factory=list)


def read_musicxml_file(path: Path | str) -> Score:
    """Read a `.musicxml`, `.xml`, or compressed `.mxl` file."""
    source = Path(path).expanduser()
    try:
        root = _parse(source)
    except OSError as exc:
        raise MusicXmlReadError(f"could not read {source}: {exc}") from exc
    except ElementTree.ParseError as exc:
        raise MusicXmlReadError(f"{source} is not valid XML: {exc}") from exc
    score = read_musicxml(root)
    return Score(
        parts=score.parts,
        pedals=score.pedals,
        tempo_map=score.tempo_map,
        time_signatures=score.time_signatures,
        source=source,
        title=score.title or source.stem,
    )


def _parse(source: Path) -> ElementTree.Element:
    """Parse the file, unwrapping a compressed container if there is one.

    An `.mxl` is a zip whose META-INF/container.xml names the real score. The
    member is read by name from that manifest rather than by guessing, and the
    archive is treated as untrusted: a member path that escapes the archive, or
    one that decompresses to something absurd, is refused.
    """
    if not zipfile.is_zipfile(source):
        return ElementTree.parse(source).getroot()  # noqa: S314 - see module docs

    with zipfile.ZipFile(source) as archive:
        name = _container_member(archive)
        info = archive.getinfo(name)
        if info.file_size > 64_000_000:
            raise MusicXmlReadError(f"{source}: {name} is implausibly large")
        with archive.open(info) as handle:
            return ElementTree.parse(handle).getroot()  # noqa: S314


def _container_member(archive: zipfile.ZipFile) -> str:
    """The score file named by an .mxl's container manifest."""
    try:
        manifest = ElementTree.fromstring(  # noqa: S314
            archive.read("META-INF/container.xml")
        )
    except (KeyError, ElementTree.ParseError):
        manifest = None

    if manifest is not None:
        for rootfile in manifest.iter("rootfile"):
            name = rootfile.get("full-path", "")
            if name and not name.startswith(("/", "..")) and ".." not in name:
                return name

    for name in archive.namelist():
        if name.lower().endswith((".musicxml", ".xml")) and not name.startswith(
            "META-INF"
        ):
            return name
    raise MusicXmlReadError("compressed score contains no MusicXML file")


def read_musicxml(root: ElementTree.Element) -> Score:
    """Build a Score from a parsed `score-partwise` document."""
    if root.tag == "score-timewise":
        raise MusicXmlReadError(
            "score-timewise is not supported; export as score-partwise"
        )
    if root.tag != "score-partwise":
        raise MusicXmlReadError(f"not a MusicXML score: root element is <{root.tag}>")

    order = play_order(measure_marks(root))
    reads = [_read_part(part, order) for part in root.findall("part")]
    if not reads:
        raise MusicXmlReadError("score contains no parts")

    tempo_map = _tempo_map(reads)
    notes, pedals = _to_seconds(reads, tempo_map)
    return Score(
        parts=_group_by_hand(notes),
        pedals=tuple(sorted(pedals)),
        tempo_map=tempo_map,
        time_signatures=_time_signatures(reads, tempo_map),
        title=_title(root),
    )


def _title(root: ElementTree.Element) -> str:
    for path in ("work/work-title", "movement-title"):
        found = root.find(path)
        if found is not None and found.text:
            return found.text.strip()
    return ""


# -- one part ------------------------------------------------------------


def _read_part(part: ElementTree.Element, order: Sequence[int]) -> _PartRead:
    """Walk one part's measures in playing order, which repeats have unrolled.

    MusicXML requires every part to carry every measure, and the repeat marks
    are read from all the parts at once, so a part that stops early names
    measures it does not have. Those positions contribute nothing rather than
    raising or reading some other bar. Such a file is malformed and its short
    part will not stay in step; that is as far as the guess goes.
    """
    out = _PartRead()
    cursor = _Cursor()
    open_ties: dict[tuple[int, str], _Pending] = {}
    velocity = DEFAULT_VELOCITY
    pedal_down: float | None = None
    measures = part.findall("measure")
    previous = -1

    for position in order:
        if not 0 <= position < len(measures):
            continue
        if position != previous + 1:
            # A jump. A tie left open across it cannot be the same sounding
            # note, and left in place it would swallow whatever comes next on
            # that pitch.
            open_ties.clear()
        previous = position
        measure = measures[position]
        cursor.position = 0
        longest = 0

        for element in measure:
            if element.tag == "attributes":
                _read_attributes(element, cursor, out)
            elif element.tag == "direction":
                velocity, pedal_down = _read_direction(
                    element, cursor, out, velocity, pedal_down
                )
            elif element.tag == "note":
                velocity = _read_note(element, cursor, out, open_ties, velocity)
            elif element.tag == "backup":
                cursor.position -= _duration(element)
                cursor.position = max(0, cursor.position)
            elif element.tag == "forward":
                cursor.position += _duration(element)
            longest = max(longest, cursor.position)

        cursor.measure_start += longest / cursor.divisions

    if pedal_down is not None:
        out.pedals.append((pedal_down, None))
    return out


def _read_attributes(
    element: ElementTree.Element, cursor: _Cursor, out: _PartRead
) -> None:
    divisions = element.findtext("divisions")
    if divisions:
        try:
            value = int(divisions)
        except ValueError:
            value = 0
        if value > 0:
            cursor.divisions = value
    time = element.find("time")
    if time is not None:
        beats, beat_type = time.findtext("beats"), time.findtext("beat-type")
        if beats and beat_type:
            try:
                out.meters.append((cursor.beats(), int(beats), int(beat_type)))
            except ValueError:
                log.debug("unreadable time signature, ignoring")


def _read_direction(
    element: ElementTree.Element,
    cursor: _Cursor,
    out: _PartRead,
    velocity: int,
    pedal_down: float | None,
) -> tuple[int, float | None]:
    """Dynamics, pedal, and tempo, all of which arrive as `<direction>`."""
    sound = element.find("sound")
    if sound is not None:
        tempo = sound.get("tempo")
        if tempo:
            try:
                out.tempi.append((cursor.beats(), float(tempo)))
            except ValueError:
                log.debug("unreadable tempo, ignoring")
        loudness = sound.get("dynamics")
        if loudness:
            try:
                velocity = _clamp_velocity(float(loudness) * 90 / 100)
            except ValueError:
                log.debug("unreadable dynamics, ignoring")

    for dynamics in element.iter("dynamics"):
        for mark in dynamics:
            if mark.tag in DYNAMICS:
                velocity = DYNAMICS[mark.tag]

    for pedal in element.iter("pedal"):
        kind = pedal.get("type", "")
        if kind == "start" and pedal_down is None:
            pedal_down = cursor.beats()
        elif kind in {"stop", "discontinue"} and pedal_down is not None:
            out.pedals.append((pedal_down, cursor.beats()))
            pedal_down = None
        elif kind == "change" and pedal_down is not None:
            out.pedals.append((pedal_down, cursor.beats()))
            pedal_down = cursor.beats()

    return velocity, pedal_down


def _read_note(
    element: ElementTree.Element,
    cursor: _Cursor,
    out: _PartRead,
    open_ties: dict[tuple[int, str], _Pending],
    velocity: int,
) -> int:
    """One `<note>`, which may be a rest, a chord member, or a grace note."""
    staff = _int_text(element.findtext("staff"), 1)
    voice = element.findtext("voice") or "1"
    is_chord = element.find("chord") is not None
    is_grace = element.find("grace") is not None

    # A chord member sounds with the note before it, so the cursor has not
    # moved. Rewind by the previous note's length to line them up.
    duration = _duration(element)
    if is_chord:
        cursor.position -= _last_duration(out, cursor)

    start = cursor.beats()
    if element.find("rest") is not None:
        cursor.position += duration
        return velocity

    pitch = _pitch(element)
    if pitch is None:
        cursor.position += duration
        return velocity

    length = GRACE_BEATS if is_grace else duration / cursor.divisions
    tie_start, tie_stop = _ties(element)
    key = (pitch, voice)

    if tie_stop and key in open_ties:
        # Extend the note already sounding rather than starting another.
        held = open_ties.pop(key)
        held.end_beats = start + length
        if tie_start:
            held.tied = True
            open_ties[key] = held
        if not is_grace:
            cursor.position += duration
        return velocity

    note = _Pending(
        pitch=pitch,
        start_beats=start,
        end_beats=start + length,
        velocity=velocity,
        staff=staff,
        voice=voice,
        tied=tie_start,
    )
    out.notes.append(note)
    if tie_start:
        open_ties[key] = note
    if not is_grace:
        cursor.position += duration
    return velocity


def _last_duration(out: _PartRead, cursor: _Cursor) -> int:
    """Divisions taken by the note a chord member should align with."""
    if not out.notes:
        return 0
    last = out.notes[-1]
    return round((last.end_beats - last.start_beats) * cursor.divisions)


def _pitch(element: ElementTree.Element) -> int | None:
    """MIDI note number, or None when there is no pitch to read."""
    pitch = element.find("pitch")
    if pitch is None:
        unpitched = element.find("unpitched")
        if unpitched is None:
            return None
        pitch = unpitched
    step = (pitch.findtext("step") or pitch.findtext("display-step") or "").upper()
    if step not in _STEPS:
        return None
    octave = _int_text(pitch.findtext("octave") or pitch.findtext("display-octave"), 4)
    alter = round(float(pitch.findtext("alter") or 0))
    value = (octave + 1) * 12 + _STEPS[step] + alter
    return value if 0 <= value <= 127 else None


def _ties(element: ElementTree.Element) -> tuple[bool, bool]:
    """Whether this note starts and/or stops a tie.

    Read from `<tie>`, which is the sounding one. `<tied>` inside `<notations>`
    is the engraved slur-like mark and is deliberately ignored: the two usually
    agree, and where they do not it is the sound that matters here.
    """
    start = stop = False
    for tie in element.findall("tie"):
        kind = tie.get("type", "")
        start = start or kind == "start"
        stop = stop or kind == "stop"
    return start, stop


def _duration(element: ElementTree.Element) -> int:
    return max(0, _int_text(element.findtext("duration"), 0))


def _int_text(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default


def _clamp_velocity(value: float) -> int:
    return max(1, min(127, round(value)))


# -- assembling the score ------------------------------------------------


def _tempo_map(reads: list[_PartRead]) -> TempoMap:
    """One tempo map for the whole score, from whichever parts state a tempo."""
    marks: dict[float, float] = {}
    for read in reads:
        for beats, bpm in read.tempi:
            if bpm > 0:
                marks.setdefault(beats, bpm)
    changes = [
        (round(beats * TICKS_PER_BEAT), round(60_000_000 / bpm))
        for beats, bpm in sorted(marks.items())
    ]
    return TempoMap.from_changes(TICKS_PER_BEAT, changes)


def _time_signatures(
    reads: list[_PartRead], tempo_map: TempoMap
) -> tuple[TimeSignature, ...]:
    seen: dict[float, tuple[int, int]] = {}
    for read in reads:
        for beats, numerator, denominator in read.meters:
            if numerator > 0 and denominator > 0:
                seen.setdefault(beats, (numerator, denominator))
    if not seen:
        return (TimeSignature(0, 0.0, 4, 4),)
    out = []
    for beats, (numerator, denominator) in sorted(seen.items()):
        tick = round(beats * TICKS_PER_BEAT)
        out.append(
            TimeSignature(tick, tempo_map.tick_to_seconds(tick), numerator, denominator)
        )
    return tuple(out)


def _to_seconds(
    reads: list[_PartRead], tempo_map: TempoMap
) -> tuple[list[Note], list[PedalEvent]]:
    """Turn quarter-note positions into seconds, once the tempo map is known."""

    def at(beats: float) -> float:
        return tempo_map.tick_to_seconds(round(beats * TICKS_PER_BEAT))

    notes: list[Note] = []
    pedals: list[PedalEvent] = []
    single_part = len(reads) == 1

    for index, read in enumerate(reads):
        for pending in read.notes:
            start = at(pending.start_beats)
            end = max(start, at(pending.end_beats))
            notes.append(
                Note(
                    pitch=pending.pitch,
                    start=start,
                    end=end,
                    velocity=pending.velocity,
                    hand=_hand(pending, single_part),
                    source_track=index,
                )
            )
        for down, up in read.pedals:
            start = at(down)
            end = at(up) if up is not None else start
            if end > start:
                pedals.append(PedalEvent(Pedal.SUSTAIN, start, end))

    return notes, pedals


def _hand(pending: _Pending, single_part: bool) -> Hand:
    """Which hand plays this note.

    The whole reason to read MusicXML rather than MIDI. A piano part is written
    on two staves and the file says which staff every note is on, so the hand is
    read rather than inferred. Staff 1 is the upper one.

    Left unassigned for anything that is not a single two-staff part, because
    then the staves are instruments rather than hands and the arrange stage
    should make the call.
    """
    if not single_part:
        return Hand.UNASSIGNED
    if pending.staff == 1:
        return Hand.RIGHT
    if pending.staff == 2:
        return Hand.LEFT
    return Hand.UNASSIGNED


def _group_by_hand(notes: list[Note]) -> tuple[Part, ...]:
    by_hand: dict[Hand, list[Note]] = {}
    for note in notes:
        by_hand.setdefault(note.hand, []).append(note)
    return tuple(
        Part(notes=tuple(sorted(group)), name=hand.value, hand=hand)
        for hand, group in sorted(by_hand.items(), key=lambda item: item[0].value)
    )
