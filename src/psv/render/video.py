"""Encode a sequence of frames into a video file.

ffmpeg comes from `imageio-ffmpeg`, which ships its own binary, so nothing here
depends on a system install or shells out to a path a user controls.

Frames are generated lazily and handed to the encoder one at a time. A 1080p60
render of a four-minute piece is around 14,400 frames; holding them all would
cost tens of gigabytes, so the generator is not an optimisation but a
requirement.

**Rendering in parallel.** `render_frame` is a pure function of the score and a
time, so the timeline can be cut into spans and each span rendered and encoded
by its own process, then joined with ffmpeg's concat demuxer. Measured at 2.9x
on twelve logical cores. `docs/RENDER-SPEED.md` has the numbers and the two
assumptions that turned out to be wrong on the way there.

The property that makes this safe is in `frame_times`: it computes
`start + index / fps` rather than adding repeatedly, so a span beginning at
frame k produces exactly the timestamps counting from zero would. Keep it that
way. Adding would drift, and the spans would no longer join.
"""

from __future__ import annotations

import logging
import multiprocessing
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from psv.config import VisualConfig
from psv.model import Hand, Score
from psv.render.frame import Frame, Palette, render_frame

log = logging.getLogger(__name__)

#: Seconds of silence left after the last note so the final bar is not cut off.
TAIL_S = 1.0

#: Below this many frames, splitting the work costs more than it saves: each
#: worker pays for a Python interpreter and an ffmpeg process before it draws
#: anything.
MIN_FRAMES_TO_SPLIT = 240

#: More workers than this stops helping and starts competing. Measured: eight
#: is the best setting on a twelve-thread six-core machine, and twelve is
#: slightly worse.
MAX_WORKERS = 8

#: Write full-range colour, and say so in the stream.
#:
#: h264 defaults to the television range, where 0-255 is squeezed into 16-235.
#: That is right for camera footage and wrong for a picture drawn in RGB: about
#: one grey level in seven has nowhere to land, so consecutive levels collapse
#: into one. Nothing notices until something moves slowly across a large flat
#: area, and then it does. The `pulse` effect walks the background up a level at
#: a time, and 18, 19, 20, 21 came back as 17, 18, 19, 20, with a level repeated
#: here and two skipped there: a smooth brighten arriving as an uneven stutter.
#:
#: `pc` keeps 0-255. The colourspace is named alongside it because a stream
#: tagged half way is how this class of bug happens; the tags have to describe
#: what was actually written. bt709 primaries are sRGB primaries, which is what
#: the colours in the config are.
#:
#: The scale filter is not redundant with `-color_range`. That option sets the
#: tag, and whether the conversion follows is up to the build: on Windows it
#: did, and on Linux it wrote television-range samples and labelled them full,
#: so a decoder handed the levels back offset by sixteen and squeezed. Naming
#: the range in the filter is what actually performs the conversion. Only CI
#: could catch that, and it did.
#:
#: Measured over a 1080p render of real output: mean round-trip error per
#: channel falls from 0.441 to 0.319, and every background level the pulse walks
#: through comes back as itself instead of collapsing into its neighbour.
COLOUR_PARAMS = [
    "-vf",
    "scale=in_range=full:out_range=full",
    "-color_range",
    "pc",
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
]


class VideoWriteError(RuntimeError):
    """Encoding failed, or the encoder was unavailable."""


