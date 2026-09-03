# piano-song-to-visual

Turn a MIDI file into a Synthesia-style falling-notes practice video — no hands, just
notes flowing onto a keyboard — arranged so a human can actually play it.

> **Status: working MVP.** One command turns a MIDI into a practice video with
> sound, arranged to fit your hands. Rough edges and the things deliberately left
> out are listed as [M8 in the roadmap](docs/ROADMAP.md) — practice tempo and
> section looping are the two you will miss first.

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

Requires **Python 3.12+**. No system ffmpeg needed: the `render` extra brings its
own binary.

```bash
git clone https://github.com/RinkyDinkyNooble/piano-song-to-visual
cd piano-song-to-visual
pip install -e ".[all]"
```

`[all]` is what you want: `psv run` needs the renderer. A lighter install is
possible if you only care about the MIDI stages:

```bash
pip install -e .            # inspect, export, arrange, constrain
pip install -e ".[render]"  # + video and audio, which is everything below
```

Sound works with no further setup: the built-in synth needs nothing but numpy.
For a real sampled piano see [A real piano sound](#a-real-piano-sound) below.

## Usage

One command does everything:

```bash
psv run song.mid -o practice.mp4
```

```
  arrange          reduced to two hands, 12 note(s) dropped
  constrain        403 span violation(s) found, resolved in 3 pass(es)
    drop           50
    octave-shift   278
    reassign       12
    truncate       35
  audio            builtin
  notes            6066
wrote practice.mp4
```

That is a Beethoven string quartet becoming a piano piece you can reach every
chord of, with sound, in about four seconds.

### MusicXML as well as MIDI

Any command that takes a file takes either, told apart by content rather than by
extension:

```bash
psv run sonata.musicxml -o practice.mp4
psv run sonata.mxl      -o practice.mp4    # the zipped form
```

MusicXML is the better input where you have the choice, because it *states*
what MIDI leaves to be guessed. A piano score is written on two staves and the
file says which staff every note is on, so the hands are read rather than
inferred from track names. Dynamics arrive as `pp` and `ff` rather than as
velocity bytes, and pedalling as a mark with a start and a stop.

Repeats are not yet unrolled: a score with a repeat currently plays through
once. Everything else is in place.

Before committing to a file, it is worth asking what is actually in it:

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
  dynamics       none (every note the same velocity)
  pedal          none
  hands          not separated; needs the arrange stage
```

The last three lines are the useful ones: they tell you whether the file carries
the dynamics and pedal data the visuals depend on, and whether it needs arranging
at all.

### Presets

Named bundles, so a sensible result does not need a TOML file first:

```bash
psv run song.mid -o practice.mp4 --preset beginner
psv presets                                  # what each one actually sets
```

`small-hands` (a 9-semitone reach), `beginner` (small hands, thinner texture,
0.7x tempo, two bars of count-in), `as-written` (no span limit and nothing
thinned for difficulty), and `draft` (640x360, no audio, for iterating).

`as-written` still reduces a multi-instrument score to two hands, because two
hands is the premise. What it turns off is the span limit and difficulty
thinning.

Precedence runs least specific to most: the config file, then the preset, then
the individual flags. `--preset small-hands --span 14` gives you 14.

### Practising with it

A video of the piece at full speed, both hands, from the top, is not how anyone
learns a piece. Four flags cover how it is actually done:

```bash
psv run song.mid -o practice.mp4 --bars 20-40 --tempo 0.6 --hands left --count-in 2
```

- `--tempo 0.6` plays at six-tenths of the written speed, same notes. Work a hard
  passage up from something you can play cleanly.
- `--bars 20-40` renders only those bars, counting from 1 and including both
  ends. Bars are what you think in; `--start` and `--seconds` are still there
  when you want wall-clock time instead, and cannot be combined with `--bars`.
- `--hands left` sounds one hand. The other stays on screen, faintly, so you can
  still see where it is.
- `--count-in 2` puts two bars of clicks in front of the music, at the tempo and
  meter you are about to play. `--metronome` keeps clicking through the piece.

None of these touch the arrangement. They run after arrange and constrain, so
the piece you practise at half speed is note for note the piece you practise at
full speed. All four can also live in a config file:

```toml
[practice]
tempo = 0.75
hands = "both"       # both | left | right
count_in_bars = 2
metronome = false
```

A flag on the command line beats the file.

### Iterating quickly

A full 1080p60 render of a long piece takes minutes. While you are trying
settings, keep it small and short:

```bash
psv run song.mid -o preview.mp4 --start 30 --seconds 10 --width 640 --height 360 --fps 30
```

That costs about a second.

### Running one stage at a time

Each stage reads and writes MIDI, so you can stop after any of them, fix the file
by hand in any MIDI editor, and carry on:

```bash
psv arrange   song.mid      -o two-hands.mid
psv constrain two-hands.mid -o playable.mid    # add -vv to see every decision
psv render    playable.mid  -o practice.mp4
```

`psv export song.mid -o copy.mid` parses and writes straight back out, which is
how you check that ingest understood a file.

## Configuration

Everything meaningful is configurable via a TOML file rather than flags you have to
remember. The sketch:

The grid keys are named after what each line *marks*, not which way it runs. In a
falling-notes view the horizontal axis is pitch and the vertical axis is time, so
"horizontal lines" and "vertical lines" are easy to get backwards.

```toml
[hands]
max_span_semitones = 12   # hard limit on simultaneously held notes; 18 = 1.5 octaves
                          # never relaxed, at any difficulty.
                          # 0 means no limit: the piece exactly as written,
                          # which psv then says loudly rather than implying
                          # it checked something

[difficulty]
level = "medium"          # note density, ornamentation, harmonic detail

[visual]
background = "grayscale"
black_key_bar_width = 0.6 # relative to white-key bars, so black keys read from far away
black_key_darkening = 0.2 # applied on top of the note's colour

[visual.colors]           # hue = which hand, brightness = how loud
left_hand  = "#4a90d9"
right_hand = "#5fb87a"
unassigned = "#9aa0ac"    # before hand assignment has run
pedal      = "#c8a44a"
quiet = 0.35              # brightness at pp
loud  = 1.0               # brightness at ff

[visual]
note_border = 0.0016      # outline on each bar, as a fraction of frame width.
                          # This is what separates four fast repeats on one key
                          # from one long block. 0 turns it off

[visual.grid]
pitch_lines = "octave"    # vertical rules at every C, for finding a key
beat_lines  = "beat"      # horizontal rules on the beat, for spotting simultaneity
opacity     = 0.15        # faint: an aid, not decoration

[pedals]
lanes = 1                 # up to 3; sustain is the one MIDI reliably carries

[audio]
backend = "builtin"       # builtin | fluidsynth | mux | none
audio_file = ""           # for backend = "mux": your own recording
offset_s = 0.0            # nudge that recording into sync
stereo_width = 0.5        # low notes left, high notes right, as at the keyboard

[practice]               # how the finished arrangement is presented
tempo = 1.0              # 0.75 renders at three-quarters speed
hands = "both"           # both | left | right
count_in_bars = 0        # bars of clicks before the music
metronome = false        # keep clicking through it
```

### A real piano sound

The built-in synth is sine harmonics with an envelope: fine for keeping your
place, obviously synthetic. For a sampled instrument, point at a SoundFont:

```toml
[audio]
backend = "fluidsynth"
soundfont = "~/.local/fluidsynth/GeneralUser-GS.sf2"
fluidsynth_bin = "~/.local/fluidsynth/bin"      # folder holding the DLL
program = 0    # 0 grand, 1 bright, 4 Rhodes, 5 FM electric, 6 harpsichord
```

You need the [FluidSynth](https://github.com/FluidSynth/fluidsynth/releases)
binaries matching your Python's architecture, and any `.sf2` SoundFont
([GeneralUser GS](https://www.schristiancollins.com/generaluser.php) is a good
30 MB starting point). `fluidsynth_bin` exists so you do not have to put the
library on your `PATH`. If anything is missing, the render falls back to the
built-in synth and says which piece it could not find.

`psv instruments` lists what `audio.program` can select, reading the SoundFont's
own preset names where one is configured: General MIDI is a convention, and a
font may put anything at any number. [docs/SOUNDS.md](docs/SOUNDS.md) covers
where SoundFonts come from, why bigger is usually better, and how to audition
one in a couple of seconds.

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
