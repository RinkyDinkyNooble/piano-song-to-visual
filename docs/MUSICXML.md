# MusicXML

MIDI made this tool guess which hand plays a note, from track names, and
guessing wrong once cost a real file a quarter of its notes. MusicXML does not
require the guess: a piano part is written on two staves and the file says which
staff every note is on.

That is the whole reason to read this format rather than converting it to MIDI
first. Converting through MIDI throws away the one thing worth having.

```bash
psv run sonata.musicxml -o practice.mp4
psv run sonata.mxl      -o practice.mp4    # the zipped container
psv inspect sonata.xml                     # told apart by content, not suffix
```

## What it gives that MIDI cannot

| | MIDI | MusicXML |
| --- | --- | --- |
| Hands | Guessed from track names | **Stated.** Staff 1 is the right hand |
| Dynamics | Velocity bytes, often all 64 | Written as `pp`, `ff` |
| Pedal | CC64, often absent | A mark with a start and a stop |
| Repeats | Already flattened | Unrolled here, into the same flat timeline |

Für Elise exported from MuseScore is written as 815 notes, 517 right and 298
left, and played as 951 once its repeats are unrolled, with 30 pedal presses.
The same piece as MIDI usually arrives with no dynamics, no pedal, and two
tracks named something like `track 1` and `track 2`.

## Repeats

A score is written once and played more than once. A falling note happens at a
time, so before anything else the measures are laid out in the order a player
meets them, each repeat spelled out in full. `|: :|` with a `times` count,
first- and second-time bars, D.C., D.S., segno, coda and fine.

Für Elise has two repeats, each with a first- and second-time bar. They take it
from 106 written measures to 127 played, 815 notes to 951, and 2:14 to 2:36.

The work is split in two. `repeats.measure_marks` reads the XML into one small
record per measure; `repeats.play_order` turns those records into a list of
measure indices and knows nothing about XML at all. Every mistake lives in the
second half, and it can be tested by writing the marks down directly rather
than by building a file to provoke it.

Three things are worth knowing.

**A D.C. or D.S. pass is not a repeat.** The repeats are not taken again on the
way back, and a first-time bar does not apply, so the last ending of a group is
played instead. Fine and the coda are obeyed only on that pass, which is what
they mean.

**A tie cannot reach across a jump.** One left open at a repeat barline is
dropped rather than joined to whatever the jump lands on. Left in place it
would swallow the next note on that pitch, which is the shape of the bug that
once cost 428 notes.

**Repeats do not nest.** A backward repeat returns to the most recent forward
repeat, or to the start of the piece when there is none. A `|:` written inside
a first- or second-time bar is beyond that, and such a score comes out shorter
than it is written. It is logged at warning level naming the measure, because
music going missing invisibly is the failure this project has already paid for
twice.

## Reading it

`xml.etree` and `zipfile`, both standard library. No new dependency. The
alternative was `music21`, which resolves to fourteen packages including
matplotlib, a plotting library, to parse a text format.

### The parts that are easy to get wrong

**`<backup>`** is the one that matters most. MusicXML writes a second voice by
*rewinding the cursor* rather than by stating a time, so a mistake here puts
every note of that voice in the wrong place. `<forward>` skips ahead, leaving a
gap rather than a rest.

**Ties** must fold into one note. Two tied half notes are one note of four
beats, not two of two; getting it wrong doubles the note count and re-attacks
the string. Read from `<tie>`, which is the sounding element, not from `<tied>`
inside `<notations>`, which is the engraved mark.

**`<chord/>`** means no time passed since the previous note, so the cursor
rewinds by that note's length rather than advancing.

**Divisions** are per quarter note and may change mid-score, so the same
duration number means different lengths in different bars.

**A metronome mark** is the engraved sign and `<sound tempo="">` is what it
means. Most software writes both and `<sound>` wins; a file carrying only the
mark would otherwise play at the default tempo with nothing to say so. A mark
with two note values and no `per-minute` reads "dotted quarter equals quarter",
which changes how the music is written rather than how fast it goes, and is not
a tempo.

**A transposing instrument** writes one pitch and sounds another. A clarinet in
B flat writes a C for the B flat below it. Read at written pitch it would sit a
tone sharp against every other part. Irrelevant to a piano, which is why it is
easy to miss.

**Grace notes** have no duration at all. They are given an eighth of a beat, so
they can be seen and played, and they do not advance the cursor. A zero-length
note would land in the sweep path that once cost 428 notes.

### Hands, precisely

A single part with notes on staff 1 and staff 2 is a piano score, and the
staves are the hands. Anything else, several parts especially, is an ensemble:
those notes come back unassigned and the arrange stage decides.

`psv inspect` reports "hands look already separated" whenever every note carries
a hand, *before* it considers register. Für Elise's left hand crosses up over
the right, so a register test alone calls it unseparated and contradicts the
arrange stage, which correctly leaves it alone.

## Test material

Two sets, split by licence, the same way the songs are.

**Generated fixtures**, `tests/fixtures/musicxml_builder.py`. Ours, no licence,
committed, and what CI runs against. Each states in code exactly what it tests.

**The Unofficial MusicXML Test Suite**, 29 files, fetched by
`scripts/fetch_test_scores.py` and verified against the SHA-256 in
`tests/assets/scores.toml`. MIT, which is permissive but conditional: it asks
that its copyright notice travel with the files. This repository is 0BSD and
promises downstream users no conditions at all, so those files are gitignored
rather than committed. Tests needing them skip when they are absent.
