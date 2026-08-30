# Build plan

Eight milestones. Each one ends with something you can actually run, and each one's
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

## M1 - Score model and MIDI ingest

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
OST MIDI and on a solo piano MIDI.

---

## M2 - Minimal renderer

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
the right keys at the right times.

---

## M3 - Constraint and difficulty engine

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
and confirm is playable.

---

## M4 - Full visual specification

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

---

## M5 - Audio

Four backends behind one protocol, chosen by config, each degrading gracefully when its
requirements are absent.

- `none` - silent. Zero dependencies. The fallback when nothing else is available.
- `builtin` - additive synthesis with an ADSR envelope in numpy, honouring velocity and
  CC64 sustain. Roughly a hundred lines, no external dependencies, sounds cheap but
  always works.
- `fluidsynth` - `pyfluidsynth` plus the native FluidSynth library plus your SoundFont.
  Best quality; velocity and pedal are actually audible. *(Note: the native library is
  not currently installed on this machine - the backend must detect that and fall back
  with a clear message rather than crashing.)*
- `mux` - mux a user-supplied audio file with a configurable offset, for when you have
  the recording the MIDI came from.

**Tests:** backend selection and fallback logic; output duration matches score duration;
a smoke test that the muxed video has an audio stream.

**Exit criteria:** `psv run song.mid -o out.mp4` produces a video with synchronised
audio under each available backend.

---

## M6 - Arrangement

Left for last on purpose. It is the only genuinely fuzzy stage, and everything above is
useful without it - a solo piano MIDI goes straight from parse to constrain to render.

- `psv/arrange/salience.py` - score each note by melodic role, bass function, harmonic
  necessity, velocity, register, and onset density.
- `psv/arrange/reduce.py` - drop low-salience notes until the texture fits two hands.
- `psv/arrange/hands.py` - partition by register and continuity, minimising crossings
  and jumps. Trusts existing track/channel splits when the source already separates
  hands, and skips entirely when the input is already solo piano.

**Honest expectation:** this is heuristic and always will be. It should produce a
*learnable* arrangement, not a publishable one. Hand-fixing the intermediate MIDI and
re-running from `constrain` is a supported workflow, not a failure.

**Exit criteria:** a multi-track orchestral MIDI reduces to two hands that, after
constraining, render into something recognisably the piece.

---

## M7 - Polish and extensions

Ordered by usefulness, not committed to:

- Duet and multi-part mode (the data model already allows it).
- Opt-in visual effects, off by default, never at the cost of readability.
- A TUI over the same core, if the CLI ever feels limiting.
- Preset config profiles (`--preset small-hands`, `--preset beginner`).

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
