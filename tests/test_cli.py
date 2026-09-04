"""The CLI surface, driven the way a user drives it."""

from __future__ import annotations

import re
from argparse import Namespace
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


# -- global flags on either side of the subcommand -----------------------


@pytest.mark.feature("F-56")
def test_config_is_accepted_after_the_subcommand(
    tmp_path: Path, midi_path: Callable[[str], Path]
) -> None:
    """`psv -c x.toml run ...` worked and `psv run ... -c x.toml` did not.
    Nothing about the flag suggests that, and it caught a user twice."""
    config = tmp_path / "ok.toml"
    config.write_text("[hands]\nmax_span_semitones = 15\n", encoding="utf-8")
    path = midi_path("single-note")
    assert main(["inspect", str(path), "-c", str(config)]) == 0
    assert main(["-c", str(config), "inspect", str(path)]) == 0


@pytest.mark.feature("F-56")
def test_a_bad_config_is_caught_from_either_side(
    tmp_path: Path, midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[hands]\nmax_span_semitones = 99\n", encoding="utf-8")
    path = midi_path("single-note")
    assert main(["inspect", str(path), "-c", str(config)]) == 1
    assert "max_span_semitones" in capsys.readouterr().err


@pytest.mark.feature("F-56")
def test_verbose_after_the_subcommand_reaches_the_report(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The subcommand's copy of -v must not overwrite the top-level one with its
    own default, which is what argparse does unless the copy is SUPPRESSed."""
    path = midi_path("orchestral")
    assert main(["inspect", str(path), "-v"]) == 0
    after = capsys.readouterr().out

    assert main(["-v", "inspect", str(path)]) == 0
    before = capsys.readouterr().out

    assert "track 0" in after
    assert after == before


@pytest.mark.feature("F-56")
def test_no_verbose_anywhere_stays_quiet(
    midi_path: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["inspect", str(midi_path("orchestral"))]) == 0
    assert "track 0" not in capsys.readouterr().out


# -- instruments ---------------------------------------------------------


@pytest.mark.feature("F-57")
def test_instruments_lists_the_general_midi_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["instruments"]) == 0
    out = capsys.readouterr().out
    assert "Acoustic Grand Piano" in out
    assert "Church Organ" in out
    assert "worth trying on a piano piece" in out


@pytest.mark.feature("F-57")
def test_instruments_prefers_the_soundfonts_own_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A SoundFont may put anything at any program number, so its names are what
    will actually sound. GM is a convention, not a guarantee."""
    from tests.fixtures.soundfont_builder import SOUNDFONTS

    font = tmp_path / "tiny.sf2"
    font.write_bytes(SOUNDFONTS["named-presets"]())
    config = tmp_path / "sf.toml"
    config.write_text(
        f'[audio]\nbackend = "builtin"\nsoundfont = "{font.as_posix()}"\n',
        encoding="utf-8",
    )

    assert main(["-c", str(config), "instruments"]) == 0
    out = capsys.readouterr().out
    assert "Test Piano" in out
    assert "Test Rhodes" in out
    assert "Acoustic Grand Piano" not in out


@pytest.mark.feature("F-57")
def test_an_unreadable_soundfont_falls_back_to_general_midi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the command is to answer the question. A broken file is a
    reason to say so and answer it a worse way, not to answer nothing."""
    from tests.fixtures.soundfont_builder import SOUNDFONTS

    font = tmp_path / "broken.sf2"
    font.write_bytes(SOUNDFONTS["not-a-soundfont"]())
    config = tmp_path / "sf.toml"
    config.write_text(
        f'[audio]\nbackend = "builtin"\nsoundfont = "{font.as_posix()}"\n',
        encoding="utf-8",
    )

    assert main(["-c", str(config), "instruments"]) == 0
    captured = capsys.readouterr()
    assert "could not read" in captured.err
    assert "Acoustic Grand Piano" in captured.out


# -- presets -------------------------------------------------------------


@pytest.mark.feature("F-59")
def test_presets_describes_what_each_one_changes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A preset you have to render something to understand is not a shortcut."""
    assert main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "small-hands" in out
    assert "hands.max_span_semitones = 9" in out


@pytest.mark.feature("F-80")
def test_the_reverb_flag_beats_the_config_file() -> None:
    """A flag beats the file, as every other override does."""
    from psv.cli import _audio_with_overrides
    from psv.config import Config

    config = Config.load(None)
    assert _audio_with_overrides(config.audio, Namespace(reverb=0.9)).reverb == 0.9
    assert _audio_with_overrides(config.audio, Namespace(reverb=None)) is config.audio


@pytest.mark.feature("F-80")
def test_an_out_of_range_reverb_flag_is_refused(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    argv = ["run", str(midi_path("single-note")), "-o", str(tmp_path / "out.mp4")]
    assert main([*argv, "--reverb", "3"]) == 1


@pytest.mark.feature("F-74")
def test_presets_describes_the_themes_too(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One command for both, since the question is the same: what does this do
    to my render, without rendering something to find out."""
    assert main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "themes (--theme)" in out
    assert "neon" in out
    assert "visual.gradient_top" in out


@pytest.mark.feature("F-74")
def test_a_theme_is_accepted_on_either_side_of_the_subcommand(
    midi_path: Callable[[str], Path],
) -> None:
    path = str(midi_path("single-note"))
    assert main(["--theme", "neon", "inspect", path]) == 0
    assert main(["inspect", path, "--theme", "neon"]) == 0


@pytest.mark.feature("F-74")
def test_an_unknown_theme_is_a_usage_error(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit):
        main(["--theme", "chartreuse", "inspect", str(midi_path("single-note"))])


@pytest.mark.feature("F-59")
def test_a_preset_changes_the_settings_it_names(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    from psv.constraints import verify_span

    output = tmp_path / "small.mid"
    assert (
        main(
            [
                "--preset",
                "small-hands",
                "constrain",
                str(midi_path("wide-span-chord")),
                "-o",
                str(output),
            ]
        )
        == 0
    )
    assert verify_span(read_midi_file(output), 9) == []


@pytest.mark.feature("F-59")
def test_a_flag_beats_a_preset(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Least specific to most: the config file, then the preset, then the flag."""
    output = tmp_path / "out.mid"
    assert (
        main(
            [
                "--preset",
                "small-hands",
                "constrain",
                str(midi_path("wide-span-chord")),
                "-o",
                str(output),
                "--span",
                "0",
            ]
        )
        == 0
    )
    assert "span not enforced" in capsys.readouterr().out


@pytest.mark.feature("F-59")
def test_an_unknown_preset_is_a_usage_error(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--preset", "nonsense", "inspect", str(midi_path("single-note"))])
    assert exc.value.code == 2


@pytest.mark.feature("F-59")
def test_every_preset_applies_cleanly() -> None:
    """PRESETS names config sections and fields as plain strings, so a typo is
    invisible to mypy and only shows up when someone runs that preset. Only
    `small-hands` is exercised above; this covers the rest."""
    from psv.config import Config
    from psv.presets import DESCRIPTIONS, PRESETS, apply_preset

    base = Config.load(None)
    for name in PRESETS:
        applied = apply_preset(base, name)
        applied.validate()
        assert applied != base, f"preset {name!r} changed nothing"

    assert set(DESCRIPTIONS) == set(PRESETS), "every preset needs a description"


@pytest.mark.feature("F-59")
def test_an_unknown_preset_names_the_real_ones() -> None:
    from psv.config import Config, ConfigError
    from psv.presets import apply_preset

    with pytest.raises(ConfigError, match="small-hands"):
        apply_preset(Config.load(None), "nonsense")


@pytest.mark.feature("F-57")
def test_control_characters_are_stripped_from_preset_names(tmp_path: Path) -> None:
    """Preset names come from an untrusted file and go straight to a terminal.
    An escape sequence could recolour it and a carriage return could overwrite
    the line above, hiding an entry."""
    from psv.instruments import soundfont_presets
    from tests.fixtures.soundfont_builder import minimal_soundfont

    font = tmp_path / "evil.sf2"
    font.write_bytes(minimal_soundfont([(0, 0, "\x1b[31mRED\x1b[0m\rHIDDEN")]))

    name = soundfont_presets(font)[0].name
    assert "\x1b" not in name
    assert "\r" not in name
    assert "RED" in name


@pytest.mark.feature("F-70")
def test_render_honours_the_encode_override(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """`fast` spends less time compressing, so it writes a bigger file for the
    same pictures. That is the only way to see from outside that the flag
    arrived."""
    sizes = {}
    for level in ("small", "fast"):
        output = tmp_path / f"{level}.mp4"
        assert (
            main(
                [
                    "render",
                    str(midi_path("two-hands")),
                    "-o",
                    str(output),
                    "--seconds",
                    "2",
                    "--width",
                    "160",
                    "--height",
                    "120",
                    "--fps",
                    "20",
                    "--encode",
                    level,
                    "--workers",
                    "1",
                ]
            )
            == 0
        )
        sizes[level] = output.stat().st_size
    assert sizes["fast"] > sizes["small"]


@pytest.mark.feature("F-69")
def test_render_honours_the_worker_override(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """Two workers must produce a video of the same shape as one."""
    outputs = {}
    for workers in (1, 2):
        output = tmp_path / f"w{workers}.mp4"
        assert (
            main(
                [
                    "render",
                    str(midi_path("two-hands")),
                    "-o",
                    str(output),
                    "--seconds",
                    "24",
                    "--width",
                    "64",
                    "--height",
                    "48",
                    "--fps",
                    "10",
                    "--workers",
                    str(workers),
                ]
            )
            == 0
        )
        outputs[workers] = output
    assert video_meta(outputs[2])["size"] == video_meta(outputs[1])["size"]
    assert video_meta(outputs[2])["duration"] == pytest.approx(
        video_meta(outputs[1])["duration"], abs=0.05
    )


@pytest.mark.feature("F-70")
def test_an_unknown_encode_level_is_refused_at_the_command_line(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "render",
                str(midi_path("single-note")),
                "-o",
                str(tmp_path / "x.mp4"),
                "--encode",
                "tiny",
            ]
        )