def frame_times(duration: float, fps: int, *, start: float = 0.0) -> Iterator[float]:
    """Yield the timestamp of every frame.

    Computed as ``start + index / fps`` rather than by repeated addition, so
    rounding error cannot accumulate over a long render.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    count = max(1, round(duration * fps))
    for index in range(count):
        yield start + index / fps


def iter_frames(
    score: Score,
    config: VisualConfig,
    *,
    start: float = 0.0,
    duration: float | None = None,
    palette: Palette | None = None,
    pedal_lanes: int = 1,
    focus: Hand | None = None,
) -> Iterator[Frame]:
    """Render every frame of the requested span, lazily."""
    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    for time in frame_times(duration, config.fps, start=start):
        yield render_frame(
            score,
            config,
            time,
            palette=palette,
            pedal_lanes=pedal_lanes,
            focus=focus,
        )


@dataclass(frozen=True, slots=True)
class _Span:
    """One contiguous run of frames, for one worker to draw and encode."""

    score: Score
    config: VisualConfig
    output: str
    first: int
    count: int
    start: float
    palette: Palette | None
    pedal_lanes: int
    focus: Hand | None


def _open_writer(config: VisualConfig, output: Path) -> Any:
    """An ffmpeg writer for one file, with this project's settings."""
    import imageio_ffmpeg

    try:
        with output.open("wb"):
            pass
    except OSError as exc:
        raise VideoWriteError(f"could not write {output}: {exc}") from exc

    writer = imageio_ffmpeg.write_frames(
        str(output),
        size=(config.width, config.height),
        fps=config.fps,
        # Without this, imageio pads the frame up to a multiple of 16 and the
        # output silently differs from the size that was asked for. Config
        # already requires even dimensions, which is what h264 actually needs.
        macro_block_size=1,
        ffmpeg_log_level="error",
        output_params=["-preset", config.encoder_preset, *COLOUR_PARAMS],
    )
    writer.send(None)
    return writer


def _render_span(span: _Span) -> int:
    """Draw and encode one span. This is what runs in a worker process."""
    writer = _open_writer(span.config, Path(span.output))
    try:
        for index in range(span.first, span.first + span.count):
            frame = render_frame(
                span.score,
                span.config,
                span.start + index / span.config.fps,
                palette=span.palette,
                pedal_lanes=span.pedal_lanes,
                focus=span.focus,
            )
            writer.send(np.ascontiguousarray(frame))
    finally:
        writer.close()
    return span.count


