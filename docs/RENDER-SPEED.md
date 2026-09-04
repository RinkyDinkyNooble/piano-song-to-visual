# Making renders faster

A 1080p60 render of a short piece is thousands of frames. Für Elise, 2:35 long,
is 9410 of them. That wait sits between making a change and seeing whether it
worked, which makes it the most expensive number in the project even though it
breaks nothing.

Frames are independent and `render_frame` is a pure function of the score and a
timestamp, so the work parallelises in principle. This is the plan for finding
out whether it does in practice, and what to build if it does.

**Nothing here is built.** The order matters: measure, decide, then build.

## Step 0: measure, before choosing anything

The per-frame drawing cost has been measured once, at 8.6 ms after the
`Score.notes` caching landed and 10.7 ms before it. The encoder's share has
never been measured at all, and it decides the whole question: if ffmpeg is
already half the wall time, then parallel drawing alone cannot do better than
twice as fast no matter how many cores it gets.

So the first thing is a throwaway script, in the scratchpad rather than the
repo, timing three runs over the same frame count:

| Run | What it does | What it isolates |
| --- | --- | --- |
| A | `iter_frames`, discarding each frame | drawing |
| B | one pre-made frame sent to the writer N times | pipe and encode |
| C | `render_video` as it stands | the truth |

A plus B should land close to C. Where it does not, something unaccounted for
is in the loop and needs finding before any of this is worth doing.

Run it at 1920x1080 and again at 320x180. The split will move with resolution:
drawing scales with pixel count, and so does encoding, but not at the same rate.

Record the numbers in this file. An estimate that was never checked is how the
roadmap came to claim an interval index that had never been built.

### The gate

If drawing is under half the wall time, stop and write down why. The ceiling on
parallel drawing is `1 / (1 - drawing_share)`, so a 40% share caps out at 1.7x
however many cores are thrown at it, and that is not worth a process pool in a
tool that has to stay easy to install.

If drawing dominates, carry on to step 1.

## Step 1: choose the shape

Two ways to do it, and the measurement decides between them.

### Option A: parallel drawing, one encoder

Workers draw frames; the parent sends them to a single ffmpeg process in order.

- `ProcessPoolExecutor`, with an initializer handing each worker the score,
  config and palette once. After that a task is a float and a return is a frame.
- Bounded lookahead: at most `workers * 2` futures in flight, yielded strictly
  in submission order. Unbounded would buffer the whole video — a 1080p RGB
  frame is 6.2 MB, so 9410 of them is 58 GB.

The risk is the return trip. Every frame crosses a process boundary as 6.2 MB
of pickled array, which at 60 fps is a lot of copying to pay for work that took
8.6 ms to do. Step 0's script should time one frame's round trip through a pool
before this option is taken seriously.

It also leaves encoding serial, so the gate's ceiling still applies.

### Option B: parallel segments, concatenated

Split the timeline into one contiguous span per worker. Each worker renders
*and encodes* its own span to a temporary file; ffmpeg's concat demuxer joins
them with `-c copy`.

- No frame ever crosses a process boundary.
- Encoding parallelises too, so the gate's ceiling does not apply.
- Every segment is an independent encode and therefore starts on a keyframe
  already, which is what concat needs.

The costs are real but small: temporary files to clean up on failure, a
concat step to get right, forced keyframes at the seams costing a little file
size, and progress reporting becoming several counters to add up.

**Option B is the one to try first** unless step 0 shows encoding to be
negligible, in which case A is less machinery.

### Why the timestamps cannot drift

`frame_times` computes `start + index / fps` rather than adding repeatedly, so
the frame at index k has the same timestamp whether it was reached by counting
from zero or by starting a segment there. Splitting the timeline is therefore
exact rather than approximate, and this is the property that makes either option
safe. It is already true of the code; it must stay true.

## Step 2: build it

- `render_frame` is not touched. Its purity is what the reference images and the
  determinism guarantee rest on, and it is what makes this possible at all.
- One config key, `render.workers`. `0` means one per core, `1` means the
  current single-threaded path, and that path stays as it is rather than
  becoming a pool of one.
- Default to `0` only after step 3 passes on all three operating systems. Until
  then it defaults to `1` and is opt-in.
- Windows uses spawn, so `Score`, `VisualConfig` and `Palette` must pickle. They
  are frozen slotted dataclasses, which should be fine and has not been checked.
- Each worker owns its own writer and closes it. The `imageio-ffmpeg` pipe leak
  that CI caught once is per-writer, so more writers means more chances to hit
  it.

## Step 3: verify

Not "it looks right". Four checks:

1. **Same frame count** as the single-threaded render, at several durations,
   including one that does not divide evenly by the worker count.
2. **Same duration** back out of ffprobe.
3. **Pixel-exact spot checks.** Pull frames from the parallel and serial
   renders at the same timestamps and compare arrays. They must be identical,
   not similar: `render_frame` is deterministic and the timestamps are computed
   the same way, so anything else is a bug.
4. **A failing worker fails the render**, loudly, with the temporary files
   cleaned up. A silently short video is the worst outcome available here and
   is exactly the failure this project has already paid for twice.

## Step 4: re-measure and record

Same script, same piece, same resolution. Write the before and after into this
file with the machine and core count beside them.

**If it is under 2x, revert it.** A process pool is a permanent cost in
installation problems, debugging difficulty and platform-specific failure, and
it has to buy something worth that. Reverting after measuring is not a wasted
step; it is the measurement doing its job.
