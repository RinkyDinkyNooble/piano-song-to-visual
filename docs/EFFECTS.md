# Effects, visual and audio

Two different videos come out of this tool. One is a practice aid, where every
pixel that is not telling you which note to play next is in the way. The other is
the thing a pianist posts: sparks off the strike line, a glow on the keys, the
piece looking like an event. Both are legitimate and they want opposite things,
so none of this is on by default.

**The theme layer is built. None of the effects are.** This document is both
the plan and the record of it, and it is arranged around the fact that the risk
in this feature is taste rather than engineering.

## Two layers, not one

This started as one list of effects and it should not have been. What makes a
render look striking splits cleanly in two, and the halves want different
treatment:

**The theme.** Background, note colours, bar borders, gradients. Static. It is
the same in every frame, it can be judged from a single picture, and most of it
is already configurable. There is no motion question to answer, so it does not
need the motion checkpoint.

**The effects.** Flashes, glows, trails, sparks. Transient, tied to when a note
lands, and impossible to judge frozen. These are the ones that need to be seen
moving before anything about them becomes real.

Splitting them matters because the theme is where most of the "make it look
good" actually lives, it costs almost nothing, and it should not sit behind
seven taste judgements about particles.

## The thing this plan is designed to avoid

Every effect is a small pure function. None of them is hard. What is hard is
knowing whether a glow radius of 12 looks good, and that cannot be settled by a
test, a type or a measurement. It is settled by looking.

So the order is: **look first, build second.** No effect gets a config key, a
validator, a test or a reference image until it has been seen and kept.
Reference images in particular are the expensive thing to redo, so they come
last, only for what survived.

## What the budget allows

Measured at 1920x1080 on the machine this is developed on. Drawing an entire
frame costs 8.5 ms today.

| | cost per frame |
| --- | --- |
| Filling the background, flat | 5.4 ms |
| Filling the background, vertical gradient | 5.4 ms |
| Bloom, full resolution | 162 ms |
| Bloom, at 1/8 resolution | 26.6 ms |
| Full-frame additive blend | 12 ms |
| 40 local glow regions, blended in place | 0.41 ms |
| A vertical gradient down every visible bar | 0.69 ms |
| 300 particle sprites | 0.19 ms |

Two of those are worth reading twice.

**The background fill is already two thirds of a frame**, and putting a gradient
there costs nothing measurable over the flat colour it replaces. It is the same
write to the same pixels with a different source. This is the cheapest good
thing available.

**Bloom has a floor, and it is not the blur.** Doing it at an eighth of the
resolution takes it from 162 ms to 26.6 ms, but no further: past that point the
cost is the full-frame composite, which is the 12 ms row, and no amount of
shrinking the blur touches it. Bloom is the one candidate that cannot be made
local, because being global is what it is.

**The budget is 3 ms of effects per frame for the shipped preset**, with about
10 ms as the point where it stops being worth it. Ten milliseconds roughly
doubles a frame, which takes Fur Elise from 44 seconds to about 1:40 and is a
fine trade for a mode you turned on deliberately. Bloom at 26.6 ms triples it,
and that is a different conversation.

No new dependency. Pillow is already here, and skia-python or OpenCV would buy
about 18 ms on the one operation that is over budget anyway, for a wheel of 30
MB or more. A GPU path breaks determinism, has nothing to run on in CI, and
fights the rule that this has to stay easy to install.

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

---

# The theme

Half of it already existed and was not documented anywhere anyone would look.
The other half is step 2 below, and is built.

| | before | now |
| --- | --- | --- |
| Background | `visual.background`, grayscale only | `gradient_top` / `gradient_bottom`, any hue |
| Hand colours | `visual.colors.left_hand` / `right_hand`, any hex | unchanged, this already worked |
| Border width | `visual.note_border`, a fraction of frame width | unchanged, this already worked |
| Border colour | fixed: 45% darker, same hue | `note_border_shade`, -1 to +1 |
| Bar fill | flat | `bar_gradient`, -1 to +1 |
| Whole schemes | none | `--theme`, four of them |

