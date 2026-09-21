"""A render sizes itself to the machine, and does not take all of it.

A 4K render at a slower preset used to start eight workers, each with an x264
encoder running one and a half threads per core. A `medium` worker was measured
at 1.6 GB at 4K with its threads capped to two, so eight need at least 12.8 GB,
and the 16 GB Windows machine it was reported on had 6 to 8 GB free. It
stopped responding. These pin the three things that prevent it:
workers are counted against free memory, encoders share the cores rather than
multiplying them, and everything a render starts runs below normal priority.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pytest

from psv.render.encoder import Encoder, ffmpeg_command, lower_own_priority
from psv.render.resources import (
    ASSUMED_AVAILABLE_MB,
    ENCODER_THREADS,
    MAX_WORKERS,
    MEMORY_SHARE,
    MIN_FRAMES_TO_SPLIT,
    RenderPlan,
    available_memory_mb,
    plan_render,
    worker_megabytes,
)

LONG = 14_400  # four minutes at 60fps
SIZES = [(1280, 720), (1920, 1080), (3840, 2160)]
PRESETS = ["ultrafast", "veryfast", "medium"]


def plan(width: int, height: int, preset: str, free_mb: float | None) -> RenderPlan:
    return plan_render(
        LONG,
        0,
        width=width,
        height=height,
        preset=preset,
        cpus=12,
        available_mb=free_mb,
    )


@pytest.mark.feature("F-97")
def test_the_render_that_froze_the_machine_is_no_longer_planned() -> None:
    """4K60 at `medium` with the 6.3 GB that was free when it was measured."""
    chosen = plan(3840, 2160, "medium", 6300)
    assert chosen.workers < MAX_WORKERS
    assert chosen.limited_by == "memory"
    assert chosen.expected_mb <= 6300


@pytest.mark.feature("F-97")
@pytest.mark.parametrize("free_mb", [1500.0, 4000.0, 9000.0, 30000.0])
@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize(("width", "height"), SIZES)
def test_a_plan_stays_inside_its_share_of_free_memory(
    width: int, height: int, preset: str, free_mb: float
) -> None:
    """Unless even one worker does not fit, which is still better than none."""
    chosen = plan(width, height, preset, free_mb)
    assert chosen.workers >= 1
    if chosen.workers > 1:
        assert chosen.expected_mb <= free_mb * MEMORY_SHARE


@pytest.mark.feature("F-97")
def test_more_free_memory_never_means_fewer_workers() -> None:
    counts = [plan(3840, 2160, "medium", mb).workers for mb in range(1000, 40000, 500)]
    assert counts == sorted(counts)


@pytest.mark.feature("F-97")
def test_a_slower_preset_is_planned_as_costing_more() -> None:
    """`medium` keeps forty frames of lookahead that `ultrafast` does not."""
    cheap, dear = (worker_megabytes(3840, 2160, p) for p in ("ultrafast", "medium"))
    assert dear > cheap


@pytest.mark.feature("F-97")
def test_unknown_memory_is_treated_as_scarce_not_as_unlimited() -> None:
    assert plan(3840, 2160, "medium", None) == plan(
        3840, 2160, "medium", ASSUMED_AVAILABLE_MB
    )


@pytest.mark.feature("F-97")
def test_a_worker_request_is_an_upper_bound_that_memory_can_lower() -> None:
    chosen = plan_render(
        LONG,
        8,
        width=3840,
        height=2160,
        preset="medium",
        cpus=12,
        available_mb=4000,
    )
    assert chosen.workers < 8


@pytest.mark.feature("F-97")
def test_short_renders_and_one_worker_skip_the_planning() -> None:
    for total, requested in ((MIN_FRAMES_TO_SPLIT - 1, 0), (LONG, 1)):
        chosen = plan_render(
            total,
            requested,
            width=3840,
            height=2160,
            preset="medium",
            cpus=12,
            available_mb=1e9,
        )
        assert chosen.workers == 1


@pytest.mark.feature("F-97")
def test_every_encoder_is_given_a_thread_count() -> None:
    """Left unset, x264 starts one and a half threads per core in every encoder."""
    assert ENCODER_THREADS > 0
    command = ffmpeg_command(
        Path("out.mp4"),
        width=64,
        height=48,
        fps=10,
        preset="medium",
        crf=20,
        threads=ENCODER_THREADS,
    )
    assert command[command.index("-threads") + 1] == str(ENCODER_THREADS)


def test_this_machine_reports_its_free_memory() -> None:
    """Every CI platform can answer. None is allowed for, not expected."""
    found = available_memory_mb()
    assert found is not None
    assert found > 0


def _priority_of(pid: int) -> int:
    """Windows priority class, or POSIX nice value, of a process."""
    if sys.platform == "win32":
        import ctypes.wintypes

        wintypes = ctypes.wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetPriorityClass.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        try:
            found = int(kernel32.GetPriorityClass(handle))
        finally:
            kernel32.CloseHandle(handle)
    else:
        found = os.getpriority(os.PRIO_PROCESS, pid)
    return found


def _own_priority() -> int:
    lower_own_priority()
    return _priority_of(os.getpid())


@pytest.mark.feature("F-97")
def test_the_encoder_runs_below_normal_priority(tmp_path: Path) -> None:
    with Encoder(
        tmp_path / "out.mp4", width=64, height=48, fps=10, preset="ultrafast", crf=20
    ) as encoder:
        encoder.write(np.zeros((48, 64, 3), dtype=np.uint8))
        child = _priority_of(encoder._process.pid)
    if sys.platform == "win32":
        assert child == 0x00004000, "BELOW_NORMAL_PRIORITY_CLASS"
    else:
        assert child > os.getpriority(os.PRIO_PROCESS, 0)


@pytest.mark.feature("F-97")
def test_a_render_worker_lowers_its_own_priority() -> None:
    """Run in a spawned process, as a render worker is, so this one is untouched."""
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
        child = pool.submit(_own_priority).result()
    if sys.platform == "win32":
        assert child == 0x00004000
    else:
        assert child > os.getpriority(os.PRIO_PROCESS, 0)
