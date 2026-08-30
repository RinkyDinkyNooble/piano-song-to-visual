# Architecture

## Scope

**Input is MIDI.** Audio-to-MIDI transcription is explicitly out of scope for this
project and will live in a separate tool that emits MIDI you feed in here. That
keeps this repo free of the machine-learning dependency stack, keeps installs and CI
fast, and — more importantly — keeps the tool's quality independent of transcription
quality, which was the one thing that could not be engineered around.

## Interface decision: a library with a CLI on top

**Recommendation: build a pure-Python core library, expose a CLI over it now,
and leave room for a TUI later. Do not build a GUI.**

Reasoning:

- The pipeline is **batch work**. Rendering is a minutes-long, non-interactive job.
  That fits a command you run and come back to, not a window you sit in front of.
- The configuration surface is **large and worth versioning**. Hand span, difficulty,
  colour maps, grid intervals, pedal lanes — these belong in a TOML file you keep and
  tweak, not in a dialog box whose state lives nowhere.
- Re-running a stage is the main workflow. `psv render --config mine.toml song.mid`
  after tweaking one colour is trivial to type and trivial to script over a folder.
- A GUI's only real advantage is **interactive arrangement editing** — dragging a note
  to the other hand when the reduction guesses wrong. That is a v2 concern, and it is
  better served by exporting a MIDI you fix in an existing editor than by writing a
  piano-roll editor from scratch.

So: every stage is a function on plain data structures, the CLI is a thin shell over
those functions, and any future TUI is another shell over the same core. Nothing in the
core knows the CLI exists.

## Pipeline

```
                                                    ┌── audio backend ──┐
MIDI ──▶ [1] parse ──▶ [2] arrange ──▶ [3] constrain ──▶ [4] render ──▶ ffmpeg ──▶ video
         Score          two hands       span + difficulty   frames
```

Each stage reads and writes a serialisable intermediate, so any stage can be run,
inspected, hand-edited, and re-run alone.

### Stage 1 — Parse (MIDI → Score)

`mido` reads the file; we convert to an internal `Score`: absolute-time notes with
pitch, onset, duration, velocity, and provenance, plus tempo map, time signatures, and
pedal (CC64/66/67) events. Ticks are resolved to seconds once, here, so no downstream
stage deals with tempo.

`psv inspect` reports what a file actually contains — track count, instruments,
polyphony, pitch range, whether pedal data is present, whether hands are already
separated. This drives every decision about what the later stages need to do.

### Stage 2 — Arrangement (many tracks → two hands)

Runs only when needed. A two-track solo piano MIDI already *is* the arrangement, and
the tool should not touch it.

1. **Salience scoring** — rank notes by how much they matter (melodic line, bass root,
   harmonic function, velocity, register, onset density).
2. **Reduction** — drop low-salience notes until the texture fits two hands.
3. **Hand assignment** — partition surviving notes into left/right by register and
   continuity, minimising crossings and jumps. Existing track/channel splits are
   trusted when the source already separates hands.

This is heuristic and always will be. It aims for a *learnable* arrangement, not a
publishable one.

### Stage 3 — Constraint & difficulty engine (the custom part)

Two orthogonal knobs, deliberately separated:

- **Hand span — a hard, always-enforced invariant.** For every instant, the set of
  notes held by one hand must fit within `max_span` semitones (default 12, ceiling 18).
  Enforced by sweeping the timeline, detecting violating instants, and repairing them —
  by moving a note to the other hand, octave-displacing it, shortening it so it no
  longer overlaps, or dropping it, in that order of preference.
- **Difficulty — a soft, tunable target.** Note density, ornamentation, rhythmic
  subdivision, how much inner harmony survives. Raising difficulty *never* relaxes span.

The guarantee is testable as a property: *for any input and any config, no instant in
the output has a one-hand span exceeding the configured maximum.* Property-based tests
against that invariant are the point of this module. Conforming notes are never touched;
the engine only edits what actually violates.

### Stage 4 — Render

A custom offline renderer, not a wrapper around an existing app.

Existing falling-note renderers (MIDIVisualizer, Synthesia, Piano From Above) are either
closed, C++/OpenGL apps built to be driven by a human, or both — and none draw the pedal
lanes, dynamics scale, and alignment grid this project exists to provide. Wrapping one
costs more than writing the drawing code, which is 2D rectangles on a timeline.

Frames are drawn deterministically (numpy/Pillow) and piped to ffmpeg. Deterministic and
headless means the renderer is testable in CI: render frame N of a fixed arrangement,
compare against a reference image.

**Visual encoding.** Hand identity is the hue; dynamics is the brightness/saturation
within that hue. Left and right hand get distinct colour families, and within each,
quiet notes read dark and desaturated while loud notes read bright and saturated. Both
channels stay legible without fighting each other. Black-key bars are drawn thinner than
white-key bars and darkened further, so they are distinguishable by shape far from the
keyboard and by tone up close — independent of whatever dynamics colour is applied.

### Audio

Pluggable backends, selected by config, degrading gracefully:

| Backend | Needs | Notes |
| --- | --- | --- |
| `fluidsynth` | `pyfluidsynth` + native FluidSynth + a `.sf2` | Best quality; velocity and pedal are actually audible |
| `mux` | a user-supplied audio file | For when you have the recording the MIDI came from; configurable offset |
| `builtin` | nothing beyond numpy | Simple internal synth; cheap-sounding but always works |
| `none` | nothing | Silent video, for fast iteration on visuals |

The renderer never depends on which backend ran; audio is muxed as a final step.

## Data model

The intermediate representation carries more than MIDI does — hand assignment, dynamic
class, and provenance (was this note original, moved, octave-displaced, or dropped by the
constraint engine?). Provenance is what makes the engine auditable rather than a black
box. It is modelled as a list of parts rather than exactly two hands, so duet mode is a
configuration change later rather than a rewrite.

## Cross-cutting

- **Logging** at every stage boundary, with `-v`/`-vv` controlling depth.
- **Tests** per stage, plus invariant tests for stage 3 and reference-image tests for
  stage 4.
- **Security**: input files are untrusted. The MIDI parser is the main attack surface;
  parsing is bounded and no input-derived string is passed to a shell. `ruff`'s bandit
  rules and CodeQL run in CI.