`left_hand = "#ff2d95"` already worked before any of this. Two gaps were real.

## The background, and the rule it has to get past

`visual.background` is validated as grayscale on purpose. Any hue back there
competes with the hues that carry which-hand information, which is the whole
readability argument from M4 and is still right for a practice video.

The escape is a separate key rather than a relaxed rule:

```toml
[visual]
gradient_top = "#140e28"
gradient_bottom = "#46205a"
```

Unset by default, in which case `background` behaves exactly as it does now.
Set, they override `background` and accept any colour, because setting them is
itself the opt-in. That keeps the practice default honest without a cross-key
validation rule that says "colour is allowed if some other thing is on", which
is the kind of condition nobody can predict from reading the config.

Two keys rather than the single `gradient = [top, bottom]` this planned. The
config loader coerces scalars and passes anything else through, so a list would
be the first value in the file whose declared type is not what arrives, and it
would need a normalising step and a new class of validation error for the sake
of one line. Two plain strings need neither. "Set both or neither" is the only
rule, and the error says so.

Whether it drifts is one more number. Motion has to be a pure function of time,
so the phase is `time * speed` and nothing accumulates. Both rules are satisfied
by construction, and the cost is still the same fill.

## The bar borders

One knob, in the shape the audio section settled on:

```toml
[visual]
note_border_shade = -0.45   # -1 black, 0 the bar's own colour, +1 white
```

`-0.45` is exactly what is drawn today, so the default changes nothing. Positive
values give the light outline that makes a bar look lit from inside rather than
cut out of the background, which is what most of the striking renders you have
seen are actually doing.

A literal border colour was considered and is not the first thing to build. The
border is drawn inside the bar and eats a few pixels of it, so at speed a short
note can be mostly border; keeping it a shade of the bar's own hue is what makes
that survivable, because which hand is playing is still readable at the edge. If
the shade knob turns out not to be enough once you have seen it, an explicit
colour is a small addition on top and can be added then.

## Bar gradients

A vertical ramp down each bar instead of a flat fill, at 0.69 ms for a busy
frame. Cheap enough to be a theme setting rather than an effect, and it makes
the falling bars read as objects with a light on them.

---

# The effects

## Checkpoint A, which has happened

Seven candidates were drawn into one frame of Fur Elise at two intensities and
looked at. **Nothing was cut.** That is not the outcome the plan expected, and
the plan said seven of the eight should die here, so it is worth writing down
what actually came back:

- All seven read as plausible at some intensity, and the interesting question
  turned out to be combinations rather than survival.
- **`trail` and `key_glow` are the same picture in a still.** Every note struck
  in the last 0.4 seconds was still held, so the trail sat on a key that was
  already lit in the same colour. Their difference is entirely a motion
  question.
- **`beat_pulse` cannot be judged from a still at all.** The chosen frame sat
  between beats, so the honest still was identical to the one with the effect
  off, and it had to be drawn at the peak of its pulse to be visible.
- **`bloom` blows out the white keys**, because they are the brightest thing on
  screen and bloom finds the brightest thing on screen.

The costs measured at 1080p, at full intensity:

| Effect | cost | verdict |
| --- | --- | --- |
| `particles` | 1.1 ms | kept |
| `trail` | 1.4 ms | kept, but see `key_glow` |
| `strike_flash` | 1.5 ms | kept |
| `key_glow` | 5.0 ms | kept |
| `halo` | 8.1 ms | kept, and the most expensive local one |
| `pulse` | not measurable yet | reworked, see below |
| `bloom` | 162 ms, or 26.6 ms done small | on probation |

## The catalogue as it now stands

| Effect | What it does | Learning value |
| --- | --- | --- |
| `strike_flash` | A burst at the line as a note lands, fading over ~130 ms | Real. Confirms hit timing |
| `key_glow` | The pressed key lit beyond the current highlight | Some |
| `trail` | A fading streak down the key for recently played notes | None |
| `particles` | Sparks thrown from the strike point | None |
| `halo` | A soft edge around each falling bar | Negative. Smears adjacent notes |
| `pulse` | The background lifting when notes land | Some, for rhythm |
| `bloom` | Only the brightest pixels blurred and added back | Negative |

