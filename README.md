# piano-song-to-visual

Turn a MIDI file into a Synthesia-style falling-notes practice video — no hands, just
notes flowing onto a keyboard — arranged so a human can actually play it.

> **Status: pre-alpha.** MIDI ingest works: `psv inspect` and `psv export` are real
> commands you can run today. The arrange, constrain, and render stages are not built
> yet. See [the roadmap](docs/ROADMAP.md).

## Why this exists

If you learn piano by watching and listening rather than by reading sheet music, you're
stuck with whatever arrangements happen to exist on YouTube. For a lot of music — game
soundtracks especially — nothing exists.

This tool takes a MIDI and produces the video you'd have wanted someone to make, with
three things standard falling-note videos leave out:

- **Dynamics you can see.** Note brightness tracks velocity, so loud and soft are
  visible rather than guessed at.
- **Pedal lanes.** A lane to the right of the keyboard where pedal presses fall like
  notes do, showing exactly when the sustain pedal goes down and for how long.
- **An alignment grid.** Faint horizontal and vertical rules, so you can tell that two
  notes an octave and a half apart are actually simultaneous.

And one thing no falling-note renderer does at all:

- **A hand-span constraint that is guaranteed, not suggested.** You set your maximum
  comfortable simultaneous reach; the arrangement is rewritten so nothing in the output
  ever exceeds it. Difficulty is a separate knob — a harder setting gives you more notes
  and faster passages, never a wider stretch.

## Scope

**MIDI in, video out.** Converting general audio into MIDI is a separate problem with
separate failure modes, and it will live in its own tool that emits MIDI you feed in
here. Keeping it out means this repo has no machine-learning dependencies, installs in
seconds, and doesn't inherit anyone else's transcription errors.

## Pipeline

```
MIDI ──▶ parse ──▶ arrange ──▶ constrain ──▶ render ──▶ video
```

Any stage runs on its own, so you can hand-fix an intermediate MIDI and pick up from
there. Full reasoning, including component choices per stage, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Install

Requires **Python 3.12+** and **ffmpeg** on your `PATH`.

```bash
git clone https://github.com/RinkyDinkyNooble/piano-song-to-visual
cd piano-song-to-visual
pip install -e ".[all]"
```

The base install is tiny. Video rendering and MIDI-to-audio synthesis are optional
extras:

```bash
pip install -e .            # parse, arrange, constrain
pip install -e ".[render]"  # + video output
pip install -e ".[all]"     # + FluidSynth audio
```

The `fluidsynth` audio backend additionally needs the native FluidSynth library and a
SoundFont; the `builtin`, `mux`, and `none` backends do not.

## Usage

Working today:

```bash
psv inspect song.mid
```

```
beethoven-op18-no4-i-quartet
  duration       514.8s
  notes          6151 in 4 part(s)
  range          C2 to C7
  polyphony      peak 11, mean 3.7
  widest span    51 semitones at 503.5s
  tempo          138 BPM, constant
  meter          4/4
  dynamics       none (every note the same velocity)
  pedal          none
  hands          not separated; needs the arrange stage
```

That last pair of lines is the point: it tells you whether a file carries the dynamics
and pedal data the visuals depend on, and whether it needs the arrange stage at all.
Add `-v` for a per-track breakdown.

`psv export song.mid -o copy.mid` parses a file and writes it back out, which is how you
check that ingest understood it.

Once the remaining stages land:

```bash
psv run song.mid -o practice.mp4 -c my-hands.toml
```

## Configuration

Everything meaningful is configurable via a TOML file rather than flags you have to
remember. The sketch:

```toml
[hands]
max_span_semitones = 12   # hard limit on simultaneously held notes; 18 = 1.5 octaves
                          # never relaxed, at any difficulty

[difficulty]
level = "medium"          # note density, ornamentation, harmonic detail

[visual]
background = "grayscale"
black_key_bar_width = 0.6 # relative to white-key bars, so black keys read from far away
black_key_darkening = 0.2 # applied on top of the note's colour

[visual.colors]           # hue = which hand, brightness = how loud
left_hand  = "#4a90d9"
right_hand = "#5fb87a"
quiet = 0.35              # brightness at pp
loud  = 1.0               # brightness at ff

[visual.grid]
horizontal_every = "octave"
vertical_every = "beat"

[pedals]
lanes = 1                 # up to 3; sustain is the one MIDI reliably carries

[audio]
backend = "fluidsynth"    # fluidsynth | mux | builtin | none
soundfont = "~/soundfonts/piano.sf2"
```

## Tests

```bash
pytest
python scripts/fetch_test_songs.py   # optional: the two CC BY-SA test songs
```

Two public-domain songs are committed, so the suite runs green on a fresh clone with no
network. Every feature the tool promises is registered in `tests/features.toml` and
cannot be marked done until a test claims it. See [docs/TEST-PLAN.md](docs/TEST-PLAN.md).

## Licence

[0BSD](LICENSE) — do whatever you want, no attribution required.

Note that the licence covers *this code*. Music you feed it is your responsibility;
this is a tool for learning to play things yourself.
