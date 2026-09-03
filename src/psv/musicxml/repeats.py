"""Repeat structure, turned into the order the measures are played in.

A score is written once and played more than once. `|: :|` says go back,
first- and second-time bars say which way to go on which pass, and D.C., D.S.,
segno, coda and fine are jumps written in words. None of that survives into a
video: a falling note happens at a time, so the measures have to be laid out in
the order a player would meet them, each repeat spelled out in full.

The work is split in two on purpose. `measure_marks` reads the XML and produces
one small record per measure; `play_order` turns those records into a list of
measure indices and knows nothing about XML at all. The second half is where
every mistake lives, and it can be tested by writing the marks down directly.

**Repeats do not nest.** A backward repeat returns to the most recent forward
repeat, or to the start of the piece when there is none, which is what the
notation means in every piece this tool is aimed at. A forward repeat written
inside an already-repeating section would be followed as if it replaced the
outer one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from xml.etree import ElementTree

log = logging.getLogger(__name__)

#: A ceiling on the unrolled length, so a repeat structure that contradicts
#: itself stops rather than running forever. Far above any real piece: the
#: longest thing anyone would practise is a few hundred measures.
MAX_PLAYED_MEASURES = 100_000


@dataclass(frozen=True, slots=True)
class MeasureMarks:
    """Everything about one measure that changes where the player goes next."""

    #: `|:` at the left barline.
    forward_repeat: bool = False
    #: `:|` at the right barline.
    backward_repeat: bool = False
    #: How many times the section is played in total, from `times`. Two by
    #: default, which is one jump back.
    times: int = 2
    #: The pass numbers this measure's ending block applies to, from a
    #: `number="1,2"` attribute. Empty when the measure starts no ending.
    endings: frozenset[int] = frozenset()
    #: Whether an ending block finishes here.
    ending_stop: bool = False
    segno: bool = False
    coda: bool = False
    fine: bool = False
    dacapo: bool = False
    dalsegno: bool = False
    tocoda: bool = False


def play_order(marks: Sequence[MeasureMarks]) -> tuple[int, ...]:
    """Measure indices in the order they are played, repeats spelled out.

    A sixteen-bar piece with one repeat comes back as thirty-two indices.
    """
    total = len(marks)
    if total == 0:
        return ()

    for measure in nested_repeats(marks):
        log.warning(
            "measure %d opens a repeat inside a first- or second-time bar; "
            "nested repeats are flattened, so the played order may be short",
            measure + 1,
        )

    order: list[int] = []
    index = 0
    #: The measure a backward repeat returns to.
    section_start = 0
    #: Which time through that section this is, which is what an ending block
    #: is matched against.
    pass_number = 1
    #: Backward-repeat measure -> jumps already made from it. Every jump is
    #: recorded and never forgotten, which is what makes this terminate.
    jumped_back: dict[int, int] = {}
    #: True once a D.C. or D.S. has been followed. On that pass the repeats
    #: are not taken again, which is the convention unless the score says
    #: "con repetizione", and nothing here says it.
    on_the_jump = False
    coda_taken = False

    while 0 <= index < total:
        if len(order) >= MAX_PLAYED_MEASURES:
            log.warning(
                "repeat structure exceeded %d measures; stopping there",
                MAX_PLAYED_MEASURES,
            )
            break

        mark = marks[index]

        # The ending is judged first. A measure that both opens an ending and
        # carries `|:` belongs to the section it is ending, not to the new one.
        if mark.endings and not _ending_applies(marks, index, pass_number, on_the_jump):
            # Always strictly forward, so skipping an ending cannot loop.
            index = _after_ending(marks, index)
            continue

        if mark.forward_repeat and not on_the_jump and index != section_start:
            section_start = index
            pass_number = 1

        order.append(index)

        if mark.tocoda and on_the_jump and not coda_taken:
            target = _find(marks, "coda")
            if target is not None:
                coda_taken = True
                index = target
                continue

        if mark.fine and on_the_jump:
            break

        if mark.backward_repeat and not on_the_jump:
            done = jumped_back.get(index, 0)
            if done < mark.times - 1:
                jumped_back[index] = done + 1
                pass_number += 1
                index = section_start
                continue

        if not on_the_jump and (mark.dacapo or mark.dalsegno):
            target = 0 if mark.dacapo else _find(marks, "segno")
            if target is not None:
                on_the_jump = True
                index = target
                continue

        index += 1

    return tuple(order)


def nested_repeats(marks: Sequence[MeasureMarks]) -> tuple[int, ...]:
    """Measures opening a repeat inside an ending block.

    That is the shape `play_order` cannot follow, since it keeps one section
    at a time rather than a stack, and it is worth saying out loud: the piece
    comes out shorter than it is written, which is otherwise invisible.
    """
    found: list[int] = []
    inside = False
    for index, mark in enumerate(marks):
        if mark.endings:
            inside = True
        if inside and mark.forward_repeat:
            found.append(index)
        if mark.ending_stop:
            inside = False
    return tuple(found)


def _ending_applies(
    marks: Sequence[MeasureMarks], index: int, pass_number: int, on_the_jump: bool
) -> bool:
    """Whether the ending block starting here is played on this pass."""
    if not on_the_jump:
        return pass_number in marks[index].endings
    # A D.C. or D.S. pass is not a repeat, so "first time only" does not apply
    # to it. Play the last ending of the group, which is the one that carries
    # on into the rest of the piece.
    after = _after_ending(marks, index)
    return not (0 <= after < len(marks) and marks[after].endings)


def _after_ending(marks: Sequence[MeasureMarks], index: int) -> int:
    """The measure following the ending block that begins at ``index``.

    Ends at the measure carrying the block's stop mark, and failing that at the
    next ending block, because a file may mark only the starts.
    """
    for candidate in range(index, len(marks)):
        if candidate > index and marks[candidate].endings:
            return candidate
        if marks[candidate].ending_stop:
            return candidate + 1
    return len(marks)


def _find(marks: Sequence[MeasureMarks], attribute: str) -> int | None:
    for index, mark in enumerate(marks):
        if getattr(mark, attribute):
            return index
    return None


# -- reading the marks out of the XML ------------------------------------


@dataclass(slots=True)
class _Builder:
    """The mutable form of `MeasureMarks`, filled in from several parts."""

    forward_repeat: bool = False
    backward_repeat: bool = False
    times: int = 2
    endings: set[int] = field(default_factory=set)
    ending_stop: bool = False
    segno: bool = False
    coda: bool = False
    fine: bool = False
    dacapo: bool = False
    dalsegno: bool = False
    tocoda: bool = False
    #: The engraved signs, kept apart from the `sound` attributes above. Some
    #: exports draw a coda sign beside the words "To Coda" as well as at the
    #: coda itself, so a glyph is only trusted where nothing states the mark.
    segno_glyph: bool = False
    coda_glyph: bool = False

    def freeze(self) -> MeasureMarks:
        return MeasureMarks(
            forward_repeat=self.forward_repeat,
            backward_repeat=self.backward_repeat,
            times=self.times,
            endings=frozenset(self.endings),
            ending_stop=self.ending_stop,
            segno=self.segno,
            coda=self.coda,
            fine=self.fine,
            dacapo=self.dacapo,
            dalsegno=self.dalsegno,
            tocoda=self.tocoda,
        )


def measure_marks(root: ElementTree.Element) -> tuple[MeasureMarks, ...]:
    """Read the repeat structure from a parsed `score-partwise` document.

    Every part is read and the marks merged by measure number. Notation
    software writes the barline repeats into every part but usually writes
    D.C. and the segno into the first one only, and a mark missed in one part
    would silently shorten the piece.
    """
    builders: list[_Builder] = []

    for part in root.findall("part"):
        for position, measure in enumerate(part.findall("measure")):
            while len(builders) <= position:
                builders.append(_Builder())
            _read_measure(measure, builders[position])

    for name in ("segno", "coda"):
        if not any(getattr(builder, name) for builder in builders):
            for builder in builders:
                setattr(builder, name, getattr(builder, f"{name}_glyph"))

    return tuple(builder.freeze() for builder in builders)


def _read_measure(measure: ElementTree.Element, into: _Builder) -> None:
    for barline in measure.iter("barline"):
        _read_barline(barline, into)
    for sound in measure.iter("sound"):
        _read_sound(sound, into)
    # MuseScore writes the segno and coda glyphs as direction types as well as
    # in the `sound` element. Either one is enough to place them.
    if measure.find(".//segno") is not None:
        into.segno_glyph = True
    if measure.find(".//coda") is not None:
        into.coda_glyph = True


def _read_barline(barline: ElementTree.Element, into: _Builder) -> None:
    repeat = barline.find("repeat")
    if repeat is not None:
        direction = repeat.get("direction", "")
        if direction == "forward":
            into.forward_repeat = True
        elif direction == "backward":
            into.backward_repeat = True
            into.times = max(2, _times(repeat.get("times")))

    ending = barline.find("ending")
    if ending is not None:
        kind = ending.get("type", "")
        if kind == "start":
            into.endings |= _ending_numbers(ending)
        elif kind in {"stop", "discontinue"}:
            into.ending_stop = True


def _ending_numbers(ending: ElementTree.Element) -> set[int]:
    """The pass numbers from `number="1, 2"`.

    A block with no readable number applies to the first pass, which is what a
    lone first-time bar means and the only reading that cannot lose music.
    """
    numbers = set()
    for piece in (ending.get("number") or "").replace(".", "").split(","):
        piece = piece.strip()
        if piece.isdigit():
            numbers.add(int(piece))
    return numbers or {1}


def _times(text: str | None) -> int:
    try:
        return int(text) if text else 2
    except ValueError:
        return 2


def _read_sound(sound: ElementTree.Element, into: _Builder) -> None:
    if sound.get("dacapo"):
        into.dacapo = True
    if sound.get("dalsegno"):
        into.dalsegno = True
    if sound.get("segno"):
        into.segno = True
    if sound.get("coda"):
        into.coda = True
    if sound.get("tocoda"):
        into.tocoda = True
    if sound.get("fine"):
        into.fine = True
