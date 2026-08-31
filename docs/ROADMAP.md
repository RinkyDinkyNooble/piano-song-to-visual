# Build plan

Eight milestones, then a list of optional touches. Each one ends with something you can actually run, and each one's
exit criteria are checkable rather than vibes. Ordering is chosen so that the fuzziest,
least-certain work happens last, on top of foundations that are already proven.

**Decisions this plan assumes** (settled, not open):

| Decision | Choice |
| --- | --- |
| Input | MIDI only. Audio to MIDI is a separate future tool. |
| Interface | Core library + thin CLI. No GUI. |
| Note colour | Hue = which hand; brightness/saturation = velocity. |
| Audio | Four pluggable backends: `fluidsynth`, `mux`, `builtin`, `none`. |
| Hand span | Hard invariant, verified on every run, never relaxed by difficulty. |
| Python | 3.12+ (numpy's type stubs need it); CI covers 3.12 through 3.14. |

---

## M0 - Foundations (done)

Packaging, linting, strict typing, tests, CI across three OSes and three Python
versions, CodeQL, Dependabot, issue/PR templates, 0BSD licence, architecture doc.

**Exit criteria:** `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`, and
`python -m build` all pass locally and in CI. *(Verified.)*

---

## M1 - Score model and MIDI ingest (done)

The foundation everything else reads and writes. Nothing downstream should ever touch
`mido` or think about ticks.

**Modules**

- `psv/model.py` - the intermediate representation:
  - `Note` - pitch, start/end in seconds, velocity, `hand`, `provenance`, source track.
  - `PedalEvent` - pedal number, start/end, depth (for half-pedalling).
  - `Part` - an ordered set of notes belonging to one player's one hand.
  - `Score` - parts, tempo map, time signatures, pedal events, source metadata.
  - `Hand` and `Provenance` enums. Provenance records whether a note is original,
    reassigned, octave-shifted, truncated, or dropped - this is what makes the
    constraint engine auditable instead of a black box.
  - Parts are a *list*, not a left/right pair, so duet mode is later config rather than
    a rewrite.
- `psv/tempo.py` - `TempoMap`: tick/second conversion, beat and bar boundary iteration.
  The renderer's vertical grid lines and the difficulty engine's quantisation both need
  this.
- `psv/midi/read.py` - `mido` to `Score`. Handles multi-track files, note-on-with-
  velocity-zero as note-off, overlapping same-pitch notes, tempo and time-signature
  changes, CC64/66/67 with a configurable half-pedal threshold, and excluding the
  drum channel.
- `psv/midi/write.py` - `Score` to MIDI, so every intermediate is hand-editable.
- `psv/config.py` - TOML loading via stdlib `tomllib` into validated dataclasses, with
  clear errors pointing at the offending key. No config value reaches a subprocess
  unvalidated.
- `psv inspect` - reports track count, instruments, peak and mean polyphony, pitch
  range, whether pedal data exists, whether hands appear pre-separated, and the widest
  simultaneous span already present. This report is what tells you whether a given file
  even needs the arrange stage.

**Tests:** round-trip Score to MIDI to Score equivalence; synthetic fixtures for each
parsing edge case; tempo-map arithmetic against hand-computed values.

**Exit criteria:** `psv inspect` gives an accurate report on a real multi-track game
OST MIDI and on a solo piano MIDI. *(Met: the report is checked against the recorded
contents of both committed songs, and every fixture round-trips through
`Score -> MIDI -> Score` unchanged. 14 features marked done, 203 tests, 96% coverage.)*

Shipped slightly beyond the original list: `psv export` was added so the round trip is
reachable from the command line, and the pedal threshold defaults to 1 rather than the
conventional 64, because half-pedalling is exactly the sort of thing this tool exists to
make visible. Set `pedals.threshold = 64` for the on/off reading.

---

## M2 - Minimal renderer (done)

Deliberately placed *before* the constraint engine. The fastest way to understand and
trust what the constraint engine does to a piece is to watch a before-and-after video
of it. Building a rough renderer first makes M3 inspectable instead of theoretical.

**Modules**

- `psv/render/geometry.py` - 88-key layout, A0 (21) to C8 (108). White-key index
  mapping, black-key positions and widths, pitch to x-coordinate. Pure math, fully
  unit-testable with no rendering involved.
- `psv/render/frame.py` - `render_frame(score, config, t) -> ndarray`. A pure function:
  same inputs, same pixels, always. Draws background, falling note bars, and the
  keyboard with pressed keys highlighted.
- `psv/render/video.py` - frame loop piping raw RGB to ffmpeg via `imageio-ffmpeg`.
  Only notes inside the visible time window are considered, via an interval index, so
  cost scales with what is on screen rather than with song length.

Scope limit for this milestone: white and black bars, correct geometry, correct timing.
No colour mapping, no grid, no pedal lanes - those are M4.

**Tests:** geometry unit tests; frame determinism; reference-image comparison at a small
resolution against committed PNGs.

**Exit criteria:** a solo piano MIDI renders to a silent MP4 whose notes visibly land on
the right keys at the right times. *(Met: verified by eye against the Moonlight Sonata,
and pinned by four committed reference frames plus a test asserting that a bar's bottom
edge sits exactly on the keyboard at the note's start time.)*

Two things came out differently from the plan. Black-key bars are already drawn thinner
than white ones, because that is geometry rather than colour and there was no reason to
defer it; F-29 is therefore done. And `visual.width`/`height` must now be even, because
imageio otherwise pads the frame up to a multiple of 16 and hands back a video that is
not the size that was asked for.

---

## M3 - Constraint and difficulty engine (done)

The custom part. No existing tool does this, and it is the part worth understanding in
detail rather than receiving as a black box.

### How span enforcement works

**Detection.** Sweep-line over all note-on and note-off events in time order, keeping a
sorted active set per hand. At every event boundary, the hand's span is
`max(pitch) - min(pitch)`. Any boundary where that exceeds `max_span` opens a violation
interval; it closes when the span drops back under.

One subtlety that matters a lot in practice: a note released 10 ms after the next one
starts is not a stretch you have to make - it is sloppy MIDI. So overlaps shorter than
`overlap_tolerance_ms` (default around 30 ms) do not count as simultaneous.

**Repair**, in order of musical preference. Each is a pure function
`(Score, Violation) -> Score | None`, returning `None` when it does not apply:

1. **Reassign hand.** Move the outlier note to the other hand, if that hand can take it
   without creating a new violation or an awkward crossing. Costs nothing musically.
2. **Octave shift.** Displace the outlier by 12 semitones until it fits. Preserves pitch
   class and harmonic function; bounded by the 88-key range.
3. **Truncate.** Shorten the earlier note so the overlap falls under tolerance. This one
   is nearly free *when the sustain pedal is down* - the string keeps ringing, so
   lifting the key early is literally inaudible. The engine checks pedal state and
   prefers truncation heavily when CC64 is held.
4. **Drop.** Remove the least-salient note. Last resort, and always logged.

**Search.** Greedy, lowest-cost-first per violation, re-detect, iterate to a fixed point
with a bounded iteration count so termination is guaranteed. Notes that do not violate
anything are never touched - a conforming input comes out unchanged.

**Verification.** `verify_span(score, max_span)` returns the list of remaining
violations. It runs at the end of *every* `constrain` invocation, not just in tests. If
it ever returns non-empty, that is a hard failure, not a warning.

### Difficulty

A separate, orthogonal knob: note density, ornamentation, rhythmic subdivision, how much
inner harmony survives. Raising difficulty adds notes and speed. It has no code path
that can widen a span - that is enforced structurally by running difficulty *before*
span enforcement, so span always gets the last word.

**Tests**

- Unit tests per repair strategy, including the cases where each correctly declines.
- **Property test** (hypothesis): generate random scores - random chord widths, random
  overlaps, random tempos - run `constrain`, assert `verify_span()` is empty. This is
  the guarantee, tested as a guarantee.
- Pathological regressions: a ten-note cluster spanning three octaves in one hand; both
  hands already at maximum; an outlier that cannot be octave-shifted because it sits at
  the keyboard edge.
- Idempotence: constraining twice equals constraining once.
- Non-interference: a conforming score is returned unchanged.

**Deliverable beyond code:** `docs/CONSTRAINT-ENGINE.md`, written to be read rather
than skimmed - the algorithm, why each repair ranks where it does, and what the
provenance flags let you check afterwards.

**Exit criteria:** the property test passes over thousands of generated cases, and a
real orchestral MIDI forced through a 12-semitone limit produces a video you can watch
and confirm is playable. *(Met. BWV 565 goes from a 27-semitone left-hand reach to 12,
keeping 99.4% of its notes, and the before-and-after was checked by eye as a rendered
frame - which is what M2 existed for.)*

Two things worth recording. A bug in the sweep was found while writing the tests: it
judged each instant as soon as any note started, so a chord got evaluated half-built and
the reported extremes were wrong. It now settles the whole instant first. And the MIDI
reader learned to recover hands from track names, without which stages could not be
chained through intermediate files at all - that is F-47, which landed here rather than
in M5.

---

## M4 - Full visual specification (done)

Everything the M2 renderer deliberately skipped.

- **Colour.** Hue by hand (two distinct families), brightness and saturation by
  velocity within each family. Fully config-driven.
- **Black keys.** Bars drawn thinner than white-key bars so they are distinguishable by
  *shape* from far up the screen, and darkened on top of their dynamics colour so they
  are distinguishable by *tone* up close. The darkening composes with the colour rather
  than replacing it.
- **Pedal lanes.** Up to three lanes to the right of the keyboard, pedal presses falling
  down them exactly like notes, with held duration visible. Defaults to one lane
  (sustain), because that is the one MIDI reliably carries.
- **Grid.** Faint horizontal lines at a configurable pitch interval and faint vertical
  lines at a configurable beat interval, over a grayscale background. Vertical lines
  come from the tempo map, so they stay correct through tempo changes.

**Tests:** reference images per feature; a config-matrix test that renders one frame
under many configurations and checks none of them crash or produce blank output.

**Exit criteria:** a rendered video where you can read dynamics, spot pedal presses, and
align two notes an octave and a half apart - the three things this project exists for.
*(Met, and checked by eye against the Moonlight Sonata as well as by six reference
frames.)*

Three notes on how it turned out.

The grid config keys were renamed to `pitch_lines` and `beat_lines`. The spec described
them by orientation, and had them crossed: it asked for horizontal lines to align
simultaneous notes, which is right, and then for vertical lines "for timing alignment",
which cannot be, since a vertical line spans all time. Naming each key after what it
marks removes the ambiguity rather than picking a side of it.

Saturation is deliberately not used. Washing quiet notes toward grey looks better and
costs the more important signal: at pianissimo you could no longer tell which hand is
playing. Hue stays at full strength all the way down, and brightness alone carries
loudness.

`visual.background` is now required to be grayscale, as the spec asks. Any hue back
there competes with the hues that carry hand identity. The rest of the non-note palette
was made exactly neutral for the same reason; the strike line had been faintly blue.

---

## M5 - Audio (done)

Three backends behind one call, chosen by config, each falling back to the next
when what it needs is missing.

- `none` - silence, zero dependencies.
- `builtin` - additive synthesis with an ADSR envelope in numpy. Honours velocity,
  and honours the sustain pedal: a note keeps ringing past its key release while
  CC64 is down, which is the same fact the constraint engine uses to decide that
  truncating under the pedal is free. If the audio ignored it, the two halves of
  the tool would disagree with each other.
- `mux` - use an audio file you already have.
- `fluidsynth` - a sampled instrument from a SoundFont, and the only one that
  sounds like a piano. The synth is stepped forward between events rather than
  fed a MIDI file, so its timing comes from the Score and matches the video
  exactly, and CC64 is sent at its real depth so half-pedalling reaches the
  sound as well as the picture. The DLL folder is named in config rather than
  expected on `PATH`, because pyfluidsynth finds it through `find_library`,
  which searches `PATH` only, and editing a user's environment for one optional
  backend is a poor trade.

The chain matters more than any one backend. A silent video with no explanation,
because a library was missing, is much worse than a cheap-sounding one that says
what happened.

**Exit criteria:** `psv run` produces a video with synchronised audio under each
backend, and a clear message where one cannot run. *(Met, all four.)*

---

## M6 - Arrangement (done)

Multi-instrument score to two hands, in two steps.

**Reduce.** Cap how many notes sound at once, dropping the least salient first,
so the texture could fit two hands at all. Outer voices score highly, so the
melody and bass survive and the inner harmony gives way.

**Assign hands.** Walk the piece choosing, at each instant, a pitch to split at.
The split is scored on whether each hand then fits inside the span limit, how
evenly the notes divide, and how far it has moved since the last instant. That
last term is what makes it work: the split follows the music, so crossing voices
do not confuse it, and moving is penalised, so a chord does not fling the hands
across the keyboard. Notes already sounding keep the hand they were given.

A file that already has two hands is left completely alone.

**Exit criteria:** a multi-track orchestral MIDI reduces to two hands that, after
constraining, render into something recognisably the piece. *(Met. The Beethoven
quartet keeps 98.6% of its notes, and arranging first leaves the constraint
engine 403 violations to fix instead of the 812 the placeholder register split
produced.)*

---

## M7 - The whole pipeline in one command (done)

`psv run song.mid -o practice.mp4` does everything: parse, arrange, constrain,
render, synthesise, mux. Each stage still runs alone on an intermediate file, so
hand-fixing a MIDI half way through and picking up from there is unaffected.

The video is rendered silent to a temporary file and the soundtrack muxed on
afterwards rather than interleaved. That keeps the renderer a pure function of
the score, and it means a failure in either half says which half. If the mux
fails, the picture is still written and the reason reported: losing a whole
render over a soundtrack would be the wrong trade for a practice tool.

**Exit criteria:** one command turns a MIDI into a video you can play along to.
*(Met.)*

---

## M8 - Optional touches

Everything deliberately left out of the MVP. **Nothing here is needed to use the
tool.** Each item makes it nicer, and each is independent of the others, so they
can be picked off in any order. Roughly ordered by value.

### Worth doing first

**Practice tempo** (`--tempo 0.75`). Render slower than written while keeping the
pitches. Learning a hard passage at three-quarter speed and working up is how a
piece actually gets learned, and this is the single most useful thing missing.
Scale the tempo map on the way into the renderer and the synth; the score itself
stays untouched.

**Section practice** (`--bars 20-40`). `--start` and `--seconds` already exist but
are in seconds, which means counting. Bars are what you actually think in. Needs a
bar index built from the tempo map and time signatures, both of which exist.

**Count-in and a metronome click.** Two bars of clicks before the music starts,
and optionally a click through it. Straightforward inside the built-in synth.

**Accept global flags in either position.** `-c` and `-v` are defined on the top
level parser, so they only work *before* the subcommand: `psv -c x.toml run ...`
is accepted and `psv run ... -c x.toml` is a usage error. Nothing about the flags
suggests that, and it has caught a user twice. Give each subparser the same
options, or attach them through `parents=`, so either order works.

**One hand at a time.** `--hands left` renders and sounds only one hand, with the
other still drawn faintly for reference. Hands are already assigned and audio is
already synthesised note by note, so this is a filter rather than new machinery.

### Sound

**Better built-in tone.** The current synth is additive sine harmonics with an
ADSR envelope. It is clearly synthetic. One short recorded piano sample per
octave, pitch shifted, would sound far better for very little code.

**Stereo, with pan following register.** Low notes to the left, high notes to the
right, as at the instrument.

### Visuals

**Config presets.** `--preset small-hands`, `--preset beginner`. Named bundles of
settings that go together, so getting a sensible result does not require
assembling a TOML file first.

**Note name labels.** Optional letters on the bars, useful when learning and
clutter when not, so strictly opt-in.

**Bar numbers down the side.** Makes it possible to say "the bit at bar 31".

**Opt-in visual effects.** Key-strike flashes, glow, particles. Off by default and
never at the cost of readability; the spec is explicit that effects usually hurt.

### Bigger things

**Duet and multi-part mode.** The data model already stores parts as a list rather
than a left/right pair specifically so this does not need a rewrite. What it needs
is a way to say how many players there are and which part is whose, and a renderer
that can show more than two hand colours.

**Better salience for arranging.** The current function is velocity, duration, and
an outer-voice bonus. Real salience needs harmonic analysis: which notes are chord
tones, which are passing, which carry the line. This is the largest single lever on
arrangement quality, and it is one function with two call sites.

**Fingering suggestions.** Genuinely hard, genuinely useful, and a project in its
own right.

**A live player instead of a video file.** A window that scrolls in real time, with
pause, rewind, and loop-a-section. Better for practice than a video. `render_frame`
is already a pure function of time, so the drawing code needs no changes: what is
missing is a window and an event loop.

**Speed, generally.** A 1080p60 render of a long piece takes minutes, and that is
the single biggest thing standing between a change and seeing it. Worth a proper
look rather than piecemeal tuning:

- **Parallel frame rendering.** Frames are independent and `render_frame` is
  pure, so this parallelises about as cleanly as anything ever does. Likely the
  largest single win, and the obvious place to start.
- **Profile before optimising anything else.** The per-frame cost is currently
  assumed, not measured. Measure first, then decide.
- **Draw only what changed.** The keyboard, lanes, and grid are identical across
  most frames; only the falling bars move. Rendering the static parts once and
  compositing could cut a lot of work.
- **Consider the encoder settings.** Frames are handed to ffmpeg one at a time
  and re-encoded at default quality; a faster preset may matter more than the
  drawing does.

Whatever it turns out to be, keep `render_frame` a pure function of the score and
the time. The reference-image tests and the determinism guarantee both rest on
it, and a speed-up that costs that is not worth having.

### Explicitly not planned

YouTube upload, or any distribution automation. This is a local tool.

---

## Cross-cutting, from M1 onward

These are built in as the code is written, not bolted on at the end:

- **Logging** at every stage boundary; `-v` for stage summaries, `-vv` for per-note
  decisions - especially every constraint-engine repair.
- **Types**: `mypy --strict` on everything, no exceptions granted.
- **Security**: MIDI, SoundFont, and audio inputs are untrusted. Parsing is bounded, no
  input-derived string reaches a shell, ffmpeg is invoked with argument lists. `ruff`'s
  bandit rules and CodeQL run on every push.
- **Docs**: every stage gets its rationale written down while the reasoning is fresh.
