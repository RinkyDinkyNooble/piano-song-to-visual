"""The ffmpeg process a render writes its frames into.

Owned here rather than borrowed from `imageio_ffmpeg.write_frames`, which
starts ffmpeg itself and so leaves no way to say how hard it may work. Two
things need saying. How many threads, because a parallel render runs one
encoder per worker, and each left to itself starts one and a half threads per
core and holds its own frames in each. And at what priority, so a render uses
what the computer is not using rather than taking it from whoever is at it.

`imageio_ffmpeg` still supplies the binary, so nothing here depends on a system
install or runs a path a user controls.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import IO, Any

import numpy as np

from psv.errors import VideoWriteError

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

#: How far below normal an encoder runs, as a POSIX nice increment. Windows has
#: named classes instead, and below-normal is the one that matches.
NICE_STEP = 10
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
#: Keeps a Ctrl-C in the console from reaching ffmpeg before psv has decided
#: what to do about it. psv kills the encoder itself on an interrupt.
_CREATE_NEW_PROCESS_GROUP = 0x00000200

#: How much of ffmpeg's own complaint to repeat when it fails.
STDERR_TAIL = 2000


def ffmpeg_command(
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    preset: str,
    crf: int,
    threads: int,
) -> list[str]:
    """The command line for one encode. ``threads`` of 0 leaves x264 to choose."""
    import imageio_ffmpeg

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        preset,
        "-crf",
        str(crf),
    ]
    if threads > 0:
        command += ["-threads", str(threads)]
    return [*command, *COLOUR_PARAMS, str(output)]


def lower_own_priority() -> None:
    """Drop this process below normal priority, and with it anything it starts.

    Run at the top of each render worker. On Windows a child process inherits a
    below-normal class from its parent, and on POSIX it inherits the nice value,
    so the encoder a worker starts is covered too. Failing to lower priority is
    never a reason to fail a render.
    """
    try:
        if sys.platform == "win32":
            _set_windows_priority_class(_BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(NICE_STEP)
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        pass


def _set_windows_priority_class(priority_class: int) -> None:
    """SetPriorityClass on this process, with the handle typed properly.

    ctypes returns a plain int unless told otherwise, and `GetCurrentProcess`
    returns a pseudo-handle of -1. Squeezed through a 32-bit int it arrives as
    an invalid 64-bit handle, the call fails, and nothing says so; a test in
    test_render_resources reads the class back to catch exactly that. A private
    WinDLL, so setting argument types here cannot change them for anyone else
    using `ctypes.windll`.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        if not kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), priority_class):
            raise OSError(ctypes.get_last_error(), "SetPriorityClass failed")


class Encoder:
    """One running ffmpeg, taking frames on stdin and writing one h264 file.

    Use as a context manager. Leaving the block normally finishes the file and
    raises `VideoWriteError`, with what ffmpeg said, if it could not. Leaving
    on an exception kills ffmpeg rather than waiting for it, since the file is
    not going to be used.
    """

    def __init__(
        self,
        output: Path,
        *,
        width: int,
        height: int,
        fps: int,
        preset: str,
        crf: int,
        threads: int = 0,
    ) -> None:
        self.output = output
        self._shape = (height, width, 3)
        command = ffmpeg_command(
            output,
            width=width,
            height=height,
            fps=fps,
            preset=preset,
            crf=crf,
            threads=threads,
        )
        # A file rather than a pipe: ffmpeg blocks once a pipe's buffer fills,
        # and nothing reads stderr until something has gone wrong.
        # Closed by `close` or `__exit__`, which is why it is not a `with`.
        self._stderr: IO[bytes] = tempfile.TemporaryFile()  # noqa: SIM115
        try:
            self._process = subprocess.Popen(  # noqa: S603 - argument list, no shell
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr,
                **_quiet_low_priority(),
            )
        except OSError as exc:
            self._stderr.close()
            raise VideoWriteError(f"could not start ffmpeg: {exc}") from exc
        _lower_child_priority(self._process.pid)

    def write(self, frame: np.ndarray) -> None:
        """Send one frame: uint8 RGB at the size this encoder was opened with."""
        if frame.shape != self._shape or frame.dtype != np.uint8:
            raise ValueError(
                f"frame is {frame.shape} {frame.dtype}, the encoder expects "
                f"{self._shape} uint8"
            )
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.write(np.ascontiguousarray(frame).data)
        except OSError as exc:
            # ffmpeg has gone. Its own account of why is the useful part.
            said = self._said()
            self.kill()
            raise VideoWriteError(
                f"ffmpeg stopped while writing {self.output}: {said}"
            ) from exc

    def close(self) -> None:
        """Finish the file and wait for ffmpeg, raising if it failed."""
        stdin = self._process.stdin
        try:
            if stdin is not None and not stdin.closed:
                # An OSError here means ffmpeg already exited; the exit code
                # below says why.
                with contextlib.suppress(OSError):
                    stdin.close()
            code = self._process.wait()
            said = self._said()
        finally:
            self._stderr.close()
        if code != 0:
            raise VideoWriteError(
                f"ffmpeg could not write {self.output} (exit {code}): {said}"
            )

    def kill(self) -> None:
        """Stop ffmpeg now, without finishing the file."""
        if self._process.poll() is None:
            self._process.kill()
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()
        self._process.wait()

    def _said(self) -> str:
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""
        return text[-STDERR_TAIL:]

    def __enter__(self) -> Encoder:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if kind is None:
            self.close()
            return
        self.kill()
        self._stderr.close()


def _quiet_low_priority() -> dict[str, Any]:
    """Popen arguments for an encoder: no console window, below-normal priority."""
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        arguments: dict[str, Any] = {
            "startupinfo": startupinfo,
            "creationflags": _CREATE_NEW_PROCESS_GROUP | _BELOW_NORMAL_PRIORITY_CLASS,
        }
    else:
        arguments = {"start_new_session": True}
    return arguments


def _lower_child_priority(pid: int) -> None:
    """POSIX has no priority argument to Popen; lower the child once it exists.

    Windows set the class as the process was created, so there is nothing to do.
    """
    if sys.platform != "win32":
        try:
            current = os.getpriority(os.PRIO_PROCESS, 0)
            os.setpriority(os.PRIO_PROCESS, pid, min(19, current + NICE_STEP))
        except OSError:  # pragma: no cover - platform dependent
            pass
