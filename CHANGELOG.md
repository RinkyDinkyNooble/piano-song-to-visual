# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- 189 tests covering M1, including a `Score -> MIDI -> Score` round trip over all 19
  fixtures and both committed songs. 14 features marked done; 96% coverage.

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
