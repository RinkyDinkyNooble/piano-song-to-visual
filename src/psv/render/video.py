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
on twelve logical cores. The measurements, and the two
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

from psv.config import VisualConfig
from psv.errors import VideoWriteError
from psv.model import Hand, Score
from psv.render.encoder import COLOUR_PARAMS, Encoder, lower_own_priority
from psv.render.frame import Frame, Palette, render_frame
from psv.render.resources import (
    MAX_WORKERS,
    MIN_FRAMES_TO_SPLIT,
    RenderPlan,
    available_memory_mb,
    logical_cpus,
    plan_render,
)

# COLOUR_PARAMS, MAX_WORKERS and MIN_FRAMES_TO_SPLIT live in `encoder` and
# `resources` and are listed here so they stay importable from this module,
# where they were before 1.3.0.
__all__ = [
    "COLOUR_PARAMS",
    "MAX_WORKERS",
    "MIN_FRAMES_TO_SPLIT",
    "TAIL_S",
    "VideoWriteError",
    "frame_times",
    "iter_frames",
    "open_encoder",
    "plan_for",
    "render_video",
]

log = logging.getLogger(__name__)

#: Seconds of silence left after the last note so the final bar is not cut off.
TAIL_S = 1.0


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
    encoder_threads: int


def open_encoder(config: VisualConfig, output: Path, threads: int = 0) -> Encoder:
    """An encoder for one file, with this project's settings."""
    return Encoder(
        output,
        width=config.width,
        height=config.height,
        fps=config.fps,
        preset=config.encoder_preset,
        crf=config.crf,
        threads=threads,
    )


def _render_span(span: _Span) -> int:
    """Draw and encode one span. This is what runs in a worker process."""
    with open_encoder(span.config, Path(span.output), span.encoder_threads) as encoder:
        for index in range(span.first, span.first + span.count):
            encoder.write(
                render_frame(
                    span.score,
                    span.config,
                    span.start + index / span.config.fps,
                    palette=span.palette,
                    pedal_lanes=span.pedal_lanes,
                    focus=span.focus,
                )
            )
    return span.count


def plan_for(config: VisualConfig, total_frames: int) -> RenderPlan:
    """How many processes this render gets, given the machine as it is now.

    See `psv.render.resources` for how, and why the answer depends on memory
    as well as cores.
    """
    return plan_render(
        total_frames,
        config.workers,
        width=config.width,
        height=config.height,
        preset=config.encoder_preset,
        cpus=logical_cpus(),
        available_mb=available_memory_mb(),
    )


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
    encoder_threads: int,
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
                encoder_threads=encoder_threads,
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
    plan: RenderPlan,
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
            plan.workers,
            start=start,
            palette=palette,
            pedal_lanes=pedal_lanes,
            focus=focus,
            encoder_threads=plan.encoder_threads,
        )
        # Spawn on every platform rather than fork, so what is tested is what
        # runs. Forking a process that has threads is deprecated in 3.12, and
        # this project turns warnings into errors.
        context = multiprocessing.get_context("spawn")
        done = 0
        # Each worker lowers its own priority as it starts, and the encoder it
        # launches inherits that, so the render takes what the computer is not
        # otherwise using.
        with ProcessPoolExecutor(
            max_workers=len(spans),
            mp_context=context,
            initializer=lower_own_priority,
        ) as pool:
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

    # Open the destination before starting anything. A path that is a
    # directory, or not writable, says so here in one line, instead of after a
    # parallel render has drawn every frame and the join fails, or as ffmpeg's
    # stderr.
    try:
        with output.open("wb"):
            pass
    except OSError as exc:
        raise VideoWriteError(f"could not write {output}: {exc}") from exc

    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    total = max(1, round(duration * config.fps))
    plan = plan_for(config, total)

    log.info(
        "rendering %d frames at %dx%d %dfps to %s, %s encode, %s "
        "(set by %s; about %.1f GB), %d encoder threads each",
        total,
        config.width,
        config.height,
        config.fps,
        output,
        config.encode,
        f"{plan.workers} processes" if plan.workers > 1 else "one process",
        plan.limited_by,
        plan.expected_mb / 1024,
        plan.encoder_threads,
    )

    if plan.workers > 1:
        _render_in_parallel(
            score,
            config,
            output,
            total,
            plan,
            start=start,
            palette=palette,
            pedal_lanes=pedal_lanes,
            focus=focus,
            on_frame=on_frame,
        )
        log.info("wrote %s", output)
        return output

    with open_encoder(config, output, plan.encoder_threads) as encoder:
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
            encoder.write(frame)
            if on_frame is not None:
                on_frame(index, total)

    log.info("wrote %s", output)
    return output
