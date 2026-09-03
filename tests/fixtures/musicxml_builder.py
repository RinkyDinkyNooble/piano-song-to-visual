"""Synthetic MusicXML, written in code so the test states its own input.

The fetched suite in `tests/assets/scores/` is MIT and therefore gitignored, so
continuous integration never sees it. These are ours, carry no licence at all,
and are what the parser is actually held to on every push. They also let a test
say exactly what it is testing, which a downloaded file cannot.

Same pattern as `midi_builder`, and the same reason: the file is the input, so
writing it in code makes what is being tested visible rather than hiding it in
a blob.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Divisions per quarter note used throughout. Divisible by 3, so triplets and
#: dotted notes both land on whole numbers.
DIVISIONS = 24

_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>{title}</work-title></work>
  <part-list>{part_list}</part-list>
{parts}</score-partwise>
"""


def _pitch(name: str) -> str:
    """`C4`, `F#3`, `Bb5` to a `<pitch>` element."""
    step = name[0].upper()
    rest = name[1:]
    alter = 0
    while rest and rest[0] in "#b":
        alter += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    octave = int(rest)
    alter_tag = f"<alter>{alter}</alter>" if alter else ""
    return f"<pitch><step>{step}</step>{alter_tag}<octave>{octave}</octave></pitch>"


def note(
    name: str,
    beats: float = 1.0,
    *,
    staff: int = 1,
    voice: int = 1,
    chord: bool = False,
    tie: str = "",
    grace: bool = False,
    divisions: int = DIVISIONS,
) -> str:
    """One `<note>`. ``tie`` is "start", "stop", or "start stop".

    ``divisions`` must match whatever the enclosing measure declared, since a
    duration is counted in those. Only the division-change fixture needs it.
    """
    duration = round(beats * divisions)
    parts = ["<note>"]
    if grace:
        parts.append("<grace/>")
    if chord:
        parts.append("<chord/>")
    parts.append(_pitch(name))
    if not grace:
        parts.append(f"<duration>{duration}</duration>")
    for kind in tie.split():
        parts.append(f'<tie type="{kind}"/>')
    parts.append(f"<voice>{voice}</voice><staff>{staff}</staff>")
    for kind in tie.split():
        parts.append(f'<notations><tied type="{kind}"/></notations>')
    parts.append("</note>")
    return "".join(parts)


def rest(beats: float = 1.0, *, staff: int = 1, voice: int = 1) -> str:
    duration = round(beats * DIVISIONS)
    return (
        f"<note><rest/><duration>{duration}</duration>"
        f"<voice>{voice}</voice><staff>{staff}</staff></note>"
    )


def backup(beats: float) -> str:
    """Rewind the cursor, which is how a second voice is written."""
    return f"<backup><duration>{round(beats * DIVISIONS)}</duration></backup>"


def forward(beats: float) -> str:
    return f"<forward><duration>{round(beats * DIVISIONS)}</duration></forward>"


def dynamic(mark: str) -> str:
    return (
        f'<direction placement="below"><direction-type><dynamics><{mark}/>'
        f"</dynamics></direction-type></direction>"
    )


def pedal(kind: str) -> str:
    """``kind`` is "start", "stop", or "change"."""
    return (
        f'<direction placement="below"><direction-type>'
        f'<pedal type="{kind}" line="yes"/></direction-type></direction>'
    )


def tempo(bpm: float) -> str:
    return f'<direction><sound tempo="{bpm}"/></direction>'


def attributes(
    *, divisions: int = DIVISIONS, staves: int = 1, meter: tuple[int, int] | None = None
) -> str:
    out = [f"<attributes><divisions>{divisions}</divisions>"]
    if meter:
        out.append(
            f"<time><beats>{meter[0]}</beats><beat-type>{meter[1]}</beat-type></time>"
        )
    out.append(f"<staves>{staves}</staves></attributes>")
    return "".join(out)


def repeat_forward() -> str:
    """`|:` at the left barline of this measure."""
    return '<barline location="left"><repeat direction="forward"/></barline>'


