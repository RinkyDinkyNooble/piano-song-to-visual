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
| Repeats | Already flattened | **Not unrolled yet.** See below |

Für Elise exported from MuseScore reads as 815 notes, 517 right and 298 left,
with 22 pedal presses. The same piece as MIDI usually arrives with no dynamics,
no pedal, and two tracks named something like `track 1` and `track 2`.

## What it does not do yet

**Repeats are not unrolled.** The reader walks the measures once, in document
order, so a score with a repeat plays through once and comes out shorter than it
sounds. `<repeat>`, `<ending>`, D.C., D.S., segno, coda and fine are all
ignored. That is the next piece of work and doing half of it would be worse than
not starting: a partly-unrolled score is wrong in a way nobody can see.

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

**The Unofficial MusicXML Test Suite**, 23 files, fetched by
`scripts/fetch_test_scores.py` and verified against the SHA-256 in
`tests/assets/scores.toml`. MIT, which is permissive but conditional: it asks
that its copyright notice travel with the files. This repository is 0BSD and
promises downstream users no conditions at all, so those files are gitignored
rather than committed. Tests needing them skip when they are absent.
