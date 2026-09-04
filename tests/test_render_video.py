"""Video encoding.

Small and short on purpose: these run in CI on every push, and the thing being
tested is that frames reach ffmpeg intact and come back out at the size and
length asked for, not that a four-minute render looks nice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from psv.config import VisualConfig
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Score
from psv.render.frame import Frame
from psv.render.video import (
    MAX_WORKERS,
    MIN_FRAMES_TO_SPLIT,
    TAIL_S,
    VideoWriteError,
    frame_times,
    iter_frames,
    render_video,
    worker_count,
)
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import decoded_frames, frame_count, video_meta

TINY = VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0)


# -- frame timing --------------------------------------------------------


def test_frame_times_are_evenly_spaced() -> None:
    assert list(frame_times(1.0, 4)) == pytest.approx([0.0, 0.25, 0.5, 0.75])


def test_frame_times_start_where_asked() -> None:
    assert list(frame_times(0.5, 4, start=10.0)) == pytest.approx([10.0, 10.25])


def test_frame_times_do_not_drift_over_a_long_render() -> None:
    """Computed from the index rather than by repeated addition, so a long
    render cannot accumulate rounding error."""
    times = list(frame_times(600.0, 60))
    assert len(times) == 36_000
    assert times[-1] == pytest.approx(599.9833333, abs=1e-6)


def test_a_zero_length_render_still_produces_one_frame() -> None:
    assert len(list(frame_times(0.0, 30))) == 1


def test_frame_times_rejects_a_nonpositive_rate() -> None:
    with pytest.raises(ValueError, match="fps must be positive"):
        list(frame_times(1.0, 0))


# -- frame generation ----------------------------------------------------


@pytest.mark.feature("F-14")
def test_iter_frames_yields_the_expected_count_and_shape() -> None:
    score = read_midi(FIXTURES["two-hands"]())
    frames = list(iter_frames(score, TINY, duration=1.0))
    assert len(frames) == 10
    assert all(f.shape == (120, 160, 3) for f in frames)
    assert all(f.dtype == np.uint8 for f in frames)


@pytest.mark.feature("F-14")
def test_the_default_duration_covers_the_piece_plus_a_tail() -> None:
    """Without the tail the last bar is cut off the instant it lands."""
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=2.0),)),))
    frames = list(iter_frames(score, TINY))
    assert len(frames) == round((2.0 + TAIL_S) * TINY.fps)


# -- encoding ------------------------------------------------------------


@pytest.mark.feature("F-14")
def test_a_video_is_written_at_the_requested_size_and_length(tmp_path: Path) -> None:
    score = read_midi(FIXTURES["dynamic-levels"]())
    path = render_video(score, TINY, tmp_path / "out.mp4", duration=2.0)

    assert path.exists()
    assert path.stat().st_size > 0

    meta = video_meta(path)
    assert meta["size"] == (160, 120)
    assert meta["fps"] == pytest.approx(10.0)
    assert frame_count(path) == 20


@pytest.mark.feature("F-14")
def test_odd_frame_sizes_are_rejected_rather_than_silently_padded() -> None:
    """imageio pads to a multiple of the macro block unless told not to, which
    would hand back a video that is not the size that was asked for."""
    from psv.config import ConfigError

    with pytest.raises(ConfigError, match="must be even"):
        replace(TINY, height=121).validate()


@pytest.mark.feature("F-14")
def test_the_output_directory_is_created(tmp_path: Path) -> None:
    score = read_midi(FIXTURES["single-note"]())
    path = render_video(score, TINY, tmp_path / "a" / "b" / "out.mp4", duration=0.5)
    assert path.exists()


@pytest.mark.feature("F-14")
def test_rendering_from_a_start_offset_shortens_nothing(tmp_path: Path) -> None:
    score = read_midi(FIXTURES["tempo-changes"]())
    path = render_video(score, TINY, tmp_path / "out.mp4", start=4.0, duration=1.0)
    assert frame_count(path) == 10


@pytest.mark.feature("F-14")
def test_progress_is_reported_for_every_frame(tmp_path: Path) -> None:
    seen: list[tuple[int, int]] = []
    score = read_midi(FIXTURES["single-note"]())
    render_video(
        score,
        TINY,
        tmp_path / "out.mp4",
        duration=1.0,
        on_frame=lambda done, total: seen.append((done, total)),
    )
    assert [done for done, _ in seen] == list(range(1, 11))
    assert {total for _, total in seen} == {10}


@pytest.mark.feature("F-14")
def test_an_empty_score_still_encodes(tmp_path: Path) -> None:
    path = render_video(Score(), TINY, tmp_path / "out.mp4", duration=0.5)
    assert frame_count(path) == 5


@pytest.mark.feature("F-14")
def test_an_unwritable_destination_raises_a_video_error(tmp_path: Path) -> None:
    """A directory where a file should go. The failure must be the project's
    own error type, not a raw ffmpeg traceback."""
    blocked = tmp_path / "out.mp4"
    blocked.mkdir()
    score = read_midi(FIXTURES["single-note"]())
    with pytest.raises(VideoWriteError):
        render_video(score, TINY, blocked, duration=0.5)


@pytest.mark.feature("F-14")
def test_a_real_song_renders_end_to_end(tmp_path: Path) -> None:
    """The committed public-domain organ piece, so this runs offline in CI."""
    from psv.midi import read_midi_file

    source = (
        Path(__file__).resolve().parent
        / "assets"
        / "public-domain"
        / "bach-bwv565-toccata-and-fugue.mid"
    )
    score = read_midi_file(source)
    path = render_video(score, TINY, tmp_path / "bach.mp4", start=30.0, duration=2.0)
    assert frame_count(path) == 20
    assert path.stat().st_size > 1000


@pytest.mark.feature("F-14")
def test_an_unwritable_destination_fails_before_ffmpeg_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting ffmpeg discover the bad path leaks its stdin.

    imageio-ffmpeg closes that pipe only while the process is still alive, and
    one that failed to open its output has already exited, so the pipe falls to
    the garbage collector and surfaces on POSIX as a ResourceWarning charged to
    some unrelated later test. The only fix available from outside the library
    is not to start ffmpeg at all, so that is what this pins.
    """
    import imageio_ffmpeg

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("ffmpeg was started for a destination it cannot open")

    monkeypatch.setattr(imageio_ffmpeg, "write_frames", fail)

    blocked = tmp_path / "out.mp4"
    blocked.mkdir()
    score = read_midi(FIXTURES["single-note"]())
    with pytest.raises(VideoWriteError, match="could not write"):
        render_video(score, TINY, blocked, duration=0.5)


