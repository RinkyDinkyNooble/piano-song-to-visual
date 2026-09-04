# Effects, visual and audio

Two different videos come out of this tool. One is a practice aid, where every
pixel that is not telling you which note to play next is in the way. The other is
the thing a pianist posts: sparks off the strike line, a glow on the keys, the
piece looking like an event. Both are legitimate and they want opposite things,
so effects are opt-in, off by default, and bundled into a preset for the people
who want the second one.

**Nothing here is built.** This is the plan, and it is arranged around the fact
that the risk in this feature is taste rather than engineering.

## The thing this plan is designed to avoid

Every effect is a small pure function. None of them is hard. What is hard is
knowing whether a glow radius of 12 looks good, and that cannot be settled by a
test, a type or a measurement. It is settled by looking.

So the order is: **look first, build second.** No effect gets a config key, a
validator, a test or a reference image until it has been seen in motion and
kept. Reference images in particular are the expensive thing to redo, so they
come last, only for what survived.

Three checkpoints, each cheaper to fail than the one after it.

## What the budget allows

Measured at 1920x1080. Drawing an entire frame costs about 8.5 ms today.

| | cost per frame |
| --- | --- |
| Full-frame Gaussian blur | 53 ms |
| Full-frame additive blend | 12 ms |
| Blur of a strike-line strip only | 7.7 ms |
| 400 extra shapes, drawn locally | 2.8 ms |
| 40 local glow regions, blended in place | 0.41 ms |
| 300 particle sprites | 0.19 ms |

Local drawing is about a hundred times cheaper than post-processing the finished
frame, and looks near enough the same for this. So every effect below draws into
the rectangles it needs and touches nothing else. **The budget is 3 ms of effects
per frame**, which roughly doubles a busy frame and is worth it for a mode you
turned on deliberately.

No new dependency. Pillow is already here and can blur, but its full-frame filter
is the 53 ms row. skia-python or OpenCV would make that about 18 ms, still worse
than drawing the whole frame, for a wheel of 30 MB or more. A GPU path breaks
determinism, has nothing to run on in CI, and fights the rule that this has to
stay easy to install.

## Two rules that rule out whole categories

**`render_frame` stays a pure function of the score and a time.** Nothing may
read the previous frame. That removes motion blur, accumulating trails, and
particle systems that carry state.

**Spans are rendered by separate processes.** Frame-to-frame state would break at
every span boundary and leave a visible seam every few seconds. This is a second
and unrelated reason for the same rule, which is a good sign it is the right one.

The way round it is to derive the effect from the score instead of from history.
A trail is "notes that crossed the line in the last 200 ms", which is a pure
function of time. A particle's position comes from seeding the generator with the
note index and the frame index, so it is deterministic, stateless, and identical
in every process.

## The catalogue

Ordered by what they are likely to be worth, with a guess at cost. All are
candidates, not commitments; checkpoint A is where most of them should die.

| Effect | What it does | Cost | Learning value |
| --- | --- | --- | --- |
| `strike_flash` | A burst at the line as a note lands, fading over ~120 ms | Low | Real. Confirms hit timing |
| `key_glow` | The pressed key lit beyond the current highlight | Low | Some |
| `trail` | A fading afterimage below the line for recently played notes | Low | None |
| `particles` | Sparks thrown from the strike point | Low | None |
| `halo` | A soft edge around each falling bar | Medium | Negative. Smears adjacent notes |
| `beat_pulse` | The background brightening slightly on the beat | Low | Some, for rhythm |
| `bloom` | Only the brightest pixels blurred and added back | High | Negative |

`bloom` is listed to be honest about it, and is the one most likely to be cut on
cost alone.

Every effect takes an `intensity` from 0 to 1 that scales it to nothing at zero.
That matters for the checkpoints: "right idea, too strong" must be a slider and
not a rewrite.

---

# The plan

## Step 1: a contact sheet

One throwaway script in the scratchpad. It renders **the same frame** of a real
piece, once per effect, at a couple of intensities, and writes them as PNGs into
one folder with readable names.

No config keys. No validation. No tests. No integration with the renderer beyond
calling it. Each effect is a function taking the frame and the score and drawing
into it, in one file, roughly 20 lines each.

Pick a frame worth judging: a dense chord with the pedal down, a note landing,
and something in both hands. Probably somewhere in the middle of Für Elise.

### Checkpoint A: still frames

You get a folder of PNGs and a note saying which is which. You say which effects
survive and which are gone.

**What a "no" costs at this point:** one script that was going to be deleted
anyway. Nothing else exists yet. This is the cheapest possible place to reject
seven of the eight.

## Step 2: the same effects in motion

Still frames lie about anything that moves. A flash that looks harsh frozen can
read as a tap in motion, and a trail that looks elegant frozen can smear.

So the survivors from A get rendered as **ten-second clips at 1280x720**, using
the parallel renderer, which makes each one about four seconds of waiting. Three
clips per effect at low, medium and high intensity, plus one clip with everything
that survived turned on together, since effects interact and a glow under
particles is not the sum of the two.

