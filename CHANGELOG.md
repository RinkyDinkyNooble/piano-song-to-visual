# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


### Added

- **Rounded bar ends**, `visual.note_radius`, as a fraction of the bar's own
  width rather than of the frame: the right radius is set by how wide the bar
  is, a black-key bar is narrower and wants less, and half a bar is the most
  that can be rounded off either end. 0 is the square corner this always drew
  and is byte-identical to it.

- **A count-in that keeps its time and drops its clicks.** `--silent-count-in`,
  or `count_in_clicks = false`. The lead-in does two jobs, giving you time to
  get your hands ready and counting the beat, and the falling notes already do
  the second one. Two settings because they are two decisions.

- **Optional visual effects, off by default.** Seven of them, composed in the
  order listed, each with an `intensity` from 0 to 1 where 0 is a no-op rather
  than something faint. `strike_flash`, `key_glow`, `trail`, `particles`,
  `halo`, `pulse` and `bloom`.
  - `[[visual.effects]]` in the config, or `--effects` for a named bundle:
    `subtle`, `showcase`, `maximum`, and `none` to turn off what a config file
    asked for.
  - Measured at 1080p, against the 8.5 ms a frame already costs: `subtle` 1.3
    ms, `showcase` 2.3 ms, `maximum` 8.5 ms. `bloom` alone is 26.8 ms, about
    three times a whole frame, so it is in no bundle and has to be named.
  - Nothing reads the previous frame, so `render_frame` stays a pure function
    of the score and a time. Sparks are hashed from the note and the spark
    index rather than carried between frames, which is also why a frame drawn
    in a worker process is byte-identical to one drawn in the parent. Tested
    across a real spawned process, because that is the seam this rule exists
    to protect.
  - `pulse` is the one that does not draw. It changes the colour the background
    is about to be filled with, which is why it costs 0.03 ms. It is driven by
    note onsets and velocity, not by the tempo map: pulsing on the beat is a
    metronome you can see, and the grid already draws the beat without moving.
  - Every distance is a fraction of the frame rather than a pixel count, so an
    effect is the same effect at 720p and at 1080p.
  - Nothing got a config key until it had been seen in motion and kept.

- **Reverb, as one number.** `audio.reverb` from 0 to 1, or `--reverb`, driving
  FluidSynth's room size, damping, width and level together. Exposing all four
  means picking four numbers to find out that three of them barely matter.
  - `0.5` is the default and changes nothing: FluidSynth enables its own reverb
    unless told not to, so psv has never been dry, and the middle of the range
    is exactly the numbers that were already in use.
  - `0` switches the reverb off outright rather than mixing it in at zero. What
    is still ringing half a second after a short note goes from the noise floor
    at 0 to 35 times that at 1.
  - `builtin` and `mux` do not go through FluidSynth, so choosing a reverb with
    either of them says it was ignored instead of implying it happened.

