# Test plan

The goal is not high line coverage. It is that **every feature the tool promises is
proven at least once by something that exercises it for real**, rather than only having
its internals unit tested. Unit tests catch broken functions. They do not catch a
feature that was wired up wrong, or quietly never wired up at all.

## How that is enforced

`tests/features.toml` lists all 54 user-visible features. Each entry has an id, the
milestone it belongs to, how it gets verified, a status, and the assets it needs.

Tests claim a feature with a marker:

```python
@pytest.mark.feature("F-20")
def test_span_never_exceeds_configured_limit(...): ...
```

`tests/test_feature_coverage.py` then enforces two rules on every run:

1. Every feature with `status = "done"` has at least one test claiming it. Marking a
   feature done without a test fails the suite.
2. Every id used in a marker exists in the registry, so a typo cannot fake coverage.

It also checks that ids are unique and well-formed, that every `uses` reference points
at a song or fixture that actually exists, and, through `test_the_gate_actually_fires`,
that rule 1 really does catch an uncovered feature. Without that last one, a checker
that always passes would look identical to a working one for as long as every feature
is still planned.

The workflow: implement a feature, write the test, add the marker, flip status to
`done` in the same change. Flipping early fails.

```bash
pytest -q -s          # the summary line shows total / done / covered
```

## Verification levels

| Level | Meaning |
| --- | --- |
| `e2e` | Drives the CLI or the full pipeline against a real file |
| `property` | Hypothesis generates inputs and asserts an invariant holds for all of them |
| `unit` | Exercised through one module's public interface |
| `visual` | Compared against a committed reference image |

Span enforcement is `property`, not `unit`, on purpose. The promise is "no input ever
produces an over-wide chord", and only generated inputs can test a claim of that shape.

## Test assets, and why there are two kinds

### Synthetic fixtures, in `tests/fixtures/midi_builder.py`

Nineteen small MIDI files built in code. They are the source of truth and are committed
as Python, not as binary blobs. `scripts/make_fixtures.py` writes them to
`tests/assets/generated/` (gitignored) when you want to open one in a DAW, but no test
reads those files.

They exist because **the real songs cannot test dynamics or pedalling**. Every song
below is engraved sheet music exported by LilyPond, which means uniform velocity and no
CC64. The fixtures cover what that leaves out, plus the deliberately pathological cases
no real score contains:

- `velocity-ramp`, `dynamic-levels` — the whole velocity range, for the colour map
- `sustain-pedal`, `half-pedal`, `three-pedals` — pedal lanes and partial depths
- `wide-span-chord`, `span-edge-cases` — violations, including at the keyboard edges
  where octave-shifting has nowhere to go
- `tiny-overlap` — a 10 ms overlap that must *not* count as a stretch
- `tempo-changes`, `time-signatures` — grid alignment through meter and tempo changes
- `full-keyboard` — all 88 keys, so no geometry error can hide
- `retriggered-pitch`, `zero-velocity-note-off`, `drum-channel`, `empty` — parser edges

### Real songs, in `tests/assets/`

Four pieces from the Mutopia Project, split by licence rather than by size:

| Song | Licence | In git | Why it is here |
| --- | --- | --- | --- |
| Bach, Toccata and Fugue BWV 565 | Public Domain | yes | Organ. The pedalboard staff sits far below the manuals, so reducing to two hands produces unavoidable span violations. The hardest honest constraint test. |
| Beethoven, Op. 18 No. 4 quartet, i | Public Domain | yes | Four instruments on four programs. The arrange stage's real problem, with no piano writing to fall back on. |
| Beethoven, Moonlight Sonata, i | CC BY-SA 2.5 | no | Already solo piano with hands separated: the control case, where arrange should decline to act. Also the clearest piece to eyeball a render against. |
| Bach, Invention No. 1 BWV 772 | CC BY-SA 3.0 | no | Two voices that cross repeatedly, so a register-only hand heuristic fails it. Shortest song, so it is the default for full renders. |

**The split is licensing, not size.** This repo is 0BSD, which puts no conditions on
anyone downstream. Public-domain files carry no conditions either, so committing them
is consistent. CC BY-SA files carry attribution and share-alike obligations that would
contradict the repo's own licence, so they stay out of git and are fetched on demand.

Committing the two public-domain songs also means CI can run real end-to-end tests with
no network access at all.

```bash
python scripts/fetch_test_songs.py           # get the CC BY-SA pair
python scripts/fetch_test_songs.py --check   # verify what is present
```

Fetches are SHA-256 verified against `tests/assets/songs.toml`, which also records
attribution for the CC BY-SA entries. Tests needing an unfetched song skip rather than
fail, so a fresh clone has a green suite.

## What each milestone must prove before it counts as done

- **M1** parses both committed songs and every parser-edge fixture, and `psv inspect`
  reports contents that match the numbers recorded in `songs.toml`. *(Done.)*
- **M2** renders a real song to a playable video, with reference-image tests pinning
  frame output. *(Done.)*
- **M3** passes the span property test over thousands of generated scores, and turns
  BWV 565 into something a human can play. *(Done.)*
- **M4** produces a video where dynamics, pedal presses, and alignment are all readable.
  Some of this is a judgement call and gets checked by eye against `moonlight`. *(Done.)*
- **M5** produces synchronised audio under every backend that is available, and a clear
  message under every backend that is not.
- **M6** reduces the quartet to two hands that still sound like the piece.

## Reference images

Six frames under `tests/assets/reference/` pin the renderer. They are 320x180, under a
kilobyte each, and generated by the project itself, so committing them raises no
licensing question.

```bash
python scripts/make_references.py --check   # report drift, change nothing
python scripts/make_references.py           # rewrite them
```

Regenerate only when a rendering change is deliberate, and look at the images before
committing. Regenerating to make a failing test pass is how a reference suite stops
being worth anything.

## Running things

```bash
pytest                              # everything
pytest -q -s                        # plus the feature coverage summary
pytest -m "feature"                 # only tests that claim a feature
python scripts/make_fixtures.py     # write fixtures out for inspection
```