def repeat_backward(times: int = 2) -> str:
    """`:|`. ``times`` is how often the section is played in all, not how many
    jumps it makes."""
    attribute = f' times="{times}"' if times != 2 else ""
    return (
        f'<barline location="right"><repeat direction="backward"{attribute}/></barline>'
    )


def ending_start(*numbers: int) -> str:
    """The left barline of a first- or second-time bar."""
    joined = ",".join(str(number) for number in numbers)
    return (
        f'<barline location="left"><ending number="{joined}" type="start"/></barline>'
    )


def ending_stop(*numbers: int, repeat: bool = False) -> str:
    """The right barline that closes an ending block, with `:|` if asked."""
    joined = ",".join(str(number) for number in numbers)
    inner = f'<ending number="{joined}" type="stop"/>'
    if repeat:
        inner += '<repeat direction="backward"/>'
    return f'<barline location="right">{inner}</barline>'


def jump(**attributes: str) -> str:
    """A `<sound>` carrying one of the written jumps: `dacapo`, `dalsegno`,
    `segno`, `coda`, `tocoda`, `fine`."""
    written = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f"<direction><sound{written}/></direction>"


def measure(number: int, *contents: str) -> str:
    body = "".join(contents)
    return f'    <measure number="{number}">{body}</measure>\n'


def score(*measures: str, title: str = "test", parts: int = 1) -> str:
    """Wrap measures into a complete `score-partwise` document."""
    part_list = "".join(
        f'<score-part id="P{i + 1}"><part-name>Part {i + 1}</part-name></score-part>'
        for i in range(parts)
    )
    body = "".join(
        f'  <part id="P{i + 1}">\n{"".join(measures)}  </part>\n' for i in range(parts)
    )
    return _HEADER.format(title=title, part_list=part_list, parts=body)


# -- the fixtures --------------------------------------------------------


def piano_two_staves() -> str:
    """The case MusicXML exists to solve: the file says which hand is which."""
    return score(
        measure(
            1,
            attributes(staves=2, meter=(4, 4)),
            tempo(120),
            note("C5", 4.0, staff=1),
            backup(4.0),
            note("C3", 4.0, staff=2, voice=2),
        ),
        title="two staves",
    )


def tied_note() -> str:
    """Two tied half notes are one note of four beats, not two of two."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("D4", 4.0, tie="start")),
        measure(2, note("D4", 4.0, tie="stop")),
        title="tied",
    )


def chord() -> str:
    """Three notes, one attack. The <chord/> flag means no time passed."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            note("C4", 2.0),
            note("E4", 2.0, chord=True),
            note("G4", 2.0, chord=True),
            note("F4", 2.0),
        ),
        title="chord",
    )


def two_voices() -> str:
    """One staff, two voices, written with a backup between them."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            note("C5", 1.0, voice=1),
            note("D5", 1.0, voice=1),
            backup(2.0),
            note("C4", 2.0, voice=2),
        ),
        title="two voices",
    )


def dynamics_and_pedal() -> str:
    """What MIDI can only guess at, stated as notation."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            dynamic("pp"),
            pedal("start"),
            note("C4", 2.0),
            dynamic("ff"),
            note("E4", 2.0),
            pedal("stop"),
        ),
        title="dynamics and pedal",
    )


def grace_notes() -> str:
    """Notes with no duration, which land in the sweep path that once cost 428
    notes. They must exist, and must not consume time."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            note("B3", grace=True),
            note("C4", 4.0),
        ),
        title="grace",
    )


def division_change() -> str:
    """Divisions change between measures, so the same number means different
    lengths. Both measures are four beats."""
    return score(
        measure(
            1,
            attributes(divisions=12, meter=(4, 4)),
            tempo(120),
            note("C4", 4.0, divisions=12),
        ),
        measure(2, attributes(divisions=48), note("D4", 4.0, divisions=48)),
        title="division change",
    )


def tempo_change() -> str:
    """120 then 60, so the second bar takes twice as long as the first."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("C4", 4.0)),
        measure(2, tempo(60), note("D4", 4.0)),
        title="tempo change",
    )


