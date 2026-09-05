# The constraint engine

This is the part of the project that does not exist anywhere else, and the part
worth understanding rather than trusting. It is written to be read start to finish.

## The problem

You can comfortably reach an octave and stretch to about an octave and a half. A piece
written for an organ, an orchestra, or a pianist with larger hands will regularly ask
for more. Standard falling-note videos show you that chord anyway and leave you to work
out what to do about it.

The engine's job is to rewrite the arrangement so that never happens, and to do it
without wrecking the music.

**The promise:** at every instant, the set of notes one hand is holding fits inside
`hands.max_span_semitones`. Not usually. Not except in hard passages. Every instant,
every input.

## What counts as "held together"

Two notes are held together if they sound at the same time for longer than
`hands.overlap_tolerance_s`, which defaults to 30 ms.

That tolerance matters more than it sounds. MIDI is full of notes released a few
milliseconds after the next one starts, especially from a real performance or a sloppy
export. Treating those as chords would have the engine rewriting passages that are
perfectly comfortable to play. The rule is: if you would not notice holding them
together, it is not a stretch.

Sequential leaps are never a problem, however wide. You can jump three octaves; you just
cannot hold three octaves.

## Finding the violations

A sweep line over note boundaries in time order, keeping each hand's sounding pitches
sorted. Two observations keep it simple:

**Only note starts can create a violation.** Ending a note can never widen the set that
remains, so ends just update state and move on.

**The tolerance is applied by ending every note early by that amount.** A brush overlap
then disappears from the sweep entirely, instead of needing a special case at every
comparison.

One subtlety was a real bug during development, and it is worth stating because it is
easy to get wrong. The sweep must **settle the entire instant before judging it**. If
you evaluate after each individual note start, a three-note chord gets judged when only
two of its notes are down: the reported extremes are wrong, and the other hand may not
have been added yet. That second part matters because the cheapest repair is usually to
move a note across, and deciding that needs to know what the other hand is already
holding. So the sweep processes every event sharing a timestamp, then asks the question
once per hand.

```
for each instant:
    apply every note-end at this instant
    apply every note-start at this instant
    for each hand that gained a note:
        if highest - lowest > max_span: record a violation
```

`detect_violations` returns one violation per (instant, hand), carrying the extremes and
a snapshot of what **both** hands were holding.

## Choosing what to move

A violation has a low end and a high end. Moving either narrows the set, so the engine
takes whichever removal narrows it more.

When both are equally effective, it gives up whichever the music will miss least, using
`Salience`: the top and bottom of a chord get a large bonus because they are the melody
and the bass, and what sits between them is harmony, which a listener misses least. A
note that is both the top of the chord and part of a stepwise line gets more again,
because that is the tune, and it is short notes in a fast passage that a length-based
score gets wrong.

Length counts from the moment of the choice rather than from the note's start. How much
of a note is still to come is what dropping it costs; how long it was written is not.

Harmonic analysis was tried here and removed. Scoring chord tones above passing tones
made the arrangement measurably worse, because a passing note in the melody matters more
than a chord tone in an inner voice and a pitch-class histogram cannot tell them apart.
The reasoning is kept in the module docstring so it is not rebuilt.

## The five repairs

Tried in order of what they cost the music. The first one that applies wins.

### 1. Reassign to the other hand

Move the outlier across. **Nothing sounds different at all**, so this is always tried
first, and on real music it is the most common repair by a wide margin.

Refused when the other hand could not reach the note either, since that would only
relocate the problem. A note is only ever reassigned once, which stops it bouncing
between hands forever.

### 2. Truncate, while the sustain pedal is down

Shorten the note that is being held into the stretch.

This is the one genuinely interesting idea in the engine. **While the sustain pedal is
down, the damper is off the string and the note keeps ringing whether or not your finger
stays on the key.** Lifting early is literally inaudible. So when CC64 is held, this
repair costs nothing, and it jumps ahead of moving the octave.

The note keeps its pitch, its register, and its sound. Only the key release moves, and
nobody hears it.

This is the entire reason the engine reads pedal data. It is also why the parser records
pedal depth rather than a boolean.

Refused when the two notes are **struck together**: shortening one cannot separate notes
that start at the same instant, because the stretch exists the moment both are down.

### 3. Octave shift

Move the outlier a whole octave toward the rest.