- **A theme layer: the picture can be made to look like something now.** All of
  it is off by default, because a practice aid and a piece of spectacle want
  opposite things and the practice aid is the default.
  - `visual.gradient_top` and `visual.gradient_bottom`, a vertical gradient
    behind everything. Both or neither, and setting them replaces
    `visual.background`. They may have a hue, which `background` may not: the
    grayscale rule exists so a practice render cannot drift into competing with
    the colours that say which hand is playing, and setting a gradient is itself
    the opt-in. Measured at 5.40 ms against the flat fill's 5.38, which is to
    say free: it is the same write to the same pixels from a different source.
  - `visual.note_border_shade`, from -1 (black) through 0 (the bar's own colour)
    to +1 (white). The default -0.45 is exactly the dark edge that was drawn
    before. Positive lights a bar from inside rather than cutting it out of the
    background. A shade rather than a colour of its own, because the border is
    drawn inside the bar and a short note is mostly border, so keeping the bar's
    hue is what leaves which hand readable at the edge.
  - `visual.bar_gradient`, a brightness ramp along each bar. Positive fades the
    top, negative fades the bottom, 0 is the flat fill. 0.69 ms for a busy 1080p
    frame. The ramp is worked out over the bar's whole length and then clipped,
    so a bar leaving the top of the screen does not have its shading slide.
  - `--theme`, with `midnight`, `ember`, `neon` and `aurora`. A preset changes
    how the piece is played and a theme only how it looks, so they are separate
    flags and compose. `psv presets` now describes both. Every theme is tested
    to keep the two hands' colours far enough apart to tell apart, and to be
    writable as a config file: a theme is a shortcut, never a capability.
  - The keyboard is deliberately not themed. It is a picture of a real
    instrument and it reads as itself under every scheme.

- **Renders run across processes, and how hard the encoder works is now your
  choice.** Für Elise at 1080p60 took 1:41 and now takes 0:44, or 0:34 asking
  for `--encode fast`.
  - `visual.workers`, or `--workers`. `0` uses one process per core, capped at
    eight; `1` is the previous single-process path, unchanged. A render under
    240 frames is never split, since a worker costs an interpreter and an ffmpeg
    process before it draws anything.
  - `visual.encode`, or `--encode`: `small`, `balanced` or `fast`. This is how
    long the encoder spends looking for things to compress. It changes the file
    size, not the picture: `balanced` is about 1.3x the file of `small` and
    `fast` about 2.8x. Default is `balanced`.
  - The two are worth 2.03x and 1.04x separately, and 2.30x together, because a
    single-process render was never waiting on the encoder. Both were needed and
    neither was obvious without measuring.
  - The timeline is cut into spans, each rendered and encoded by its own
    process, and joined with ffmpeg's concat demuxer without re-encoding. The
    parent counts what its workers report and refuses to write a video whose
    frames do not add up.
  - `render_frame` is untouched. Splitting is exact rather than approximate
    because `frame_times` computes `start + index / fps` rather than adding, so
    a span beginning at frame k produces the timestamps counting from zero
    would.
  - Two assumptions turned out wrong when measured: encoding was never serial,
    and x264 was already using every core.


- **MusicXML input.** `psv run sonata.musicxml -o practice.mp4`, and `.mxl`,
  and `.xml`. Any command that reads a file now takes either format, told apart
  by content rather than by extension, since notation software exports `.xml`
  as readily as `.musicxml`.
  - **Staves are hands, stated rather than inferred.** This is the reason to
    read MusicXML at all. A piano part is written on two staves and the file
    says which staff every note is on. Guessing that from MIDI track names is
    what cost the Rondo alla Turca a quarter of its notes.
  - Dynamics arrive as `pp` and `ff` rather than as velocity bytes, and pedal
    as a mark with a start and a stop. A MIDI export of the same score usually
    has neither.
  - Ties become one note rather than two. Chords, `<backup>`, `<forward>`,
    mid-score division changes, grace notes, rests and tempo marks all handled;
    `<backup>` is the one that decides whether a second voice lands at the
    right time, and it has both a generated fixture and a real one.
  - Written against `xml.etree` and `zipfile`. The alternative, `music21`,
    brings fourteen packages including matplotlib to parse a text format.
  - **Repeats are unrolled into a linear timeline.** `|: :|` with a `times`
    count, first- and second-time bars, D.C., D.S., segno, coda and fine. A
    falling note happens at a time, so the measures have to be laid out in the
    order a player meets them; Für Elise's two repeats take it from 106 written
    measures to 127 played, 815 notes to 951, and 2:14 to 2:36.
    - The reading of the marks and the working-out of the order are separate,
      and the second half knows nothing about XML. That is where the mistakes
      live and it is testable by writing the marks down directly.
    - A tie left open across a jump is dropped rather than joined to whatever
      the jump lands on, which is the shape of the bug that once cost 428
      notes.
    - **Repeats do not nest.** A `|:` inside a first-time bar is flattened, and
      the piece comes out shorter than it is written. That is logged at warning
      level rather than done quietly.
  - Tempo is read from an engraved `<metronome>` mark where a file writes no
    `<sound tempo>` beside it, dotted beat units included. A mark reading
    "dotted quarter equals quarter" is a change of notation rather than of
    speed, and is not read as one.
  - Transposing instruments are read at sounding pitch. A clarinet in B flat
    writes a C for the B flat below it; at written pitch it would sit a tone
    sharp against every other part.
  - 29 files of the Unofficial MusicXML Test Suite are fetched and
    hash-verified by `scripts/fetch_test_scores.py`. They are MIT and therefore
    gitignored, the same reasoning that keeps the CC BY-SA songs out: this
    repository is 0BSD and should not quietly attach a condition it says is not
    there. CI runs against the generated fixtures instead.

- **M8 quality of life.** Everything that stood between the tool working and
  the tool being pleasant to use.
  - `--preset small-hands|beginner|as-written|draft`, with `psv presets`
    printing what each one sets. Precedence is file, then preset, then flags.
  - `hands.max_span_semitones = 0` and `--span N` for an unlimited-span mode.
    `constrain` then verifies nothing and changes nothing, rather than running
    at a very wide limit, so "unlimited" cannot come to mean 36. Loud in the
    report: an unplayable arrangement should not be a surprise at the piano.
  - `psv instruments`, reading the SoundFont's own preset names where one is
    configured and the General MIDI names where none is. The `.sf2` parser
    checks every chunk offset against the real file length, because SoundFonts
    are untrusted input.
  - `visual.note_border`, an outline in a darker shade of each bar's own
    colour. Repeated notes on one key drew as a single block; four fast repeats
    looked like one long note, which defeats the point of the video.
  - `audio.stereo_width`, panning the built-in synth by register with an
    equal-power law. Low notes left, high right, as they sit under your hands.
  - `docs/SOUNDS.md`: where SoundFonts come from, why bigger is usually better,
    and that `program` indexes into the font rather than into General MIDI.

- Project scaffolding: packaging, linting, type checking, tests, and CI.
- Architecture decision record covering the interface choice, the pipeline stages,
  and the component selection for each — see `docs/ARCHITECTURE.md`.
- Milestone-by-milestone build plan.
- CLI skeleton (`psv`) defining the `inspect`, `arrange`, `constrain`, `render`,
  and `run` commands. None are implemented yet.
- Feature registry (`tests/features.toml`) covering all 50 user-visible features,
  with a coverage gate that blocks marking one done before a test claims it.
- Nineteen synthetic MIDI fixtures covering dynamics, pedalling, pathological hand
  spans, and parser edge cases that no real score contains.
- Four Mutopia Project test songs. The two public-domain ones are committed so CI
  can run offline; the two CC BY-SA ones are gitignored and fetched by
  `scripts/fetch_test_songs.py` with SHA-256 verification.
- Test plan explaining the two-tier asset strategy.

### Fixed

- **A bright vertical rule beside every falling note.** Cutting a rectangle
  around the black keys clamped its first piece to the keyboard line without
  also clamping it to the rectangle's own bottom, so anything drawn entirely
  above the keys was stretched down to them. A halo's lower edge is above the
  keys for as long as its bar is falling, which turned a five-pixel strip into
  an 879-pixel line. Introduced by the occlusion pass in the commit before this
  one, and locked in by its own reference image, which was regenerated with the
  fault in it. A property test now asserts that splitting a rectangle never
  reaches outside it.
- **The halo's rings were nested rectangles, not shells.** Each ring was drawn
  from the bar's edge out to its own distance, so it covered every ring inside
  it and the pixel against the bar collected all five alphas while the outermost
  collected one. That is a hard rim about five times brighter than the falloff
  asks for. Each shell now covers its own band, and the count falls with the
  spread so a shell is never thinner than a pixel.

- **Effects that reach onto the keyboard pass behind the black keys.** The
  keyboard is drawn whites first so blacks sit on top of them, but effects run
  after the keyboard, which put them on top of everything: a struck white key
  painted its strike flash and its trail across the front half of both black
  neighbours. Each such rectangle is now cut into the pieces a black key does
  not cover, with the alpha scaled by each piece's share of the original width,
  so light squeezed into the tab between two black keys stays a wash instead of
  becoming a hard line. A key never occludes itself.

- **The key glow follows the key rather than its bounding box.** A white key is
  not a rectangle: for the length of the black keys it is only the tab between
  them. Lit at full width for that whole length, the glow drew over the half of
  each black neighbour sitting in front of it, and the black key looked lit too.
  `KeyboardGeometry.visible_span` now says how wide a key is at a given depth,
  and the glow narrows over the black keys and widens below their ends.

- **Bloom is smooth now, not blocky.** Scaling the shrunken glow back up by
  repeating pixels made every blurred pixel a 6x6 square at 1080p, which against
  a near-black background the eye picks out easily. Blurring harder only lowered
  the contrast between neighbouring blocks. Pillow's bilinear resize does the
  stretch properly for 9.5 ms against the repeat's 6.3 ms, where bilinear in
  numpy costs 57 ms and a full-resolution smoothing pass 61 ms. The frame got
  faster overall, 41.4 ms against 44.5 ms, because the gain is applied before
  the stretch and the light travels as bytes.

- **The halo no longer squares off a rounded bar.** Drawn as four full-width
  strips it is a rectangle of light, and a rectangle around a rounded bar
  redraws the corners the rounding removed, in glow rather than in the bar's
  colour, so the bar reads as square with dark corners. Each strip now stops
  short by however far the rounding reaches in. At `note_radius = 0` the inset
  is 0 and the ring is what it always was, pixel for pixel.

- **Bloom no longer glows the keyboard, and no longer arrives in squares.**
  Three faults, all visible: it read the whole frame, so most of the glow came
  from the white keys rather than from the music; it scaled a shrunken copy back
  up by repeating pixels, which put the blur on screen as hard 6x6 blocks; and
  it used a hard brightness threshold, so a bar popped as it crossed. It now
  reads and writes only above the strike line, blurs far enough below the shrink
  factor that neighbouring blocks barely differ, and blooms the light *above*
  the floor rather than the whole pixel. Measured at 1080p the fix is slightly
  cheaper than what it replaces, because the keyboard is a sixth of the frame
  and the wider blur is free.

- **Videos are written full range, so grey levels survive the encode.** h264
  defaults to the television range, 16-235, which is right for camera footage
  and wrong for a picture drawn in RGB: about one level in seven had nowhere to
  land and consecutive levels collapsed into one. Invisible until something
  moved slowly across a large flat area, and then not: the `pulse` effect walks
  the background up a level at a time, and 18, 19, 20, 21 came back as 17, 18,
  19, 20 with a level repeated here and two skipped there, so a smooth brighten
  arrived as an uneven stutter. Mean round-trip error per channel over a real
  1080p frame drops from 0.441 to 0.319. The stream is tagged bt709 to match
  what is written, since a half-tagged stream is how this kind of thing starts.

- `bloom` worked on a fixed eighth of the frame, which put a 320x180 render's
  bloom on a 40x22 image and quietly did nothing. The shrunken copy is now a
  fixed number of rows instead, so the effect is the same effect at every size.

- The alignment grid is mixed with the background a row at a time, so it stays
  equally faint down a gradient instead of vanishing into the dark end. It is
  still drawn rather than composited, which is what keeps a crossing of two grid
  lines exactly as faint as either line alone.

### Changed

- One definition of what a colour is, in `psv.rgb`. Reading `#4a90d9` and
  deciding whether a colour is grey had been written twice, once in `psv.config`
  to validate and once in `psv.render.color` to parse, because config cannot
  import the renderer. Now both import the primitives, and there is a test that
  every string validation accepts, parsing can read.
- `constraints/hands.py` and `constraints/salience.py` described the arrange
  stage as work that had not happened yet. It has. The fallback assignment they
  document is still real, but it is a fallback now rather than a placeholder.

- The README's config reference is loaded by a test rather than copied into one.
  It had drifted into two `[visual]` headers, which TOML rejects outright, and an
  `[[visual.effects]]` entry in the middle of the table that would have swallowed
  the two keys after it. The test that claimed to guard this was checking a
  hand-copied subset, so it could not have caught either. It now reads the file,
  and a second test compares the block against the config dataclasses so a new
  setting cannot ship undocumented.

- Global flags work on either side of the subcommand. `psv run ... -c x.toml`
  was a usage error while `psv -c x.toml run ...` was not, which nothing about
  the flags suggested.
- Every pinned GitHub action moves to the version Dependabot asked for, which
  clears the Node 20 deprecation warning on every job.
- Duet mode moves out of the roadmap to `research/future-ideas.md`. The data
  model is shaped for it, but a third and fourth hand colour would eat the
  margin that makes hue-for-hand and brightness-for-velocity readable.

- **M8: the four practice settings, on one bar index.** `psv run` and
  `psv render` take `--tempo`, `--bars`, `--hands` and `--count-in`
  / `--metronome`, and a `[practice]` table says the same in a config file.
  - `psv.tempo.Meter` - where the bar lines fall, built from the tempo map and
    the time signatures together. Bars are numbered from 1 and a meter change
    starts a new one. All four settings rest on this, and `beat_lines = "bar"`
    now draws from it too, so bar lines stay on the bars after a meter change
    instead of drifting from the change onward.
  - `psv.practice` - `time_scaled`, `for_hand`, `bar_window`, `click_times`,
    and `prepare`, which applies the lot. Every one of them runs *after*
    arrange and constrain, so the arrangement is the same at any practice
    speed: the constraint engine measures overlaps in real seconds against a
    fixed tolerance, and scaling the time before it ran would give a different
    piece at every tempo.
  - `psv.audio.click` - the count-in and metronome click, mixed into whatever
    the synth backends produced. `mux` cannot carry them, because the audio is
    a file you already have, and it says so rather than dropping them quietly.
  - `render_frame` takes a `focus` hand. The other hand is still drawn, faintly:
    knowing where it is is half the reason to practise hands separately. It is
    the soundtrack that goes quiet, not the picture.
  - The count-in opens the render window before the music rather than splicing
    silence into the score, so the notes keep the times they already had and
    the picture and the soundtrack cannot disagree about where the music starts.

- The CodeQL workflow is back. It was removed because code scanning
  cannot be enabled on a private repository without Advanced Security;
  the repository is public now, so it has somewhere to report to.
- `--start` now defaults to unset rather than 0, so `--bars` can tell whether it
  was given. Combining `--bars` with `--start` or `--seconds` is an error: they
  are two ways of saying the same thing.

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

### Fixed

- **`psv inspect` contradicted the arrange stage on a score whose hands
  cross.** The report asked only whether two parts sit in separate registers,
  so Für Elise, whose left hand crosses up over the right, was called "not
  separated; needs the arrange stage" although MusicXML had stated its hands
  and `arrange` correctly left it alone. The report now answers from the hands
  themselves where they are known, and falls back to register only where they
  are not. Third time this particular disagreement has cost something, so it
  is now asserted by a test.

- **The short-note leak was in five sweeps, not one.** The previous entry fixed
  the copy in `arrange`; `detect_violations`, `widest_span_per_hand`,
  `apply_difficulty` and `psv inspect`'s polyphony scan each had their own copy
  of the same code and the same defect. On one real file that meant 547 span
  violations that did not exist out of 747 reported, a left hand measured at 21
  semitones where the truth was 12, and `difficulty = "medium"` removing 273
  notes when the honest answer was none. The constraint engine was then
  faithfully repairing damage that was never there. All five now share
  `psv/sweep.py`, which exists as much to make a sixth copy impossible as to
  remove the repetition.
- **A note shorter than the overlap tolerance permanently consumed a voice.**
  Its release is clamped to its own start, and releases sort before presses, so
  it was taken out of the held set before it was put in and then left there for
  the rest of the piece. After eight such notes the sweep believed the texture
  was full and dropped everything that followed. `assign_hands` shares that
  sweep, so hand assignment was being decided against the same phantom set.
  Releases that would land on their own press now sort after it.
- **`arrange` and `psv inspect` disagreed about what "already separated" means.**
  `inspect` calls a two-part score in distinct registers separated, and its own
  docstring says the arrange stage makes the decision that report is a hint for
  — but `arrange` only looked at whether hands were already assigned, which the
  MIDI reader derives from track names. An engraver that wrote "track 1" and
  "track 2" fell through to a full reduction. On Mozart's Rondo alla Turca that
  threw away 428 of 1614 notes, 71% of them after 3:12, while `inspect` reported
  the file as already separated and `--span 0` promised nothing would be
  touched. `arrange` now applies the same register test and labels the hands
  without moving a note.

- Config rejects a boolean where a number is wanted. `bool` is a subclass of
  `int` in Python, so `pedals.lanes = true` had been accepted as 1.

- **M5, M6 and M7: a working MVP.** `psv run song.mid -o practice.mp4` now does
  the whole job in one command.
  - `psv.arrange` - multi-instrument scores reduce to two hands. Density is
    capped by dropping the least salient notes, then hands are assigned by a
    split that moves with the music, so crossing voices survive and chords do
    not fling the hands across the keyboard. A file that already has two hands
    is left alone.
  - `psv.audio` - `fluidsynth` (a sampled instrument from a SoundFont),
    `builtin` (numpy synth, honours velocity and the sustain pedal), `mux`
    (your own recording), and `none`. Each falls back to the next when what it
    needs is missing, and says why.
  - `psv.pipeline` - parse, arrange, constrain, render, synthesise, mux. The
    video is rendered silent and the soundtrack muxed on afterwards, so the
    renderer stays a pure function of the score and a failure says which half
    it was in.
  - `psv run` and `psv arrange` commands. Every pipeline command is now real.
- 508 tests, all 50 features done.

- **CI was red on every push.** Two defects in this repo's own test setup, both
  invisible locally:
  - `FORCE_COLOR: 1` in the workflow made Python 3.14 colour argparse help, so
    the CLI help assertion matched against ANSI escapes. Failed on 3.14 only.
    The workflow now sets `NO_COLOR`, and the assertion strips ANSI so a
    developer with `FORCE_COLOR` set does not hit it either.
  - `imageio_ffmpeg.read_frames` only closes its subprocess pipes while ffmpeg
    is still running, so fully consuming the generator, as frame counting must,
    left them to the garbage collector. pytest promotes the resulting
    `ResourceWarning` to a failure, blaming whichever test was running when the
    collector caught up. Video probing moved to `tests/probe.py`, which reads
    metadata without exhausting the generator and counts frames through
    `subprocess.run` instead. Reproduced only on Linux and macOS, so fixed by
    construction and confirmed by CI rather than locally.
- **Removed the CodeQL workflow.** It could not upload results because code
  scanning needs a public repository or GitHub Advanced Security, neither of
  which applies here, so it warned on every push about something nobody could
  act on. `ruff`'s bandit ruleset still runs and is the check that actually
  reads this code.

### Deferred

- Everything deliberately left out of the MVP is written up as **M8** in
  the build plan: practice tempo, section looping, count-in, one-hand
  practice, better tone, presets, duet mode, a live player, and more.
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
