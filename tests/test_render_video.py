"""Video encoding.

Small and short on purpose: these run in CI on every push, and the thing being
tested is that frames reach ffmpeg intact and come back out at the size and
length asked for, not that a four-minute render looks nice.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from psv.config import VisualConfig
from psv.midi import read_midi
from psv.model import Note, Part, Score
from psv.render.video import (
    TAIL_S,
    VideoWriteError,
    frame_times,
    iter_frames,
    render_video,
)
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import frame_count, video_meta

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