def worker_count(requested: int, total_frames: int) -> int:
    """How many processes to actually use.

    Zero asks for one per core. Short renders are not split at all: a worker
    pays for a Python interpreter and an ffmpeg process before it draws
    anything, and below a few hundred frames that costs more than it saves.
    """
    if requested == 1 or total_frames < MIN_FRAMES_TO_SPLIT:
        return 1
    wanted = requested or (multiprocessing.cpu_count() or 1)
    fits = max(1, total_frames // (MIN_FRAMES_TO_SPLIT // 2))
    return max(1, min(wanted, MAX_WORKERS, fits))


def _spans(
    score: Score,
    config: VisualConfig,
    scratch: Path,
    total: int,
    workers: int,
    *,
    start: float,
    palette: Palette | None,
    pedal_lanes: int,
    focus: Hand | None,
) -> list[_Span]:
    spans = []
    for index in range(workers):
        first = index * total // workers
        last = (index + 1) * total // workers
        if last <= first:
            continue
        spans.append(
            _Span(
                score=score,
                config=config,
                output=str(scratch / f"part{index:03d}.mp4"),
                first=first,
                count=last - first,
                start=start,
                palette=palette,
                pedal_lanes=pedal_lanes,
                focus=focus,
            )
        )
    return spans


def _join(spans: Sequence[_Span], scratch: Path, output: Path) -> None:
    """Concatenate the finished spans without re-encoding them.

    Each span is an independent encode and so already begins on a keyframe,
    which is what the concat demuxer needs. The list file names the parts
    relatively and ffmpeg runs from the scratch directory, so no path the user
    chose ever reaches it.
    """
    import imageio_ffmpeg

    listing = scratch / "parts.txt"
    listing.write_text(
        "".join("file '" + Path(span.output).name + "'\n" for span in spans),
        encoding="utf-8",
    )
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        listing.name,
        "-c",
        "copy",
        str(output.resolve()),
    ]
    try:
        subprocess.run(command, cwd=scratch, check=True, capture_output=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() if exc.stderr else ""
        raise VideoWriteError(f"could not join the rendered parts: {detail}") from exc


def _render_in_parallel(
    score: Score,
    config: VisualConfig,
    output: Path,
    total: int,
    workers: int,
    *,
    start: float,
    palette: Palette | None,
    pedal_lanes: int,
    focus: Hand | None,
    on_frame: Callable[[int, int], None] | None,
) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="psv-render-"))
    try:
        spans = _spans(
            score,
            config,
            scratch,
            total,
            workers,
            start=start,
            palette=palette,
            pedal_lanes=pedal_lanes,
            focus=focus,
        )
        # Spawn on every platform rather than fork, so what is tested is what
        # runs. Forking a process that has threads is deprecated in 3.12, and
        # this project turns warnings into errors.
        context = multiprocessing.get_context("spawn")
        done = 0
        with ProcessPoolExecutor(max_workers=len(spans), mp_context=context) as pool:
            futures = [pool.submit(_render_span, span) for span in spans]
            for future in as_completed(futures):
                done += future.result()
                if on_frame is not None:
                    on_frame(done, total)

        if done != total:
            raise VideoWriteError(
                f"rendered {done} frames but expected {total}; refusing to "
                "write a video that is quietly short"
            )
        _join(spans, scratch, output)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def render_video(
    score: Score,
    config: VisualConfig,
    output: Path | str,
    *,
    start: float = 0.0,
    duration: float | None = None,
    palette: Palette | None = None,
    pedal_lanes: int = 1,
    focus: Hand | None = None,
    on_frame: Callable[[int, int], None] | None = None,
) -> Path:
    """Render ``score`` to a video file and return its path.

    ``on_frame`` is called with (frames done, frames total) for progress
    reporting. It exists so the CLI can show progress without this module
    knowing anything about terminals. A parallel render reports one span at a
    time rather than one frame at a time, since a worker cannot call back here.
    """
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise VideoWriteError(
            "video output needs the render extra: pip install "
            "'piano-song-to-visual[render]'"
        ) from exc

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Open the destination before handing it to ffmpeg, for two reasons.
    #
    # The error is better: a path that is a directory, or not writable, says so
    # here instead of arriving as a page of ffmpeg stderr.
    #
    # And it avoids a leak in imageio-ffmpeg. Its writer closes ffmpeg's stdin
    # only `if p.poll() is None`, so a process that died opening its output has
    # already exited and that pipe is left to the garbage collector. On POSIX
    # that surfaces later as a ResourceWarning from a destructor, which this
    # project treats as an error, and it is charged to whichever test happens to
    # be running at the time. Not reaching that path is the only fix available
    # from outside the library.
    try:
        with output.open("wb"):
            pass
    except OSError as exc:
        raise VideoWriteError(f"could not write {output}: {exc}") from exc

    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    total = max(1, round(duration * config.fps))
    workers = worker_count(config.workers, total)

    log.info(
        "rendering %d frames at %dx%d %dfps to %s, %s encode, %s",
        total,
        config.width,
        config.height,
        config.fps,
        output,
        config.encode,
        f"{workers} processes" if workers > 1 else "one process",
    )

    if workers > 1:
        _render_in_parallel(
            score,
            config,
            output,
            total,
            workers,
            start=start,
            palette=palette,
            pedal_lanes=pedal_lanes,
            focus=focus,
            on_frame=on_frame,
        )
        log.info("wrote %s", output)
        return output

    writer = _open_writer(config, output)
    try:
        for index, frame in enumerate(
            iter_frames(
                score,
                config,
                start=start,
                duration=duration,
                palette=palette,
                pedal_lanes=pedal_lanes,
                focus=focus,
            ),
            start=1,
        ):
            writer.send(np.ascontiguousarray(frame))
            if on_frame is not None:
                on_frame(index, total)
    except (OSError, RuntimeError) as exc:
        raise VideoWriteError(f"could not write {output}: {exc}") from exc
    finally:
        writer.close()

    log.info("wrote %s", output)
    return output
