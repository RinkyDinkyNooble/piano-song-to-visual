"""How much of the machine a render may take: worker processes and encoder threads.

A parallel render runs one worker process per span, and each worker runs its own
x264 encoder. Both of those cost memory in proportion to the frame size, and the
encoder's cost also depends on its preset and its thread count. Left to itself,
x264 starts one and a half threads per core in every encoder. At 4K and the
`medium` preset, eight workers need at least 12.8 GB even with their threads
capped, on a machine with 16 GB and half of it in use, and it stopped
responding until the render was killed.

So the worker count is planned rather than taken from the core count alone. The
plan is a pure function of the numbers it is given, which is what the tests
drive; `available_memory_mb` and `logical_cpus` are the only parts that look at
the machine.

**The memory model** is one straight line per preset, fitted to the peak memory
of real parallel renders on the Windows machine this was reported from, three
presets at 1080p and 4K, and then raised by `MARGIN`::

    megabytes per worker = base + megapixels * per_megapixel

A worker's cost is its encoder plus its own Python process, measured together,
since the two are only ever started together. Measuring one encoder alone, fed
from a list of frames already drawn, overstated it by up to two thirds and
would have planned half the workers a 4K render can safely have.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Per preset, what one worker was measured to use at its peak: (base
#: megabytes, megabytes per megapixel), with `ENCODER_THREADS` threads. From
#: 87 and 250 MB for `ultrafast` at 1080p and 4K, 240 and 800 for `veryfast`,
#: and 440 and 1600 for `medium`, whose forty-frame lookahead is most of the
#: difference.
WORKER_COST: dict[str, tuple[float, float]] = {
    "ultrafast": (33.0, 26.0),
    "veryfast": (53.0, 90.0),
    "medium": (53.0, 187.0),
}

#: How much more than measured the plan assumes. A plan that is too cautious
#: renders more slowly; one that is too bold swaps the machine to a standstill.
MARGIN = 1.25

#: Threads per encoder. One worker draws about 18 frames a second at 4K and 45
#: at 1080p. Two `medium` threads encode 32 and 107, so a second thread is
#: what keeps the encoder from being the bottleneck, and a third buys nothing
#: but memory.
ENCODER_THREADS = 2

#: The share of currently available memory a render may plan to use. With
#: `MARGIN`, a render expects to actually use under three fifths of what was
#: free when it started, leaving the rest for everything else running.
MEMORY_SHARE = 0.7

#: More workers than this stops helping and starts competing for cores.
#: Measured: eight is the best setting on a twelve-thread six-core machine.
MAX_WORKERS = 8

#: What to assume is free when the machine will not say. Enough for a few
#: 1080p workers, and for one at 4K: a guess that errs toward a slower render
#: rather than a frozen computer.
ASSUMED_AVAILABLE_MB = 4096.0

#: Below this many frames, splitting costs more than it saves: each worker pays
#: for a Python interpreter and an ffmpeg process before it draws anything.
MIN_FRAMES_TO_SPLIT = 240


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """How many workers, how many threads each encoder gets, and why."""

    workers: int
    encoder_threads: int
    #: What set the worker count, for the log: "requested", "short", "cores",
    #: "memory" or "cap".
    limited_by: str
    #: Megabytes the plan expects to use, all workers together.
    expected_mb: float


def worker_megabytes(width: int, height: int, preset: str) -> float:
    """What one worker is planned to use, its encoder included.

    A preset this has no measurement for is planned as the dearest one measured.
    """
    base, per_megapixel = WORKER_COST.get(preset, WORKER_COST["medium"])
    return MARGIN * (base + per_megapixel * width * height / 1e6)


def plan_render(
    total_frames: int,
    requested: int,
    *,
    width: int,
    height: int,
    preset: str,
    cpus: int,
    available_mb: float | None,
) -> RenderPlan:
    """Choose a worker count that fits the cores, the memory, and the job.

    ``requested`` is `VisualConfig.workers`: 0 asks for one per core, 1 forces
    the single-process path, and anything else is an upper bound rather than an
    order. Memory can still lower it, because a render that swaps the machine
    to a standstill has not honoured anyone's request.

    ``available_mb`` of None means the machine would not say, and the plan
    assumes `ASSUMED_AVAILABLE_MB` rather than assuming there is no limit.
    """
    threads = ENCODER_THREADS
    each = worker_megabytes(width, height, preset)

    if requested == 1 or total_frames < MIN_FRAMES_TO_SPLIT:
        reason = "requested" if requested == 1 else "short"
        return RenderPlan(1, threads, reason, each)

    wanted = requested or max(1, cpus)
    reason = "requested" if requested else "cores"
    if wanted > MAX_WORKERS:
        wanted, reason = MAX_WORKERS, "cap"

    # Each worker should get a fair number of frames, or it is all start-up.
    fits_frames = max(1, total_frames // (MIN_FRAMES_TO_SPLIT // 2))
    if fits_frames < wanted:
        wanted, reason = fits_frames, "short"

    free = ASSUMED_AVAILABLE_MB if available_mb is None else available_mb
    fits_memory = max(1, int(free * MEMORY_SHARE // each))
    if fits_memory < wanted:
        wanted, reason = fits_memory, "memory"

    return RenderPlan(wanted, threads, reason, wanted * each)


def logical_cpus() -> int:
    """Logical CPUs this process may run on."""
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


def available_memory_mb() -> float | None:
    """Memory the system could hand out now without swapping, in megabytes.

    None when it cannot be found out, which the planner treats as "assume
    little" rather than "assume plenty".
    """
    try:
        if sys.platform == "win32":
            found = _windows_available()
        elif sys.platform == "darwin":
            found = _mac_available()
        else:
            found = _linux_available()
    except (OSError, ValueError, AttributeError):
        return None
    return found


def _windows_available() -> float:
    # An if/else on the platform, so mypy checks whichever branch is real and
    # skips the other, rather than flagging one of them as unreachable.
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return float(status.available_physical) / 2**20
    else:  # pragma: no cover
        raise OSError("not Windows")


def _linux_available() -> float:
    with Path("/proc/meminfo").open(encoding="ascii") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    raise ValueError("no MemAvailable in /proc/meminfo")


def _mac_available() -> float:
    """Half of physical memory: a cautious stand-in, not a measurement.

    macOS has no counterpart to MemAvailable short of parsing `vm_stat`, and it
    keeps most otherwise idle memory as cache, so a "free" figure would read low
    anyway. Untested on a Mac.
    """
    if sys.platform == "win32":  # pragma: no cover
        raise OSError("not macOS")
    else:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return float(total) / 2 / 2**20