Sound on, because judging a strike flash without hearing the note is judging the
wrong thing.

### Checkpoint B: motion and intensity

You get a handful of short clips. You say: which effects stay, and roughly what
intensity each wants. Approximate is fine, the numbers get tuned later.

**What a "no" costs:** still just the throwaway script. There is still no config
schema, no validator, no test and no reference image. **This is the last point at
which rejecting an effect costs nothing**, and it is deliberately placed after
the only question that matters has been answered.

## Step 3: make it real

Only now, and only for what survived B.

- **Config.** An `[[visual.effects]]` list, each entry naming a `kind` and its
  parameters. The config module rejects unknown keys on purpose, so a list whose
  entries have different shapes per kind needs a schema per kind rather than one
  flat dataclass. This is the only genuine design work in the feature.
- **The effect functions**, moved from the throwaway script into
  `psv/render/effects.py`, each a pure function of the frame, the score and the
  time, drawing locally.
- **Ordering.** Effects compose in the order listed, so a halo under particles
  is a different picture from particles under a halo. The list is the order.
- **A preset**, bundling the survivors at the intensities from B. Name to be
  decided; `--preset showcase` or similar. This is the door for anyone who wants
  it to look good and does not want to read a config reference.

Cost is measured here, not assumed: a frame with the preset on, against a frame
without, at 1080p. If it is over 3 ms the intensity or the effect gets cut, and
the number goes in the docs either way.

## Step 4: tests, then the documents

- One reference image per effect, at low resolution, pinned the way the existing
  visual tests are. These come last because they are the thing that would have to
  be regenerated every time an effect changed, and by now nothing is changing.
- A test that the default config produces a frame identical to today's. **Effects
  off must mean literally unchanged**, not visually similar.
- A test that every effect at `intensity = 0` is a no-op, which is the property
  that makes the slider trustworthy.
- Determinism: the same frame twice is byte-identical, including particles.
- A parallel render with effects on matches a serial one, which is the seam check
  that the stateless rule exists to guarantee.
- Feature registry entries, README section, CHANGELOG.

### Checkpoint C: a whole piece

A full render of a real piece with the preset on, at your resolution, with sound.
Ten seconds tells you whether an effect works. Two and a half minutes tells you
whether it wears well, which is a different question and the one that decides
whether this is something you would actually post.

**What a "no" costs here:** tuning, not rebuilding. The structure holds; the
numbers change. That is why the intensity slider is in from step 1.

---

# Audio

One setting, and it is the only one worth having.

## Reverb, and nothing else

A dry sampled piano sounds like a sample player. Put it in a room and it sounds
like an instrument. That is nearly the whole gap between what comes out of this
tool and what a recording sounds like, and FluidSynth already carries the reverb
to close it.

```toml
[audio]
reverb = 0.35    # 0 is dry, 1 is a large hall
```

**One number, not four.** FluidSynth's reverb takes room size, damping, width and
level. Exposing all four means picking four numbers to find out that three of
them barely matter. A single amount drives all four along a curve chosen once by
ear, which is the difference between a setting you use and a setting you read
about and skip.

No new dependency, no DSP, and roughly an afternoon.

**Checkpoint:** three short renders of the same passage, dry, moderate and
generous. You say which is the default and whether the top of the range is far
enough. If it needs a second knob after hearing it, that is a finding, not a
failure.

## What the other backends get

Nothing, and they say so. `builtin` and `mux` do not go through FluidSynth, so
selecting a reverb with either of them logs that it was ignored rather than
implying it happened.

A Freeverb network in numpy would give `builtin` a reverb for about sixty lines,
and it is deliberately not planned. It is the most complicated thing in this
document and it would improve a synth that exists so the video is not silent when
FluidSynth is missing. If the built-in tone matters enough to want reverb on it,
the fix is a better tone, which is a separate item on the roadmap.

## Deliberately not planned

Compression, EQ, stereo widening beyond the register pan that already exists,
limiting, tape saturation, and anything else with a threshold and a ratio.

Each is a knob that makes a recording sound more produced and a practice aid no
more useful, and each is done better by software built for it. The `mux` backend
already takes a finished audio file back, so the whole professional route stays
open: render silent, treat the audio wherever you like, mux it in. That is one
extra command for the rare case, against a permanent pile of settings for the
common one.

# What could go wrong

**Everything gets rejected at checkpoint A.** Fine, and cheap: that is a deleted
script and a day. It is also a real answer, that this tool is a practice aid and
the spectacle belongs elsewhere.

**The effects look good alone and bad together.** Which is why B includes one
clip with all of them on rather than only the individual ones.

**Particles look wrong deterministically.** Seeding from the note index and frame
index gives repeatable randomness, and repeatable randomness can look patterned
rather than random. If it does, the fix is a better hash rather than real state,
because state is what the two rules forbid.

**It costs more than 3 ms.** Then it is cut or turned down, and the measurement
is in the docs so nobody has to rediscover it.
