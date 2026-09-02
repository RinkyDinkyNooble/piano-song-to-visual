# Build plan

Eight milestones, all done. Then the CI failures, a list of optional
touches, and the one big thing still ahead. Each one ends with something you can actually run, and each one's
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

## CI: fixed

Eight workflow logs were captured from GitHub Actions. Every failure traced back
to one of three causes, none of them a fault in `psv` itself: two were defects in
this repo's own test setup, and the third was a workflow asking for something a
private repository on a free account cannot give it.

### 1. Fixed: `FORCE_COLOR: 1` broke the CLI help assertion on Python 3.14

Python 3.14 colours argparse help, and `FORCE_COLOR` told it to do so even
though CI's stdout is not a terminal, so the test saw ANSI escapes between the
words it was matching. It failed on 3.14 only and passed on 3.12 and 3.13, which
is exactly why local runs never caught it.

The workflow now sets `NO_COLOR` instead. `FORCE_COLOR` bought prettier logs and
cost correctness: telling a program its output is a terminal when it is not is
a lie that something will eventually act on.

The assertion also strips ANSI now, so anyone who sets `FORCE_COLOR` in their own
shell does not hit the same thing.

### 2. Fixed: ffmpeg pipes were left for the garbage collector

`imageio_ffmpeg.read_frames` only closes its subprocess pipes when ffmpeg is
*still running* as the generator finishes:

```python
if process.poll() is None:
    process.stdout.close()
    process.stdin.close()
```

Consume the generator to the end, as frame counting must, and ffmpeg has already
exited on its own, so that branch is skipped and the pipes fall to the garbage
collector. It raises `ResourceWarning` from a destructor at some unrelated later
moment; pytest promotes unraisable exceptions to failures, and this project
treats warnings as errors, so the suite failed and blamed whichever test was
running when the collector caught up. That is why the reported failures included
tests that never open a video.

Closing the generator does not help, because an exhausted generator's `close()`
is a no-op.

`tests/probe.py` now takes two different routes on purpose. `video_meta` stops
after the first yield, while ffmpeg is alive, which is the case imageio cleans up
correctly. `frame_count` avoids imageio entirely and runs ffmpeg through
`subprocess.run`, which owns and closes its own pipes.

**This one could not be reproduced locally.** Re-reading the logs, it failed on
Linux and macOS only; the Windows run failed on the colour assertion alone. POSIX
wraps subprocess pipes in file objects that warn when finalised, and Windows does
not. So the fix is correct by construction rather than by reproduction, and CI is
the thing that confirms it.

Worth keeping `filterwarnings = ["error"]` rather than relaxing it. It caught a
real leak that would otherwise have gone unnoticed.

### 3. Fixed by going public: CodeQL had nowhere to upload its results

Code scanning was not enabled on the repository, and while the repository was
private it could not be: GitHub gives code scanning away on *public*
repositories, and on a private one it needs Advanced Security, which is an
organisation product. There was no setting on a personal free account to turn
on, so the workflow was deleted rather than left warning on every push about
something nobody could act on.

Making the repository public is what fixed it. The workflow is back, unchanged,
and now has somewhere to upload to.

`ruff`'s bandit ruleset runs alongside it and is the check that actually reads
this code; CodeQL is the belt-and-braces layer.

### Also noted, not urgent

GitHub is deprecating Node 20 on Actions runners. The workflow already runs on
Node 24 and nothing here pins Node 20, so there is nothing to do unless a pinned
action starts failing.

### The wider lesson

The matrix runs three Python versions across three operating systems, and local
development is one of each. A 3.12-only regression, or a POSIX-only one, cannot
be seen here. Running the matrix before pushing is not possible; reading the logs
after is, and was not being done.

---

## M8 - Optional touches

Everything deliberately left out of the MVP. **Nothing here is needed to use the
tool.** Each item makes it nicer, and each is independent of the others, so they
can be picked off in any order. Roughly ordered by value.

The four most useful are done, and are written up first. The rest is still
open.

### Done: the practice settings

The four things that turn a video of the piece into something you learn from:
play it slower, play forty bars of it, count yourself in, play one hand.

They landed together because they are one feature underneath. All four need to
know where the bar lines are, and nothing did: `TempoMap` knows when the beats
happen and `TimeSignature` knows how many are in a bar, but neither can answer
"when does bar 31 start" alone. `Meter` is the pair that can, and it is now what
`beat_lines = "bar"` draws from as well, so bar lines stay on the bars through a
meter change instead of drifting from the change onward.

- **Practice tempo**, `--tempo 0.75`. Scales the notes, the pedalling and the
  tempo map together, so the grid stays on the music.
- **Section practice**, `--bars 20-40`. Inclusive of both ends, counting from 1,
  running to the start of the bar after the last one plus the usual tail.
- **Count-in and metronome**, `--count-in 2` and `--metronome`. The count-in
  beats are extrapolated backwards at a steady tempo, because there is no music
  there to follow; the clicks during the piece come from the tempo map and the
  bar index, so they track tempo and meter changes.
- **One hand at a time**, `--hands left`. A filter on the soundtrack, not on the
  picture: the other hand stays on screen, faintly.

All four also live in a `[practice]` config table, and a flag beats the file.

**Where they run matters more than what they do.** They are applied after
arrange and constrain, never before. The constraint engine measures overlaps in
real seconds against a fixed tolerance, so scaling the time first would change
which stretches it repaired and hand back a different arrangement at every
practice speed. Slowing a piece down must not quietly re-arrange it.