Every effect takes an `intensity` from 0 to 1 that scales it to nothing at zero.
That matters for the checkpoints: "right idea, too strong" must be a slider and
not a rewrite.

They compose in the order they are listed, so a halo under particles is a
different picture from particles under a halo. The list is the order, and mixing
them is the point rather than a concession.

### `pulse`, reworked before you judge it

The version in the contact sheet brightened the background on every beat of the
tempo map. That is a metronome you can see: it fires whether or not anything is
played, and it is indifferent to how hard.

The version worth judging is driven by the music instead. It lifts on note
onsets, scaled by velocity and by how many landed at once, and decays over a few
hundred milliseconds. Same cost, same purity, and it is a pure function of time
because "which notes started in the last 300 ms" is a query against the score.

The metronome version is not offered as an option. If you want to see the beat,
the grid already draws it and does so without moving.

### `bloom`, and what it would take

Its problem was never the intensity. At full resolution it costs 162 ms a frame,
which would take Fur Elise from 44 seconds to about half an hour. Done at an
eighth of the resolution it costs 26.6 ms, still triple a whole frame, and it
cannot go lower because the floor is the full-frame composite rather than the
blur.

So the decision is a straight trade with no cleverness left in it: about 3x the
render time for a look that nothing local reproduces. It stays in as a candidate
that has to be turned on by name, is never in a preset, and says what it costs
when you select it. If the motion checkpoint does not make a case for it, it
goes.

The cheap approximation of bloom is `halo` plus `strike_flash`, at 9.6 ms
together, and it may well be that seeing those two in motion is what kills
bloom.

---

# The plan

## Step 1: the contact sheet (done)

One throwaway script, one frame, each effect at two intensities. Written,
looked at, and its findings are the Checkpoint A section above.

## Step 2: the theme, built for real (done)

The theme layer has no motion question, so it did not wait.

- `visual.gradient_top` and `visual.gradient_bottom`, unset by default,
  replacing `background` when set and free to have a hue.
- `visual.note_border_shade`, defaulting to the -0.45 that was drawn before.
- `visual.bar_gradient`, defaulting to 0, the flat fill.
- `--theme`, with `midnight`, `ember`, `neon` and `aurora`. A separate flag from
  `--preset` rather than more entries in it: a preset changes how the piece is
  played and a theme only how it looks, and one list holding both would make
  "a 9-semitone reach" and "deep blue" look like the same kind of thing.

Three things came out of building it that the plan did not have.

**The grid had to learn about the background.** It was mixed with a nominal
background colour once and drawn as a flat line, which on a gradient would have
meant a grid that vanishes into the dark end. It is now mixed a row at a time.

**Compositing the grid instead was wrong, and the reference images caught it.**
Blending each line over whatever was underneath is the obvious way to do that,
and it blends twice where a beat line crosses a pitch line, leaving a brighter
dot at every intersection. Six committed frames failed on 40 pixels each. The
grid is worked out per row and then drawn, not composited, and there is now a
test naming the intersections.

**The keyboard is not themed.** It is still white keys and black keys under a
neon gradient, which looks deliberate on some themes and unfinished on others.
Left alone for now: the keys are a picture of a real keyboard, and it is not
obvious that tinting them helps. Worth revisiting after seeing a theme move.

### Checkpoint B: theme stills (passed)

Four strips on the same frame of Fur Elise: the themes side by side, then each
knob swept on its own so it could be judged apart from the theme wrapped round
it.

Stills were sufficient, which is the point of the split. Nothing in this layer
changes between frames, so there was nothing a video would have said that a
picture did not.

**All four themes kept, unchanged.** The condition attached was that everything
a theme sets has to be settable by hand, which is now a test: each theme is
written out as a TOML file, loaded, and compared against what `--theme` produces.
A theme is a shortcut, not a capability. If one could reach something the config
could not, those four schemes would quietly become the whole palette anyone
gets.

