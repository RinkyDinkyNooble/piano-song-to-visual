# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **M4: the full visual specification.** Everything the M2 renderer left out.
  - `psv.render.color` - hue for the hand, brightness for the velocity. Saturation is
    left alone on purpose so hand identity survives at pianissimo. Black-key bars are
    darkened on top of the dynamics colour, so the two channels compose.
  - Pedal lanes to the right of the keyboard, in the order the pedals sit under your
    feet. Presses fall exactly as notes do, and depth is drawn as brightness, so
    half-pedalling is visible rather than rounded to on/off.
  - The alignment grid: vertical rules at pitch landmarks, horizontal rules on the
    beat. Beat rules come from the tempo map, so they stay on the beat through a
    tempo change.
- **M3: the constraint and difficulty engine.** The part of this project that does
  not exist anywhere else. See `docs/CONSTRAINT-ENGINE.md`.
  - `psv.constraints.span` - sweep-line detection of every instant a hand is asked to
    reach further than allowed, plus `verify_span`, which runs on every `constrain`
    call rather than only under test.
  - `psv.constraints.repair` - five repairs ranked by what they cost the music:
    reassign, truncate-under-pedal, octave-shift, truncate, drop. Truncation outranks
    octave shifting only while the sustain pedal is down, because the string keeps
    ringing and lifting the key early is then inaudible.
  - `psv.constraints.difficulty` - texture thinning that only ever removes notes and
    runs before span enforcement, so no difficulty setting can widen a reach.
  - `psv.constraints.hands` - a deliberately simple register split, documented as a
    placeholder until the arrange stage replaces it.
  - `psv constrain`, with `-vv` to list every individual repair.
- **M2: the renderer.**
  - `psv.render.geometry` - 88-key layout as pure arithmetic, so it is checked
    exhaustively over every key rather than sampled. Black-key bars are drawn thinner
    than white ones, measured as a fraction of a white bar so the config key means what
    it says.
  - `psv.render.frame` - `render_frame(score, config, time)`, a pure function. Same
    inputs, same pixels, which is what lets four committed reference frames pin the
    output.
  - `psv.render.video` - lazy frame generation piped to the ffmpeg that
    `imageio-ffmpeg` ships, so no system install is needed.
  - `psv render`, with `--start`, `--seconds`, `--width`, `--height`, and `--fps` for
    fast iteration.
- **M1: score model and MIDI ingest.**
  - `psv.model` - `Score`, `Part`, `Note`, `PedalEvent`, and the `Hand`, `Provenance`,
    and `Pedal` enums. Immutable throughout, so stages are `Score -> Score` functions.
    Every note records what the pipeline has done to it.
  - `psv.tempo` - `TempoMap`, the only place that converts between ticks, beats, and
    seconds. `beat_times()` walks beats rather than seconds so the renderer's grid
    stays on the beat through a tempo change.
  - `psv.midi` - reader and writer. Handles note-on-with-velocity-zero as note-off, a
    pitch retriggered before its note-off, percussion on channel 9, tempo and meter
    events on any track, notes left hanging at end of track, and pedals as continuous
    controllers rather than switches.
  - `psv.config` - TOML loading into validated dataclasses. Unknown keys are an error
    naming the valid alternatives, and the hand-span limit cannot be set beyond human
    reach.
  - `psv.inspect` - the report behind `psv inspect`: polyphony, pitch range, widest
    simultaneous span, whether dynamics and pedal data exist, and whether the hands
    already look separated.
  - `psv inspect` and `psv export` commands.
- Hand assignment now survives a MIDI round trip, recovered from track names, so
  pipeline stages can be chained through intermediate files.
- 458 tests, 39 of 50 features marked done. Includes a
  `Score -> MIDI -> Score` round trip over all 19 fixtures and both committed songs,
  and hypothesis property tests for render determinism and for the span guarantee.

### Added (scaffolding)

- Project scaffolding: packaging, linting, type checking, tests, and CI.
- Architecture decision record covering the interface choice, the pipeline stages,
  and the component selection for each — see `docs/ARCHITECTURE.md`.
- Milestone-by-milestone build plan — see `docs/ROADMAP.md`.
- CLI skeleton (`psv`) defining the `inspect`, `arrange`, `constrain`, `render`,
  and `run` commands. None are implemented yet.
- Feature registry (`tests/features.toml`) covering all 50 user-visible features,
  with a coverage gate that blocks marking one done before a test claims it.
- Nineteen synthetic MIDI fixtures covering dynamics, pedalling, pathological hand
  spans, and parser edge cases that no real score contains.
- Four Mutopia Project test songs. The two public-domain ones are committed so CI
  can run offline; the two CC BY-SA ones are gitignored and fetched by
  `scripts/fetch_test_songs.py` with SHA-256 verification.
- Test plan explaining the two-tier asset strategy - see `docs/TEST-PLAN.md`.

### Changed

- **Scope narrowed to MIDI input.** Audio-to-MIDI transcription moves to a separate
  future tool, removing the machine-learning dependency stack from this project and
  decoupling output quality from transcription quality.
- Note colour now encodes hand as hue and velocity as brightness, rather than
  velocity alone, so hand identity and dynamics are both readable at a glance.
- Audio is produced by one of four pluggable backends (`fluidsynth`, `mux`,
  `builtin`, `none`) rather than a single fixed path.
- Minimum Python raised to 3.12; CI covers 3.12 through 3.14.
- `visual.width` and `visual.height` must be even. imageio otherwise pads the frame to
  a multiple of 16 and returns a video that is not the size that was requested.
- **Grid config keys renamed** to `visual.grid.pitch_lines` and
  `visual.grid.beat_lines`, from `horizontal_every` and `vertical_every`. The old names
  described orientation and had it crossed: in a falling-notes view a vertical line
  spans all time, so it cannot be the one that aids timing. The new names say what each
  line marks and cannot be got backwards.
- `visual.background` must now be grayscale, as the spec asks. The rest of the non-note
  palette was made exactly neutral too; the strike line had been faintly blue.
- `Note` ordering is now total. Two notes alike in pitch and timing but played by
  different hands used to sort arbitrarily, so regrouping a score by hand could reorder
  them and `Score.notes` was not deterministic. Found by a property test.