Two things came out differently from the original sketch.

The count-in is a window that opens *before* the music rather than silence
spliced into the score. Every note keeps the time it already had, so the picture
and the soundtrack cannot end up disagreeing about where the music starts, and
`render_frame` needs no notion of a lead-in at all.

`--start` now defaults to unset rather than 0, so `--bars` can tell whether it
was given, and combining them is an error rather than a guess.

**Exit criteria:** a hard passage renders on its own, slowly, one hand, with two
bars of clicks in front of it. *(Met, and the arrangement is asserted identical
across practice tempos.)*

### Worth doing first

**Accept global flags in either position.** `-c` and `-v` are defined on the top
level parser, so they only work *before* the subcommand: `psv -c x.toml run ...`
is accepted and `psv run ... -c x.toml` is a usage error. Nothing about the flags
suggests that, and it has caught a user twice. Give each subparser the same
options, or attach them through `parents=`, so either order works.

**An unlimited-span mode.** `hands.max_span_semitones` is validated to 1 to 18,
so there is currently no way to say "leave it exactly as written". Sometimes that
is what you want: to see the real piece before deciding what to give up, or
because a passage is playable rolled or redistributed in a way the engine cannot
know about. Allow `max_span_semitones = 0` or `"none"` to mean no limit, and have
`constrain` then verify nothing and change nothing.

This does not weaken the guarantee. The promise is that output never exceeds *the
configured* span; asking for no limit is a different request, not a violated one.
It should be loud in the report, so an unplayable arrangement is never a surprise.

**Say what the instruments are.** `audio.program` is a bare General MIDI number,
so finding anything past a few known ones means guessing and re-rendering. Add
`psv instruments`, listing the 128 GM names, and flag the handful worth trying on
a piano piece. Better still, read the SoundFont's own preset names, since a
SoundFont may not follow the GM map at all.

**Document adding other sounds.** Swapping instruments is really swapping
SoundFonts, and nothing says so. A short guide: where to get `.sf2` files, that
bigger usually means better sampled, that `program` indexes into whatever the
font provides, and how to point `audio.soundfont` at a new one.

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

**Borders on the note bars, with a configurable size.** Repeated notes on the
same key currently draw as one continuous bar. There is already a horizontal gap
between adjacent *pitches*, so a chord reads as separate notes, but nothing
separates consecutive notes in the same column: play the same key four times
quickly and you see one long block and have to count it by ear. That defeats the
point of the video.

Two ways to fix it, and they are not exclusive. Trim a small amount off the
bottom of every bar, the vertical twin of `BAR_GAP_RATIO`, so consecutive notes
never touch. Or outline each bar in a darker shade of its own colour, which
separates them and keeps the hand hue readable. The outline is probably the
better default, since a gap eats into short notes that are already only a few
pixels tall at speed.

Either way the size wants to be configurable: the right amount depends on
resolution and on how fast the piece is, and at 320 pixels wide a border that
looks right at 1080p would swallow the bar entirely.

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

## M9 - The composer: audio to playable piano

The thing this project was originally for. Point it at a recording of anything,
get back a piano arrangement you can learn.

    audio (mp3, mp4, flac, wav) -> transcribe -> MIDI -> arrange -> constrain -> video

**Most of this already exists.** Everything from `arrange` rightwards is built and
tested: reduction to two hands, the guaranteed hand span, difficulty, the video.
The only missing stage is the first one, turning audio into note data.

### The approach

**Use an existing transcription model. Do not train one.** Training an
audio-to-MIDI model needs a large aligned audio-and-MIDI corpus and serious
compute, and the published models are already close to the state of the art. The
work here is choosing and wiring one up, not inventing one.

Candidates, by what the source is:

| Source | Model | Why |
| --- | --- | --- |
| Solo piano recording | ByteDance high-resolution piano transcription | Best onsets and offsets by a distance, and the only one that also **detects the sustain pedal** |
| Anything else | Spotify `basic-pitch` | General polyphonic, permissive licence, ONNX, runs on CPU in seconds |
| Multi-instrument, best quality | Google MT3 or MR-MT3 | Transcribes several instruments to separate tracks, which is exactly what `arrange` wants. Heavy: needs a GPU to be pleasant |

Recommendation: start with `basic-pitch` because it is small, permissive, and
CPU-only, and add the ByteDance model for piano sources, since the pedal data it
recovers is worth having and nothing else provides it. Treat MT3 as a later
upgrade rather than a starting point.

For `.mp4` and other containers, ffmpeg is already a dependency and can extract
the audio track, so video files need no extra machinery.

### The honest limit

**Transcription quality is the ceiling on the whole thing, and no amount of
downstream cleverness raises it.** A clean solo piano recording transcribes very
well. A dense orchestral mix, or anything with drums and distorted guitars, does
not: you get approximately the right notes, plus spurious ones, minus quiet ones.
What comes out is a starting point to correct by hand, not a finished score.

This is precisely why MIDI stayed the input format for the main tool, and why
this belongs in its own stage with its own honest reporting rather than being
folded silently into `run`.

### Where it should live

A separate package, `psv-transcribe`, or an optional extra here. Keeping it apart
means the machine-learning dependency stack stays out of the main tool: `psv`
currently installs in seconds and its CI runs in under twenty, and neither should
change for people who already have MIDI.

The seam is a file. The transcriber emits MIDI; `psv` reads MIDI. Nothing else
needs to couple.

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
