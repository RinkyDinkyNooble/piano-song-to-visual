# M9: the composer

> **Not being built.** This plan is kept as a record of the reasoning, not as
> work in progress. M10 made it unnecessary: MusicXML states the hands, the
> dynamics and the pedalling, and public-domain sheet music covers the
> repertoire this tool is aimed at. Transcription quality would have been a
> ceiling on everything downstream. See [ROADMAP.md](ROADMAP.md).

Point it at a recording and get back a piano arrangement you can learn.

```
audio ──▶ transcribe ──▶ MIDI ──▶ arrange ──▶ constrain ──▶ render ──▶ video
          ^^^^^^^^^^             └──────── all of this is built ────────┘
          the only missing stage
```

Everything from `arrange` rightwards exists, is tested, and is green on three
operating systems. This document plans the one stage that does not.

## What decides the design

**Ease of installation, above transcription quality.** A tool nobody can install
transcribes nothing. That constraint is not a preference here; it eliminates the
obvious approach outright, and the numbers below are measured on this machine
rather than assumed.

Python 3.14, Windows, from the project's own virtualenv:

| Candidate | Result |
| --- | --- |
| `onnxruntime` | Resolves to **6 wheels**, all binary, no build step |
| `basic-pitch` (the package) | **No wheel.** Falls back to an sdist whose build backend fails outright |
| `torch` | Wheel exists, but CPU-only is ~200 MB and the CUDA build is over 2 GB |

So the tool depends on **ONNX Runtime, not on any model author's package**. Model
authors ship research code with pinned TensorFlow or PyTorch and a narrow Python
range; their exported `.onnx` weights carry none of that. Taking the weights and
leaving the package is what makes this installable.

Second measured decision: **the audio front end is ffmpeg, which ships already.**
`imageio-ffmpeg` is a hard dependency for video output, and the same binary
decodes any container to exactly the array a model wants:

```
decoded mp3 -> 110250 samples = 5.0 s, peak 0.847
```

That removes `librosa` and `soundfile` from the plan. `librosa` alone pulls
numba and llvmlite, which are among the most install-hostile packages in the
scientific Python set.

**Net new dependency for the whole feature: `onnxruntime`.** Nothing else.

## Where it lives

In this repository, as an optional extra:

```bash
pip install "piano-song-to-visual[transcribe]"
psv run song.mp3 -o practice.mp4
```

This reverses the earlier sketch, which put transcription in a separate
`psv-transcribe` package. The reasoning has changed because ease of use is the
governing constraint: a separate package means two installs, two version
numbers, and a manual hand-off of an intermediate file. One extra means
`psv run` takes an mp3 and does the rest.

What the separate package was protecting is still protected, by the extra rather
than by the split. Someone who already has MIDI installs nothing new, `psv`
keeps installing in seconds, and the ONNX import stays inside the transcribe
backend so importing `psv` never touches it.

## Model weights

Weights are tens of megabytes and are not this project's to redistribute, so
they are not in git. They are fetched on first use and verified, which is
exactly what `scripts/fetch_test_songs.py` already does for the CC BY-SA test
songs: a manifest carrying name, URL, SHA-256, size, and licence, and a
downloader that rejects anything whose hash does not match.

```bash
psv models              # what exists, what is cached, what it would cost
psv models get basic-pitch
```

Cached under the platform's data directory, so it survives a reinstall and is
shared between virtualenvs. **Never downloaded silently.** A first `psv
transcribe` with nothing cached prints the size and the licence and asks, or
takes `--yes`. Downloading 80 MB because someone ran a command is the kind of
surprise this tool should not spring.

## Stages

Each ends with something runnable, and the uncertain work is deliberately late.

### Stage 1 — the seam, with no model at all

- `psv/transcribe/audio.py`: decode any container to mono float32 at a requested
  sample rate, through the bundled ffmpeg. Audio files are untrusted input, so
  this follows the rules the rest of the tool already follows: argument lists
  never shell strings, a duration cap, and a clear error rather than an ffmpeg
  stack dump.
