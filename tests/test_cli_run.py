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
from psv.render.video import TAIL_S
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import video_meta

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
    output = tmp_path / "practice.mp4"
    code = main(["run", str(midi_path("orchestral")), "-o", str(output), *TINY])
    assert code == 0
    assert output.exists()

    out = capsys.readouterr().out
    assert "arrange" in out
    assert "constrain" in out
    assert "audio" in out
    assert "wrote" in out

    meta = video_meta(output)
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


# -- practice ------------------------------------------------------------

#: Small, and without --seconds, so the practice flags decide the length.
SIZE = ["--width", "160", "--height", "120", "--fps", "10"]


@pytest.mark.feature("F-51")
def test_a_slower_tempo_makes_a_longer_video(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """The one thing --tempo has to do: the same piece, taking longer."""
    source = midi_path("two-hands")
    full = tmp_path / "full.mp4"
    half = tmp_path / "half.mp4"

    assert main(["run", str(source), "-o", str(full), *SIZE]) == 0
    assert main(["run", str(source), "-o", str(half), "--tempo", "0.5", *SIZE]) == 0

    # Both carry the same one-second tail after the last note, so it is the
    # music either side of that which has to have doubled.
    music = video_meta(full)["duration"] - TAIL_S
    assert video_meta(half)["duration"] - TAIL_S == pytest.approx(music * 2, abs=0.2)


@pytest.mark.feature("F-51")
def test_the_tempo_flag_does_not_change_the_arrangement(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Practising at half speed has to be practising the same piece. The
    constraint engine measures overlaps in real seconds, so an arrangement that
    changed with the practice tempo would be a real bug, not a nicety."""
    source = midi_path("orchestral")
    reports = []
    for speed in ("1.0", "0.5"):
        main(
            [
                "run",
                str(source),
                "-o",
                str(tmp_path / f"{speed}.mp4"),
                "--tempo",
                speed,
                "--seconds",
                "1",
                *SIZE,
            ]
        )
        out = capsys.readouterr().out
        reports.append(
            [
                line
                for line in out.splitlines()
                if line.startswith("  ") and "practice" not in line
            ]
        )
    assert reports[0] == reports[1]


@pytest.mark.feature("F-52")
def test_a_bar_range_renders_only_those_bars(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    """Two bars of four-four at 120 bpm is four seconds, plus the tail."""
    output = tmp_path / "section.mp4"
    assert (
        main(
            [
                "run",
                str(midi_path("two-hands")),
                "-o",
                str(output),
                "--bars",
                "2-3",
                *SIZE,
            ]
        )
        == 0
    )
    assert video_meta(output)["duration"] == pytest.approx(5.0, abs=0.2)


@pytest.mark.feature("F-52")
def test_a_single_bar_number_is_accepted(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "bar.mp4"
    assert (
        main(
            [
                "run",
                str(midi_path("two-hands")),
                "-o",
                str(output),
                "--bars",
                "3",
                *SIZE,
            ]
        )
        == 0
    )
    assert "bar 3" in capsys.readouterr().out


@pytest.mark.feature("F-52")
def test_bars_and_seconds_cannot_both_be_given(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two ways of saying the same thing. Better to ask than to guess which."""
    code = main(
        [
            "run",
            str(midi_path("two-hands")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--bars",
            "2-3",
            "--seconds",
            "1",
        ]
    )
    assert code == 1
    assert "--bars cannot be combined with --seconds" in capsys.readouterr().err


@pytest.mark.feature("F-52")
def test_a_malformed_bar_range_is_a_usage_error(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(midi_path("two-hands")), "-o", "x.mp4", "--bars", "40-20"])
    assert exc.value.code == 2


@pytest.mark.feature("F-53")
def test_a_count_in_lengthens_the_video_by_a_bar(
    midi_path: Callable[[str], Path], tmp_path: Path
) -> None:
    plain = tmp_path / "plain.mp4"
    counted = tmp_path / "counted.mp4"
    source = midi_path("two-hands")

    main(["run", str(source), "-o", str(plain), "--bars", "2-2", *SIZE])
    main(
        [
            "run",
            str(source),
            "-o",
            str(counted),
            "--bars",
            "2-2",
            "--count-in",
            "1",
            *SIZE,
        ]
    )

    added = video_meta(counted)["duration"] - video_meta(plain)["duration"]
    assert added == pytest.approx(2.0, abs=0.2)


@pytest.mark.feature("F-53")
def test_the_metronome_is_reported_and_needs_sound(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`render` writes a silent video, so accepting --metronome without saying
    anything would look like the clicks went missing."""
    code = main(
        [
            "render",
            str(midi_path("two-hands")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--metronome",
            "--seconds",
            "1",
            *SIZE,
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert "use `psv run`" in captured.err
    assert "metronome" in captured.out


@pytest.mark.feature("F-54")
def test_one_hand_still_shows_both(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "left.mp4"
    code = main(
        [
            "run",
            str(midi_path("two-hands")),
            "-o",
            str(output),
            "--hands",
            "left",
            "--seconds",
            "1",
            *SIZE,
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "left hand" in out
    # Every note is still in the score being drawn; only the sound is filtered.
    assert "notes            24" in out


@pytest.mark.feature("F-54")
def test_an_unknown_hand_is_a_usage_error(midi_path: Callable[[str], Path]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["run", str(midi_path("two-hands")), "-o", "x.mp4", "--hands", "third"])
    assert exc.value.code == 2


@pytest.mark.feature("F-51")
def test_practice_settings_can_come_from_the_config_file(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Everything a flag can say, a config file can say too."""
    config = tmp_path / "practice.toml"
    config.write_text(
        "[practice]\ntempo = 0.5\nhands = 'right'\ncount_in_bars = 1\n",
        encoding="utf-8",
    )
    code = main(
        [
            "-c",
            str(config),
            "run",
            str(midi_path("two-hands")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--seconds",
            "1",
            *SIZE,
        ]
    )
    assert code == 0
    assert "0.5x tempo, right hand, 1-bar count-in" in capsys.readouterr().out


@pytest.mark.feature("F-51")
def test_a_flag_overrides_the_config_file(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "practice.toml"
    config.write_text("[practice]\ntempo = 0.5\n", encoding="utf-8")
    main(
        [
            "-c",
            str(config),
            "run",
            str(midi_path("two-hands")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--tempo",
            "0.25",
            "--seconds",
            "1",
            *SIZE,
        ]
    )
    assert "0.25x tempo" in capsys.readouterr().out


def test_an_out_of_range_tempo_is_reported(
    midi_path: Callable[[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "run",
            str(midi_path("two-hands")),
            "-o",
            str(tmp_path / "out.mp4"),
            "--tempo",
            "99",
        ]
    )
    assert code == 1
    assert "practice.tempo" in capsys.readouterr().err


@pytest.mark.feature("F-54")
def test_asking_for_a_hand_with_no_notes_warns_rather_than_failing(
    midi_path: Callable[[str], Path],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A one-line melody arranges onto one hand, so the other really is empty.
    A silent soundtrack is the honest answer; saying nothing is not."""
    output = tmp_path / "out.mp4"
    with caplog.at_level("WARNING"):
        code = main(
            [
                "run",
                str(midi_path("single-note")),
                "-o",
                str(output),
                "--hands",
                "left",
                *SIZE,
            ]
        )
    assert code == 0
    assert output.exists()
    assert "no notes are assigned to the left hand" in caplog.text


@pytest.mark.feature("F-55")
def test_no_span_limit_changes_nothing_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unplayable arrangement must never be a surprise, so asking for no
    limit has to be loud about what it did not do."""
    from psv.midi import read_midi_file

    source = (
        Path(__file__).resolve().parent
        / "assets"
        / "public-domain"
        / "bach-bwv565-toccata-and-fugue.mid"
    )
    output = tmp_path / "as-written.mid"
    assert main(["constrain", str(source), "-o", str(output), "--span", "0"]) == 0

    out = capsys.readouterr().out
    assert "span not enforced" in out

    before, after = read_midi_file(source), read_midi_file(output)
    assert len(after.notes) == len(before.notes)
    assert not any(note.was_edited for note in after.notes)


@pytest.mark.feature("F-55")
def test_the_span_flag_beats_the_config_file(
    tmp_path: Path, midi_path: Callable[[str], Path]
) -> None:
    from psv.constraints import verify_span

    config = tmp_path / "wide.toml"
    config.write_text("[hands]\nmax_span_semitones = 18\n", encoding="utf-8")
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
                "--span",
                "5",
            ]
        )
        == 0
    )
    assert verify_span(read_midi_file(output), 5) == []