# -- rendering across processes ------------------------------------------
#
# The claim is that splitting the timeline changes how long a render takes and
# nothing else. Speed was measured once by hand rather than being asserted
# here, because a timing test on a shared CI runner proves nothing. What is
# asserted is sameness, which is the half that can go wrong quietly.


def long_enough() -> tuple[Score, VisualConfig, float]:
    """A render with enough frames to be worth splitting, and small enough to
    run on every push."""
    config = replace(TINY, width=64, height=48)
    seconds = MIN_FRAMES_TO_SPLIT / config.fps
    notes = tuple(
        Note(pitch=60 + (i % 13), start=i * 0.5, end=i * 0.5 + 0.4)
        for i in range(int(seconds * 2))
    )
    return (
        Score(parts=(Part(notes=notes, name="right", hand=Hand.RIGHT),)),
        config,
        (seconds),
    )


@pytest.mark.feature("F-69")
def test_worker_count_leaves_short_renders_alone() -> None:
    """A worker pays for an interpreter and an ffmpeg process before it draws
    anything. Below a few hundred frames that costs more than it saves."""
    assert worker_count(0, MIN_FRAMES_TO_SPLIT - 1) == 1
    assert worker_count(8, MIN_FRAMES_TO_SPLIT - 1) == 1


@pytest.mark.feature("F-69")
def test_worker_count_honours_one_and_caps_the_rest() -> None:
    assert worker_count(1, 100_000) == 1, "1 must mean the single-process path"
    assert worker_count(0, 100_000) >= 1
    assert worker_count(64, 100_000) == MAX_WORKERS
    # Never more workers than there is work to give them.
    assert worker_count(64, MIN_FRAMES_TO_SPLIT) == 2


@pytest.mark.feature("F-69")
def test_every_split_covers_the_frames_exactly_once() -> None:
    """The arithmetic behind the whole feature. A span beginning at frame k
    must produce the same timestamps as counting from zero, including when the
    frames do not divide evenly by the number of workers."""
    for total in (240, 1000, 9410):
        for workers in (2, 3, 7, 8):
            covered: list[int] = []
            for index in range(workers):
                first = index * total // workers
                last = (index + 1) * total // workers
                covered.extend(range(first, last))
            assert covered == list(range(total)), f"{total} frames, {workers} workers"


@pytest.mark.feature("F-69")
def test_a_parallel_render_is_the_same_video(tmp_path: Path) -> None:
    """Same length, same size, same number of frames. Run with two workers
    rather than one per core, since CI machines vary and the property does
    not."""
    score, config, seconds = long_enough()

    serial = render_video(
        score, replace(config, workers=1), tmp_path / "serial.mp4", duration=seconds
    )
    parallel = render_video(
        score, replace(config, workers=2), tmp_path / "parallel.mp4", duration=seconds
    )

    assert frame_count(parallel) == frame_count(serial)
    assert video_meta(parallel)["size"] == video_meta(serial)["size"]
    assert video_meta(parallel)["duration"] == pytest.approx(
        video_meta(serial)["duration"], abs=0.05
    )


