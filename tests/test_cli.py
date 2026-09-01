"""The CLI surface, driven the way a user drives it."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mido
import pytest

from psv import __version__
from psv.cli import IMPLEMENTED, PIPELINE_COMMANDS, build_parser, main
from psv.midi import read_midi_file
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import video_meta

#: Python 3.14 colours argparse output, and honours FORCE_COLOR even when
#: stdout is not a terminal. Tests care what the help *says*, not how it is
#: painted, so strip the escapes rather than depend on the environment.
_ANSI = re.compile(r"\[[0-9;]*m")


def plain(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture
def midi_path(tmp_path: Path) -> Callable[[str], Path]:
    def _write(name: str) -> Path:
        path = tmp_path / f"{name}.mid"
        FIXTURES[name]().save(path)
        return path

    return _write


# -- the parser ----------------------------------------------------------


def test_version_is_set() -> None:
    assert __version__


def test_parser_exposes_every_pipeline_command() -> None:
    parser = build_parser()
    for command in PIPELINE_COMMANDS:
        argv = [command, "song.mid"]
        if command in IMPLEMENTED and command != "inspect":
            argv += ["-o", "out.mid"]
        assert parser.parse_args(argv).command == command


def test_pipeline_commands_cover_every_stage() -> None:
    assert set(PIPELINE_COMMANDS) == {
        "inspect",
        "export",
        "arrange",
        "constrain",
        "render",
        "run",
    }


def test_bare_invocation_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: psv" in plain(capsys.readouterr().out)


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in plain(capsys.readouterr().out)


# -- inspect -------------------------------------------------------------


@pytest.mark.feature("F-08")
def test_inspect_prints_a_report(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(midi_path("sustain-pedal"))]) == 0
    out = capsys.readouterr().out
    assert "duration" in out
    assert "polyphony" in out
    assert "sustain" in out


@pytest.mark.feature("F-08")
def test_inspect_of_a_real_song_reports_its_shape(
    songs: dict[str, dict[str, Any]],
    load_song: Callable[[str], mido.MidiFile],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    load_song("toccata")  # skips when absent
    path = Path(songs["toccata"]["filename"])
    source = Path("tests/assets/public-domain") / path
    assert main(["inspect", str(source)]) == 0
    out = capsys.readouterr().out
    assert "3651" in out
    assert "needs the arrange stage" in out


@pytest.mark.feature("F-49")
def test_verbose_adds_the_per_track_breakdown(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    path = midi_path("orchestral")
    assert main(["inspect", str(path)]) == 0
    plain = capsys.readouterr().out

    assert main(["-v", "inspect", str(path)]) == 0
    verbose = capsys.readouterr().out

    assert len(verbose) > len(plain)
    assert "track 0" in verbose
    assert "track 0" not in plain


@pytest.mark.feature("F-50")
def test_an_empty_file_is_reported_not_crashed(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(midi_path("empty"))]) == 0
    assert "0 in 0 part(s)" in capsys.readouterr().out


@pytest.mark.feature("F-50")
def test_a_single_note_file_is_reported(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(midi_path("single-note"))]) == 0
    assert "1 in 1 part(s)" in capsys.readouterr().out


# -- export --------------------------------------------------------------


@pytest.mark.feature("F-09")
def test_export_writes_a_readable_midi(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    source = midi_path("two-hands")
    destination = tmp_path / "out" / "exported.mid"
    assert main(["export", str(source), "-o", str(destination)]) == 0
    assert destination.exists()

    original = read_midi_file(source)
    exported = read_midi_file(destination)
    assert len(exported.notes) == len(original.notes)


@pytest.mark.feature("F-09")
def test_export_requires_an_output_path(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["export", str(midi_path("single-note"))])
    assert exc.value.code == 2


# -- errors --------------------------------------------------------------


def test_a_missing_input_file_reports_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(tmp_path / "nope.mid")]) == 1
    assert "psv:" in capsys.readouterr().err


def test_a_bad_config_is_reported_before_any_work(
    tmp_path: Path, midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[hands]\nmax_span_semitones = 99\n", encoding="utf-8")
    code = main(["-c", str(config), "inspect", str(midi_path("single-note"))])
    assert code == 1
    assert "max_span_semitones" in capsys.readouterr().err


def test_a_valid_config_is_accepted(
    tmp_path: Path, midi_path: Callable[[str], Path]
) -> None:
    config = tmp_path / "ok.toml"
    config.write_text("[hands]\nmax_span_semitones = 15\n", encoding="utf-8")
    assert main(["-c", str(config), "inspect", str(midi_path("single-note"))]) == 0


# -- render --------------------------------------------------------------


@pytest.mark.feature("F-14")
def test_render_writes_a_video(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.mp4"
    code = main(
        [
            "render",
            str(midi_path("two-hands")),
            "-o",
            str(output),
            "--seconds",
            "1",
            "--width",
            "160",
            "--height",
            "120",
            "--fps",
            "10",
        ]
    )
    assert code == 0
    assert output.exists()
    assert "wrote" in capsys.readouterr().out


@pytest.mark.feature("F-14")
def test_render_honours_the_size_overrides(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """The overrides exist so the debug loop is seconds, not minutes. If they
    were ignored, every render would cost full 1080p time."""
    output = tmp_path / "out.mp4"
    main(
        [
            "render",
            str(midi_path("single-note")),
            "-o",
            str(output),
            "--seconds",
            "0.5",
            "--width",
            "240",
            "--height",
            "160",
            "--fps",
            "20",
        ]
    )
    meta = video_meta(output)
    assert meta["size"] == (240, 160)


@pytest.mark.feature("F-14")
def test_render_rejects_an_odd_frame_size(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "render",
            str(midi_path("single-note")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--seconds",
            "0.5",
            "--height",
            "121",
        ]
    )
    assert code == 1
    assert "must be even" in capsys.readouterr().err


@pytest.mark.feature("F-14")
def test_render_requires_an_output_path(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["render", str(midi_path("single-note"))])
    assert exc.value.code == 2


# -- constrain -----------------------------------------------------------


@pytest.mark.feature("F-20")
def test_constrain_writes_a_playable_midi(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from psv.config import Config
    from psv.constraints import verify_span

    output = tmp_path / "playable.mid"
    assert (
        main(["constrain", str(midi_path("wide-span-chord")), "-o", str(output)]) == 0
    )

    out = capsys.readouterr().out
    assert "violation" in out
    assert "wrote" in out

    result = read_midi_file(output)
    limit = Config.load(None).hands.max_span_semitones
    assert verify_span(result, limit) == []


@pytest.mark.feature("F-20")
def test_constrain_respects_the_configured_span(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    from psv.constraints import verify_span

    config = tmp_path / "narrow.toml"
    config.write_text("[hands]\nmax_span_semitones = 5\n", encoding="utf-8")
    output = tmp_path / "narrow.mid"

    assert (
        main(
            [
                "-c",
                str(config),
                "constrain",
                str(midi_path("wide-span-chord")),
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert verify_span(read_midi_file(output), 5) == []


@pytest.mark.feature("F-26")
def test_constrain_can_list_every_individual_repair(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auditability: -vv names what happened to each note, so a passage that
    comes out wrong can be traced to the decision that produced it."""
    main(
        [
            "-vv",
            "constrain",
            str(midi_path("wide-span-chord")),
            "-o",
            str(tmp_path / "out.mid"),
        ]
    )
    out = capsys.readouterr().out
    assert any(word in out for word in ("reassign", "octave-shift", "truncate", "drop"))


@pytest.mark.feature("F-47")
def test_stages_chain_through_intermediate_files(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """constrain then render, each run on its own, which is the documented way
    to hand-fix an intermediate and pick up from there."""
    from psv.constraints import has_hands

    constrained = tmp_path / "step1.mid"
    video = tmp_path / "step2.mp4"

    assert (
        main(["constrain", str(midi_path("orchestral")), "-o", str(constrained)]) == 0
    )
    assert has_hands(read_midi_file(constrained)), "hands must survive the hand-off"

    assert (
        main(
            [
                "render",
                str(constrained),
                "-o",
                str(video),
                "--seconds",
                "1",
                "--width",
                "160",
                "--height",
                "120",
                "--fps",
                "10",
            ]
        )
        == 0
    )
    assert video.exists()
