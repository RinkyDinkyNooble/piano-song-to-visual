"""Encode a sequence of frames into a video file.

ffmpeg comes from `imageio-ffmpeg`, which ships its own binary, so nothing here
depends on a system install or shells out to a path a user controls.

Frames are generated lazily and handed to the encoder one at a time. A 1080p60
render of a four-minute piece is around 14,400 frames; holding them all would
cost tens of gigabytes, so the generator is not an optimisation but a
requirement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np

from psv.config import VisualConfig
from psv.model import Hand, Score
from psv.render.frame import Frame, Palette, render_frame

log = logging.getLogger(__name__)

#: Seconds of silence left after the last note so the final bar is not cut off.
TAIL_S = 1.0


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
    knowing anything about terminals.
    """
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise VideoWriteError(
            "video output needs the render extra: pip install "
            "'piano-song-to-visual[render]'"
        ) from exc

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    total = max(1, round(duration * config.fps))

    log.info(
        "rendering %d frames at %dx%d %dfps to %s",
        total,
        config.width,
        config.height,
        config.fps,
        output,
    )

    writer = imageio_ffmpeg.write_frames(
        str(output),
        size=(config.width, config.height),
        fps=config.fps,
        # Without this, imageio pads the frame up to a multiple of 16 and the
        # output silently differs from the size that was asked for. Config
        # already requires even dimensions, which is what h264 actually needs.
        macro_block_size=1,
        ffmpeg_log_level="error",
    )
    writer.send(None)
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