**The keyboard stays as it is**, decided rather than deferred. It is a picture of
a real instrument, white keys and black keys, and it reads as itself under every
theme. Tinting it is one more thing to get right per theme for no gain in either
looks or clarity.

What is not configurable, and is staying that way: the non-note palette, meaning
the key colours, the strike line, the lane fills and the grid's own grey. Those
are structure rather than decoration. The grid's opacity is a setting; its hue
is not, for the same reason the flat background has to be grayscale.

## Step 3: the effects in motion

Still frames lie about anything that moves. A flash that looks harsh frozen can
read as a tap in motion, and a trail that looks elegant frozen can smear. Two of
the seven could not be judged from stills at all.

The survivors get rendered as **ten-second clips at 1280x720**, using the
parallel renderer, which makes each one about four seconds of waiting. Three
clips per effect at low, medium and high intensity, plus clips with combinations
turned on together, since effects interact and a glow under particles is not the
sum of the two.

Sound on, because judging a strike flash without hearing the note is judging the
wrong thing.

`trail` against `key_glow` gets its own pair of clips, since stills could not
separate them. `pulse` and `bloom` are each answering a specific question:
whether the reworked pulse tracks the music, and whether bloom is worth 3x.

### Checkpoint C: motion and intensity

You say which effects stay and roughly what intensity each wants. Approximate is
fine, the numbers get tuned later.

**What a "no" costs:** still just the throwaway script. There is still no effect
config schema, no validator, no test and no reference image. **This is the last
point at which rejecting an effect costs nothing**, and it is deliberately
placed after the only question that matters has been answered.

## Step 4: the effects, built for real

Only for what survived C.

- **Config.** An `[[visual.effects]]` list, each entry naming a `kind` and its
  parameters. The config module rejects unknown keys on purpose, so a list whose
  entries have different shapes per kind needs a schema per kind rather than one
  flat dataclass. This is the only genuine design work in the feature.
- **The effect functions**, moved from the throwaway script into
  `psv/render/effects.py`, each a pure function of the frame, the score and the
  time, drawing locally.
- **Presets** bundling a theme and a set of effects at the intensities from C.
  This is the door for anyone who wants it to look good without reading a config
  reference.

Cost is measured here, not assumed: a frame with each preset on, against a frame
without, at 1080p. The number goes in the docs whatever it turns out to be.

## Step 5: tests, then the documents

- One reference image per effect and per theme setting, at low resolution,
  pinned the way the existing visual tests are. These come last because they are
  the thing that would have to be regenerated every time something changed, and
  by now nothing is changing.
- A test that the default config produces a frame identical to today's.
  **Defaults must mean literally unchanged**, not visually similar. This covers
  the theme too: `note_border_shade` defaulting to -0.45 has to produce the same
  pixels as the constant it replaces.
- A test that every effect at `intensity = 0` is a no-op, which is the property
  that makes the slider trustworthy.
- Determinism: the same frame twice is byte-identical, including particles.
- A parallel render with effects on matches a serial one, which is the seam check
  that the stateless rule exists to guarantee.
- Feature registry entries, README section, CHANGELOG.

### Checkpoint D: a whole piece

A full render of a real piece with a preset on, at your resolution, with sound.
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

**Everything survives every checkpoint.** This is now the likelier failure, not
the original worry that nothing would. Seven effects and a theme layer all kept
is a large surface, and the answer is the preset: the full set exists for
whoever wants to tune it, and the preset is the opinion about which four of them
belong together.

**The effects look good alone and bad together.** Which is why the motion
checkpoint includes combinations rather than only the individual ones.

**Particles look wrong deterministically.** Seeding from the note index and frame
index gives repeatable randomness, and repeatable randomness can look patterned
rather than random. It already did once: without a per-spark birth delay, every
spark of a note was the same age and the spray drew as a clean arc. The fix was
a better hash rather than real state, because state is what the two rules forbid.

**A coloured background makes the piece harder to read.** It will. That is the
trade, it is opt-in, and the practice default does not move.