- `psv transcribe song.mp3 -o song.mid`, and `psv run` recognising an audio
  input and routing through it.
- A `null` backend that returns an empty score. Not a placeholder for its own
  sake: it makes the wiring testable, and CI can exercise the whole path with no
  model and no network.

**Exit:** `psv transcribe` runs end to end on a machine with no ML dependency
installed, and says clearly that no backend is available.

### Stage 2 — the model registry

- `psv/transcribe/models.py` and a `models.toml` manifest.
- `psv models` to list, fetch, and verify. Corrupt or truncated downloads are
  rejected on hash, not trusted because the file exists.

**Exit:** a deliberately corrupted cache file is detected and re-fetched rather
than loaded.

### Stage 3 — a spike before the first real backend

**The one genuine unknown: what `nmp.onnx` expects as input.** If the CQT and
harmonic stacking live inside the exported graph, the backend is a small
adapter. If they live in the Python that was left behind, they have to be
reimplemented against a model that cannot say whether the reimplementation is
right, which is a different and much larger job.

So the first move is to fetch the weights, load them with
`onnxruntime.InferenceSession`, and print the input and output signatures.
Half an hour, and it decides whether Stage 4 is small or large. **Do not plan
past this point until it is answered** — the estimate on the other side of it is
worthless either way.

If the answer is bad, the fallback is not to reimplement blind. It is to look at
what else exports cleanly, and to weigh a heavier but honest dependency against
a lighter one whose preprocessing we cannot verify.

### Stage 4 — posteriorgrams to a Score

Onset, frame, and contour outputs become `Note` objects: threshold onsets, track
each note until its frame probability drops, take velocity from onset strength.
Thresholds are config, because they are the only quality knob a user has.

The output is an ordinary `Score`, so `arrange`, `constrain`, and the whole
practice layer take it from there unchanged. That is the point of the seam.

### Stage 5 — piano, and the pedal

The ByteDance high-resolution model has the best onsets and offsets by a
distance and is the only one that recovers the **sustain pedal**, which this
tool uses in three separate places: the pedal lanes, the built-in synth's ring,
and the constraint engine's judgement that truncating under the pedal is free.

It ships as PyTorch, so it needs exporting to ONNX and hosting somewhere stable.
That is real work and it is last on purpose.

## Testing, and why this project can do it properly

Transcription is usually hard to test because ground truth is expensive. Here it
is free, because the tool already contains a synthesiser:

```
known MIDI ──▶ FluidSynth ──▶ audio ──▶ transcribe ──▶ MIDI ──▶ compare
     └────────────────── ground truth ──────────────────────────┘
```

Render a committed fixture to audio, transcribe it, and score the result against
the notes that produced it. Precision, recall, and F1 at a 50 ms onset
tolerance, which is the standard measure and about forty lines to implement
directly rather than adding `mir_eval` for it.

That gives a real red/green signal on a change to the post-processing, which is
otherwise the least testable code in the project. It is not a substitute for
listening to a real recording: synthesised audio is cleaner than anything real,
so these numbers will flatter the model. They are a regression test, not a
benchmark, and the docs should say so.

CI installs the extra on one job rather than all nine, and every test needing
weights skips when the cache is empty, the way the CC BY-SA song tests already
skip when they have not been fetched.

## The honest limit

**Transcription quality is the ceiling on the whole thing, and no amount of
downstream cleverness raises it.** A clean solo piano recording transcribes very
well. A dense orchestral mix, or anything with drums and distorted guitars, does
not: you get approximately the right notes, plus spurious ones, minus quiet
ones.

What comes out is a starting point to correct by hand, not a finished score.
Every stage still reads and writes MIDI, so correcting it by hand in any editor
and picking up from `arrange` is the intended workflow rather than a
consolation.

This is exactly why MIDI stays the main tool's input, why transcription is its
own command rather than being folded silently into `run`, and why the report it
prints should say how confident it is rather than presenting a guess as a
result.
