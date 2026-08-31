"""The `arrange` and `run` commands.

`run` is the one that matters for actually using the tool: one command, a MIDI
in, a video with sound out.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from psv.cli import IMPLEMENTED, main
from psv.constraints import has_hands
from psv.midi import read_midi_file
from tests.fixtures.midi_builder import FIXTURES

#: Small and short: these run on every push.
TINY = ["--seconds", "1", "--width", "160", "--height", "120", "--fps", "10"]


@pytest.fixture
def midi_path(tmp_path: Path) -> Callable[[str], Path]:
    def _write(name: str) -> Path:
        path = tmp_path / f"{name}.mid"
        FIXTURES[name]().save(path)
        return path

    return _write


def test_every_command_is_now_implemented() -> None:
    """Nothing in the pipeline is a placeholder any more."""
    from psv.cli import PIPELINE_COMMANDS

    assert set(PIPELINE_COMMANDS) == IMPLEMENTED


@pytest.mark.feature("F-44")
def test_arrange_writes_two_hands(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "arranged.mid"
    assert main(["arrange", str(midi_path("orchestral")), "-o", str(output)]) == 0
    assert "two hands" in capsys.readouterr().out
    assert has_hands(read_midi_file(output))


@pytest.mark.feature("F-48")
def test_run_turns_a_midi_into_a_video_with_sound(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import imageio_ffmpeg

    output = tmp_path / "practice.mp4"
    code = main(["run", str(midi_path("orchestral")), "-o", str(output), *TINY])
    assert code == 0
    assert output.exists()

    out = capsys.readouterr().out
    assert "arrange" in out
    assert "constrain" in out
    assert "audio" in out
    assert "wrote" in out

    reader = imageio_ffmpeg.read_frames(str(output))
    meta = next(reader)
    reader.close()
    assert meta["size"] == (160, 120)


@pytest.mark.feature("F-48")
def test_run_works_on_a_real_song(
    load_song: Callable[[str], object], tmp_path: Path
) -> None:
    """The committed public-domain organ piece, so this runs offline in CI."""
    load_song("toccata")  # skips when absent
    source = (
        Path(__file__).resolve().parent
        / "assets"
        / "public-domain"
        / "bach-bwv565-toccata-and-fugue.mid"
    )
    output = tmp_path / "bach.mp4"
    assert main(["run", str(source), "-o", str(output), "--start", "30", *TINY]) == 0
    assert output.stat().st_size > 5000


@pytest.mark.feature("F-48")
def test_run_respects_the_config(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    from psv.constraints import verify_span

    config = tmp_path / "narrow.toml"
    config.write_text(
        "[hands]\nmax_span_semitones = 7\n\n[audio]\nbackend = 'none'\n",
        encoding="utf-8",
    )
    output = tmp_path / "narrow.mp4"
    assert (
        main(
            [
                "-c",
                str(config),
                "run",
                str(midi_path("wide-span-chord")),
                "-o",
                str(output),
                *TINY,
            ]
        )
        == 0
    )
    assert output.exists()
    del verify_span


@pytest.mark.feature("F-48")
def test_run_requires_an_output_path(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(midi_path("single-note"))])
    assert exc.value.code == 2


@pytest.mark.feature("F-48")
def test_run_reports_a_bad_input_rather_than_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["run", str(tmp_path / "absent.mid"), "-o", str(tmp_path / "o.mp4")])
    assert code == 1
    assert "psv:" in capsys.readouterr().err
