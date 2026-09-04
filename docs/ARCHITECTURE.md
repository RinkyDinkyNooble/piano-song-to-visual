# Architecture

## Scope

**Input is a written score: MIDI or MusicXML.** Both arrive as the same `Score`,
and only the parse stage knows which one it read.

**Audio in is not planned.** Transcribing a recording sets a ceiling on quality
that no amount of downstream cleverness raises, and it would pull a
machine-learning dependency stack into a repo that installs in seconds. It was
planned once and dropped once MusicXML made it unnecessary for the repertoire
this is aimed at.

## A library with a CLI on top, and no GUI

Every stage is a function on plain data structures. The CLI is a thin shell over
those functions, and nothing in the core knows it exists, so another shell over the
same core is possible without touching anything below it.

There is no GUI, and that is a decision rather than an omission:

- The pipeline is **batch work**. Rendering is a minutes-long, non-interactive job.
  That fits a command you run and come back to, not a window you sit in front of.
- The configuration surface is **large and worth keeping**. Hand span, difficulty,
  colour maps, grid intervals, pedal lanes — these belong in a TOML file you edit and
  version, not in a dialog box whose state lives nowhere.
- Re-running a stage is the main workflow. `psv render -c mine.toml song.mid` after
  tweaking one colour is trivial to type and trivial to script over a folder.
- The one thing a GUI would genuinely add is **interactive arrangement editing**,
  dragging a note to the other hand when the reduction guesses wrong. Exporting a MIDI
  and fixing it in an editor that already exists does the same job without anyone
  writing a piano roll from scratch.

## Pipeline

```
                                                     ┌── audio backend ──┐
score ──▶ [1] parse ──▶ [2] arrange ──▶ [3] constrain ──▶ [4] render ──▶ ffmpeg ──▶ video
          Score          two hands       span + difficulty   frames
```

Stage [1] reads MIDI or MusicXML, told apart by content rather than by
extension. Only that stage knows the difference: everything downstream sees a
`Score`. MusicXML arrives with its repeats already unrolled, since a falling
note happens at a time and nothing after this point has a notion of going back.

Each stage reads and writes a serialisable intermediate, so any stage can be run,
inspected, hand-edited, and re-run alone.

Between [3] and [4] sits one more `Score -> Score` step, `psv.practice`: playback
speed, a bar range, one hand, and a count-in. It is not numbered because it
changes nothing about the piece, only how it is presented — and it runs *after*
the constraint engine rather than before, which is load-bearing. The engine
measures overlaps in real seconds against a fixed tolerance, so scaling the time
first would change which stretches it repaired, and the same file would come
back differently arranged at every practice speed.

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

1. **Salience scoring** — rank notes by how much they matter. Deliberately crude
   as it stands: velocity, duration, and a large bonus for the top and bottom of a
   simultaneity, since those are the melody and the bass. Real salience needs
   harmonic analysis, and this is one function with two call sites so there is one
   place to replace when it gets one.
2. **Reduction** — drop low-salience notes until the texture fits two hands.
3. **Hand assignment** — partition surviving notes into left/right by register and
   continuity, minimising crossings and jumps. Existing track/channel splits are
   trusted when the source already separates hands.

This is heuristic and always will be. It aims for a *learnable* arrangement, not a
publishable one.

### Stage 3 — Constraint & difficulty engine (the custom part)

Two orthogonal knobs, deliberately separated:

- **Hand span — a hard, always-enforced invariant.** For every instant, the set of
  notes held by one hand must fit within `max_span` semitones (default 12, ceiling
  36, and 0 means no limit at all rather than a very wide one).
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

**Visual encoding.** Hand identity is the hue; loudness is the brightness within
that hue. Left and right hand get distinct colour families, and hue stays at full
strength all the way down. Saturation is deliberately unused: washing quiet notes
toward grey looks better and costs the more important signal, since at pianissimo
you could no longer tell which hand is playing. Black-key bars are drawn thinner
than white-key bars and darkened further, so they are distinguishable by shape far
from the keyboard and by tone up close — independent of whatever dynamics colour
is applied.

`render_frame(score, config, t)` is a pure function, and stays one. Practising a
single hand is a keyword on it rather than a filtered score, because the muted
hand is still drawn: it is the soundtrack that goes quiet, not the picture.

Purity is load-bearing beyond testing. It is what lets the timeline be cut into
spans and rendered by separate processes, and it is why the optional effects in
`psv/render/effects.py` may derive from the score and a time but never from the
previous frame: state would seam at every span boundary.

**Two optional layers sit on top, both off by default**, because a practice aid
and a piece of spectacle want opposite things and both are legitimate. The theme
is static, a gradient background and hand colours and a border shade and a ramp
along each bar, and is judged from a picture. The effects are transient and tied
to when a note lands, derive from the score rather than from the previous frame,
and are listed in config in the order they draw.

**Colour range.** Video is written full range and tagged to say so. h264 defaults
to the television range, 16-235, which is right for camera footage and wrong for a
picture drawn in RGB: one grey level in seven has nowhere to land, and consecutive
levels collapse. Nothing notices until something moves slowly across a large flat
area, which is exactly what the `pulse` effect does.

### Audio

Pluggable backends, selected by config, degrading gracefully:

| Backend | Needs | Notes |
| --- | --- | --- |
| `fluidsynth` | `pyfluidsynth` + native FluidSynth + a `.sf2` | Best quality; velocity and pedal are actually audible, and `audio.reverb` puts it in a room |
| `mux` | a user-supplied audio file | For when you have the recording the MIDI came from; configurable offset |
| `builtin` | nothing beyond numpy | Simple internal synth; cheap-sounding but always works |
| `none` | nothing | Silent video, for fast iteration on visuals |

`audio.reverb` is one number from dry to a large hall, driving FluidSynth's four
reverb parameters together. It is the only audio effect there is and the only one
planned: anything with a threshold and a ratio is done better by software built
for it, and the `mux` backend already takes a finished audio file back, so the
professional route stays open as render silent, treat the audio elsewhere, mux it
in.

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
- **Security**: input files are untrusted. The parsers are the main attack surface -
  MIDI, MusicXML, the zip inside an `.mxl`, and SoundFonts. Parsing is bounded and no
  input-derived string is passed to a shell. `ruff`'s bandit rules and CodeQL run in
  CI.