Whole octaves only. That is the one displacement preserving pitch class and harmonic
function: a C stays a C, and the chord still means what it meant. Any other interval
changes the harmony.

Refused when the note would leave the 88 keys, when it would land on a pitch the same
hand is already holding (two voices silently merging into one), or when it would not
strictly narrow the span. That last condition is what prevents a note oscillating up and
down between two conflicts; a hard cap of three shifts per note backs it up.

### 4. Truncate, with the pedal up

The same edit as (2), except now the shorter note is genuinely audible. So it ranks
below moving the octave rather than above it.

### 5. Drop

Remove the less salient of the two extremes.

Always available, and that is the point: **because dropping can never fail, the loop is
guaranteed to terminate**. It is also always logged, and it should stay rare. On BWV 565
at a 12-semitone limit, dropping accounts for about 5% of repairs.

## The loop, and why it stops

```
for pass in 1..12:
    violations = detect(...)
    if none: done
    repair each violation
    compact
force_clean()          # drop-only, until nothing violates
verify_span()          # the postcondition, checked every run
```

Repairs are applied in a pass, then everything is re-detected, because a repair can move
a note into a fresh conflict somewhere else. In practice dense organ writing settles in
three to six passes.

**Termination is provable, not hoped for.** After the bounded passes, `force_clean`
drops notes until nothing violates. Dropping strictly reduces the note count, the note
count is finite and non-negative, and a hand holding fewer than two notes cannot violate
anything. So the process cannot run forever, and it cannot end in a violating state.

**The postcondition is checked on every call, not just under test.** `verify_span` runs
before `constrain` returns; if it finds anything, the engine raises rather than handing
back a score that quietly cannot be played. A failure there is a bug in this module, and
it says so.

## Difficulty is a different knob

The spec is explicit that a harder setting should mean more notes and faster passages,
never a wider stretch. That separation is structural rather than a promise:

- Difficulty runs **before** span enforcement, so span always gets the last word.
- Difficulty **only ever removes notes**. There is no code path in it that edits pitch,
  timing, or hand, so it has no mechanism by which it could widen a reach.

What it does: caps how many notes one hand holds at once (2 for beginner up to 5 for
hard, uncapped for original), and at the easiest levels strips ornaments, meaning very
short notes sounding underneath something longer. Never the last voice, since that would
leave a hole rather than a simpler piece, and never a note carrying the melodic line: a
run is short notes over long accompaniment, so a plain length threshold keeps the
accompaniment and deletes the tune. Outer voices score high in salience, so the melody
and bass survive and the harmony between them gives way.

## Provenance: how to check the engine's work

Every note carries what was done to it: `ORIGINAL`, `REASSIGNED`, `OCTAVE_SHIFTED`,
`TRUNCATED`. Every edit also produces a `Repair` record naming the strategy, the hand,
the time, and the note before and after.

```bash
psv constrain song.mid -o playable.mid -vv    # every individual repair
```

This is what makes the engine auditable instead of a black box. If a passage comes out
wrong, you can find out exactly which decision produced it rather than guessing.

## How it is tested

The guarantee is a claim about **all** inputs, so examples cannot establish it. Hypothesis
generates random scores across random span limits, and asserts `verify_span` comes back
empty. Alongside it:

| Property | What it protects |
| --- | --- |
| Output never exceeds the span | The promise itself |
| Output stays on the 88 keys | Repairs cannot invent keys |
| Repairs never invent notes | The engine may move, shorten, or remove, never add |
| A conforming score is returned untouched | It cannot make a playable arrangement worse |
| Constraining twice equals once | It cannot degrade a score on every run |

Plus one test per strategy, including the cases where each correctly declines, and
end-to-end runs against BWV 565 and the Beethoven quartet at three different span limits.

## What it is not

It does not make the music good. It makes it **reachable**.

Hand assignment is currently a placeholder that splits at the piece's median pitch, which
is not good hand assignment: it will put a left-hand melody in the right hand wherever
the voices cross. The arrange stage (M6) replaces it. Nothing in the engine depends on
how hands were chosen, only that they exist.

Salience is crude, difficulty is four numbers, and a human arranger would make better
choices throughout. The engine's contribution is narrower than that and worth stating
plainly: **whatever else is true of the output, you can reach every chord in it.**