@pytest.mark.feature("F-69")
def test_a_parallel_render_draws_the_same_pictures(tmp_path: Path) -> None:
    """Sameness where it actually matters.

    The files cannot be compared byte for byte: each span is an independent
    h264 encode with its own keyframes. So they are compared against the
    frames `render_frame` drew, and the parallel render has to be no further
    from those than the serial one is. h264 is lossy, and that loss is the
    scale everything else is measured against.
    """
    score, config, seconds = long_enough()
    serial = render_video(
        score, replace(config, workers=1), tmp_path / "serial.mp4", duration=seconds
    )
    parallel = render_video(
        score, replace(config, workers=2), tmp_path / "parallel.mp4", duration=seconds
    )

    drawn = list(iter_frames(score, config, duration=seconds))
    serial_error = _mean_error(drawn, serial, config)
    parallel_error = _mean_error(drawn, parallel, config)

    assert parallel_error <= serial_error * 1.5, (
        f"parallel render is further from what was drawn ({parallel_error:.3f}) "
        f"than the serial one is ({serial_error:.3f})"
    )


@pytest.mark.feature("F-79")
def test_consecutive_grey_levels_survive_the_round_trip(tmp_path: Path) -> None:
    """The bug this exists to prevent, at the level it happens.

    h264 defaults to the television range, 16-235, so about one grey level in
    seven has nowhere to land and consecutive levels collapse into one. Nothing
    notices until something moves slowly across a large flat area: the `pulse`
    effect walks the background up a level at a time, and a smooth brighten came
    back as an uneven stutter with a level repeated here and two skipped there.

    Written as a sequence of flat frames rather than as a render, because the
    property belongs to the writer and this way the expected answer is exact.
    """
    from psv.render.video import _open_writer

    levels = list(range(16, 40))
    config = VisualConfig(width=160, height=90, fps=10, encode="small")
    path = tmp_path / "greys.mp4"

    writer = _open_writer(config, path)
    try:
        for level in levels:
            frame = np.full((config.height, config.width, 3), level, dtype=np.uint8)
            writer.send(np.ascontiguousarray(frame))
    finally:
        writer.close()

    decoded = decoded_frames(path, config.width, config.height)
    assert len(decoded) == len(levels)

    # The middle of each frame, away from any edge the encoder might ring at.
    middle = [
        int(np.median(frame[20:70, 30:130, 0].astype(np.int16))) for frame in decoded
    ]
    assert len(set(middle)) == len(levels), (
        f"levels collapsed: {levels} came back as {middle}"
    )
    assert middle == sorted(middle), f"levels came back out of order: {middle}"


def _mean_error(drawn: list[Frame], path: Path, config: VisualConfig) -> float:
    """Average absolute pixel difference between what was drawn and what the
    file decodes back to."""
    decoded = decoded_frames(path, config.width, config.height)
    assert decoded, f"decoded no frames from {path}"
    pairs = list(zip(drawn, decoded, strict=False))
    return sum(
        float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) for a, b in pairs
    ) / len(pairs)


@pytest.mark.feature("F-69")
def test_a_render_that_loses_frames_refuses_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silently short video is the worst outcome available here, so the
    parallel path counts the frames its workers report and refuses the file if
    they do not add up.

    Provoked by handing it one span short, since nothing reachable from outside
    can make a worker come back with the wrong number. The guard is a belt: the
    point of the test is that the belt is fastened.
    """
    from psv.render import video as video_module

    score, config, seconds = long_enough()
    real_spans = video_module._spans

    def one_short(*args: Any, **kwargs: Any) -> Any:
        return real_spans(*args, **kwargs)[:-1]

    monkeypatch.setattr(video_module, "_spans", one_short)

    with pytest.raises(VideoWriteError, match="quietly short"):
        render_video(
            score,
            replace(config, workers=2),
            tmp_path / "short.mp4",
            duration=seconds,
        )


@pytest.mark.feature("F-69")
def test_an_unwritable_destination_fails_before_any_work(tmp_path: Path) -> None:
    """Both paths check the output first, so a bad path says so instead of
    arriving as a page of ffmpeg stderr after a minute of rendering."""
    score, config, seconds = long_enough()
    blocked = tmp_path / "in-the-way"
    blocked.mkdir()

    for workers in (1, 2):
        with pytest.raises(VideoWriteError, match="could not write"):
            render_video(
                score, replace(config, workers=workers), blocked, duration=seconds
            )


@pytest.mark.feature("F-70")
def test_the_encode_setting_reaches_the_encoder(tmp_path: Path) -> None:
    """`fast` spends less time compressing, so it writes a bigger file for the
    same pictures. That is the whole trade, and it is the only way to see from
    outside that the setting arrived."""
    score, config, seconds = long_enough()

    small = render_video(
        score,
        replace(config, workers=1, encode="small"),
        tmp_path / "small.mp4",
        duration=seconds,
    )
    fast = render_video(
        score,
        replace(config, workers=1, encode="fast"),
        tmp_path / "fast.mp4",
        duration=seconds,
    )

    assert frame_count(fast) == frame_count(small)
    assert fast.stat().st_size > small.stat().st_size


@pytest.mark.feature("F-70")
def test_encode_names_map_to_x264_presets() -> None:
    assert VisualConfig(encode="small").encoder_preset == "medium"
    assert VisualConfig(encode="balanced").encoder_preset == "veryfast"
    assert VisualConfig(encode="fast").encoder_preset == "ultrafast"