def meter_change() -> str:
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("C4", 4.0)),
        measure(2, attributes(meter=(3, 4)), note("D4", 3.0)),
        title="meter change",
    )


def rests_only() -> str:
    """Rests take time and make no note."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), rest(4.0)),
        measure(2, rest(2.0), note("C4", 2.0)),
        title="rests",
    )


def forward_gap() -> str:
    """A forward leaves a gap rather than a rest."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), forward(2.0), note("C4", 2.0)),
        title="forward",
    )


def ensemble() -> str:
    """Two separate parts, which are instruments rather than hands."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("C5", 4.0)),
        title="ensemble",
        parts=2,
    )


def simple_repeat() -> str:
    """Two bars between repeat marks, so four bars are played: C D C D."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            repeat_forward(),
            note("C4", 4.0),
        ),
        measure(2, note("D4", 4.0), repeat_backward()),
        title="simple repeat",
    )


def first_and_second_endings() -> str:
    """C D | 1st: E :| 2nd: F. Played C D E C D F."""
    return score(
        measure(
            1,
            attributes(meter=(4, 4)),
            tempo(120),
            repeat_forward(),
            note("C4", 4.0),
        ),
        measure(2, note("D4", 4.0)),
        measure(3, ending_start(1), note("E4", 4.0), ending_stop(1, repeat=True)),
        measure(4, ending_start(2), note("F4", 4.0), ending_stop(2)),
        title="first and second endings",
    )


def da_capo_al_fine() -> str:
    """C D(fine) E(D.C.). Played C D E C D, stopping at the Fine."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("C4", 4.0)),
        measure(2, note("D4", 4.0), jump(fine="yes")),
        measure(3, note("E4", 4.0), jump(dacapo="yes")),
        title="da capo al fine",
    )


def dal_segno_al_coda() -> str:
    """C, segno D, E "to coda", F, G "D.S.", coda A. Played C D E F G D E A."""
    return score(
        measure(1, attributes(meter=(4, 4)), tempo(120), note("C4", 4.0)),
        measure(2, jump(segno="Segno"), note("D4", 4.0)),
        measure(3, note("E4", 4.0), jump(tocoda="Coda")),
        measure(4, note("F4", 4.0)),
        measure(5, note("G4", 4.0), jump(dalsegno="Segno")),
        measure(6, jump(coda="Coda"), note("A4", 4.0)),
        title="dal segno al coda",
    )


def empty_score() -> str:
    return score(measure(1, attributes(meter=(4, 4)), rest(4.0)), title="empty")


FIXTURES = {
    "piano-two-staves": piano_two_staves,
    "tied-note": tied_note,
    "chord": chord,
    "two-voices": two_voices,
    "dynamics-and-pedal": dynamics_and_pedal,
    "grace-notes": grace_notes,
    "division-change": division_change,
    "tempo-change": tempo_change,
    "meter-change": meter_change,
    "rests-only": rests_only,
    "forward-gap": forward_gap,
    "ensemble": ensemble,
    "simple-repeat": simple_repeat,
    "first-and-second-endings": first_and_second_endings,
    "da-capo-al-fine": da_capo_al_fine,
    "dal-segno-al-coda": dal_segno_al_coda,
    "empty": empty_score,
}


def write(name: str, directory: str) -> str:
    """Write one fixture to a directory and return the path."""
    from pathlib import Path

    path = Path(directory) / f"{name}.musicxml"
    path.write_text(FIXTURES[name](), encoding="utf-8")
    return str(path)


def compressed(name: str, directory: str) -> str:
    """The same fixture as a `.mxl`, the zipped container form."""
    import zipfile
    from pathlib import Path

    path = Path(directory) / f"{name}.mxl"
    inner = f"{name}.musicxml"
    container = (
        '<?xml version="1.0" encoding="UTF-8"?><container><rootfiles>'
        f'<rootfile full-path="{inner}" '
        'media-type="application/vnd.recordare.musicxml+xml"/>'
        "</rootfiles></container>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr(inner, FIXTURES[name]())
    return str(path)


def all_names() -> Sequence[str]:
    return sorted(FIXTURES)
