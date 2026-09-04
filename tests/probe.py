"""Reading back a rendered video, without leaking the ffmpeg pipes.

`imageio_ffmpeg.read_frames` is a generator owning a subprocess and two pipes,
and it only closes those pipes if ffmpeg is *still running* when the generator
finishes:

    if process.poll() is None:
        process.stdout.close()
        process.stdin.close()

Consume the generator to the end and ffmpeg has already exited on its own, so
that branch is skipped and the pipes are left to the garbage collector. It
raises `ResourceWarning` from a destructor, at some unrelated later moment.
pytest promotes unraisable exceptions to failures and this project treats
warnings as errors, so it fails the suite, blaming whichever test happened to be
running when the collector caught up. That is what CI was reporting.

Closing the generator does not help: it is already exhausted, so `close()` is a
no-op. So the two helpers here take different routes on purpose.

`video_meta` stops after the first yield, while ffmpeg is still alive, which is
exactly the case imageio cleans up correctly.

`frame_count` has to see every frame, so it does not use imageio at all. It runs
ffmpeg directly through `subprocess.run`, which owns and closes its own pipes.
"""

from __future__ import annotations

import re
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

from psv.audio.backends import ffmpeg_exe

#: ffmpeg reports progress on stderr as `frame=  20 fps=... time=...`.
_FRAME_COUNT = re.compile(r"frame=\s*(\d+)")


def video_meta(path: Path | str) -> dict[str, Any]:
    """Size, fps and duration of a rendered video.

    Stops at the metadata, leaving ffmpeg running, so imageio's own teardown
    closes the pipes.
    """
    import imageio_ffmpeg

    with closing(imageio_ffmpeg.read_frames(str(path))) as reader:
        meta: dict[str, Any] = next(reader)
        return meta


def video_size(path: Path | str) -> tuple[int, int]:
    size: tuple[int, int] = video_meta(path)["size"]
    return size


def frame_count(path: Path | str) -> int:
    """How many frames the file actually contains.

    Decodes to the null muxer and reads the count off ffmpeg's own progress
    output. `subprocess.run` closes every pipe it opens, so nothing is left for
    the garbage collector to complain about.
    """
    result = subprocess.run(
        [ffmpeg_exe(), "-nostdin", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    matches = _FRAME_COUNT.findall(result.stderr)
    if not matches:
        raise AssertionError(
            f"ffmpeg reported no frame count for {path}:\n{result.stderr[-500:]}"
        )
    return int(matches[-1])


def decoded_frames(path: Path | str, width: int, height: int) -> list[Any]:
    """Every frame of a video, decoded, as arrays.

    Through `subprocess.run` for the same reason `frame_count` is: it owns and
    closes its own pipes. `imageio_ffmpeg.read_frames` closes ffmpeg's only
    `if process.poll() is None`, so reading a video to the end - which
    comparing every frame must - leaves them to the garbage collector, and this
    project turns the resulting ResourceWarning into a failure charged to
    whichever test happens to be running.

    Only for small videos: the whole thing is decoded into memory at once.
    """
    import numpy as np

    result = subprocess.run(
        [
            ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    frame_bytes = width * height * 3
    raw = result.stdout
    if len(raw) % frame_bytes:
        raise AssertionError(
            f"{path} decoded to {len(raw)} bytes, not a whole number of "
            f"{width}x{height} frames"
        )
    return [
        np.frombuffer(raw[i : i + frame_bytes], dtype=np.uint8).reshape(
            height, width, 3
        )
        for i in range(0, len(raw), frame_bytes)
    ]
