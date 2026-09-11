"""Flags generated from the config dataclasses.

Two claims carry this module, and they are the two tests to keep if the rest
ever go.

`test_every_config_field_has_a_flag` is the one that makes the coverage a fact
rather than a habit. Thirty hand-written `add_argument` calls used to cover
about seventy settings, and nothing failed when the gap widened.

`test_a_value_layers_defaults_then_file_then_flag` is the one that keeps the
layering honest. Every flag defaults to None and None means leave what the file
said, which is exactly the rule a `TITLE_SECONDS` bug in a personal script
broke by defaulting a flag to a real value and stomping a TOML key with it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from psv.cli import build_parser, main
from psv.cliflags import CHOICES, HELP, SECTIONS_FOR, apply_overrides, settings
from psv.config import Config, ConfigError


def parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def flags_of(command: str) -> set[str]:
    """Every option string the command accepts.

    Reaches into argparse's internals, which is ugly and is the only way to ask
    a parser what it takes. Kept in one place for that reason.
    """
    for action in build_parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and command in choices:
            sub: argparse.ArgumentParser = choices[command]
            return {option for a in sub._actions for option in a.option_strings}
    raise AssertionError(f"no subcommand named {command!r}")


# -- coverage ------------------------------------------------------------


@pytest.mark.feature("F-92")
def test_every_config_field_has_a_flag() -> None:
    """Complete by construction, and asserted anyway.

    `settings()` is what the generator walks, so this checks the walk against
    the dataclasses rather than checking the generator against itself.
    """
    paths = {setting.path for setting in settings()}

    expected: set[str] = set()

    def walk(target: object, prefix: str) -> None:
        from dataclasses import fields, is_dataclass

        for field in fields(target):  # type: ignore[arg-type]
            value = getattr(target, field.name)
            path = f"{prefix}{field.name}"
            if is_dataclass(value) and not isinstance(value, type):
                walk(value, f"{path}.")
            elif isinstance(value, bool | int | float | str):
                expected.add(path)

    walk(Config(), "")
    assert paths == expected


@pytest.mark.feature("F-92")
def test_every_setting_is_offered_by_at_least_one_command() -> None:
    """A flag no command accepts is a flag nobody can use."""
    offered = {section for sections in SECTIONS_FOR.values() for section in sections}
    orphans = {s.path for s in settings() if s.section not in offered}
    assert not orphans, f"settings with nowhere to be set: {sorted(orphans)}"


@pytest.mark.feature("F-92")
def test_run_offers_every_section() -> None:
    """The whole pipeline reads the whole config, so it takes all of it."""
    sections = {setting.section for setting in settings()}
    assert set(SECTIONS_FOR["run"]) == sections


@pytest.mark.feature("F-92")
def test_a_command_does_not_offer_flags_it_could_not_act_on() -> None:
    """`psv constrain --visual-fps 30` is a usage error, not a silent no-op.

    Accepting a flag that cannot have an effect is the failure this avoids:
    nothing says so, and the setting looks like it was applied.
    """
    assert "--visual-fps" not in flags_of("constrain")
    assert "--span" not in flags_of("render")
    with pytest.raises(SystemExit):
        parse(["constrain", "x.mid", "-o", "y.mid", "--visual-fps", "30"])


# -- the layering --------------------------------------------------------


@pytest.mark.feature("F-92")
def test_a_value_layers_defaults_then_file_then_flag(tmp_path: Path) -> None:
    """Three sources, least specific first, each overriding only what it names."""
    path = tmp_path / "psv.toml"
    path.write_text("[visual]\nwidth = 640\nfps = 24\n", encoding="utf-8")

    default = Config.load(None)
    assert default.visual.width == 1920
    assert default.visual.fps == 60

    from_file = Config.load(path)
    assert from_file.visual.width == 640
    assert from_file.visual.fps == 24

    args = parse(["render", "x.mid", "-o", "y.mp4", "--width", "320"])
    with_flag = apply_overrides(from_file, args)
    assert with_flag.visual.width == 320, "the flag beats the file"
    assert with_flag.visual.fps == 24, "a setting no flag named keeps the file's value"
    assert with_flag.visual.height == 1080, "and one neither named keeps the default"


@pytest.mark.feature("F-92")
def test_an_absent_flag_changes_nothing(tmp_path: Path) -> None:
    """None means leave it, for every flag, including the booleans.

    A boolean is the one that would bite: `store_true` defaulting to False
    reads as "the user asked for false" and silently turns a TOML `true` off.
    """
    path = tmp_path / "psv.toml"
    path.write_text(
        "[practice]\nmetronome = true\ncount_in_clicks = false\n", encoding="utf-8"
    )
    config = Config.load(path)

    args = parse(["run", "x.mid", "-o", "y.mp4"])
    unchanged = apply_overrides(config, args)

    assert unchanged.practice.metronome is True
    assert unchanged.practice.count_in_clicks is False
    assert unchanged == config


@pytest.mark.feature("F-92")
def test_a_boolean_can_be_set_either_way() -> None:
    """`--x` and `--no-x`, so a TOML value can be overridden in both directions."""
    config = Config.load(None)

    on = apply_overrides(config, parse(["run", "x.mid", "-o", "y.mp4", "--metronome"]))
    assert on.practice.metronome is True

    off = apply_overrides(
        config, parse(["run", "x.mid", "-o", "y.mp4", "--no-metronome"])
    )
    assert off.practice.metronome is False


@pytest.mark.feature("F-92")
def test_a_nested_section_is_reachable() -> None:
    """`visual.colors.left_hand` is two levels down and still has a flag."""
    args = parse(
        ["render", "x.mid", "-o", "y.mp4", "--visual-colors-left-hand", "#ff8800"]
    )
    updated = apply_overrides(Config.load(None), args)
    assert updated.visual.colors.left_hand == "#ff8800"
    assert updated.visual.colors.right_hand == Config().visual.colors.right_hand


# -- what a flag cannot do ----------------------------------------------


@pytest.mark.feature("F-92")
def test_an_override_is_validated() -> None:
    """A flag can build a config no file ever held, so the result is checked.

    Without this a bad flag reaches the renderer instead of the error message,
    and the failure arrives forty minutes into an encode.
    """
    args = parse(["run", "x.mid", "-o", "y.mp4", "--reverb", "3"])
    with pytest.raises(ConfigError, match=r"audio\.reverb"):
        apply_overrides(Config.load(None), args)


@pytest.mark.feature("F-92")
def test_an_override_that_is_only_odd_still_goes_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The warn tier reaches the flags too, and does not block them."""
    import logging

    args = parse(["render", "x.mid", "-o", "y.mp4", "--visual-note-radius", "0.9"])
    with caplog.at_level(logging.WARNING, logger="psv.config"):
        updated = apply_overrides(Config.load(None), args)
    assert updated.visual.note_radius == pytest.approx(0.9)
    assert "draws as 0.5" in caplog.text


@pytest.mark.feature("F-92")
def test_a_word_outside_the_choices_is_a_usage_error() -> None:
    """`-h` lists what the validator accepts, so argparse can refuse the rest."""
    with pytest.raises(SystemExit):
        parse(["run", "x.mid", "-o", "y.mp4", "--audio-backend", "winamp"])


@pytest.mark.feature("F-92")
def test_no_offered_word_is_one_the_validator_would_reject() -> None:
    """Two copies of a list is how `-h` starts offering something invalid.

    Checked by the word being accepted *as that setting*, not by the whole
    config validating. `audio.backend = 'fluidsynth'` is a perfectly good
    backend that additionally needs a SoundFont, and "requires audio.soundfont"
    is a different complaint from "must be one of". Only the second would mean
    `-h` and `validate` disagree, and every one of those reads `<path> must be`.
    """
    from dataclasses import replace
    from typing import Any

    for path, allowed in CHOICES.items():
        section, _, field = path.rpartition(".")
        target: Any = Config()
        for part in section.split("."):
            target = getattr(target, part)
        for word in allowed:
            try:
                replace(target, **{field: word}).validate()
            except ConfigError as exc:
                assert f"{path} must be" not in str(exc), (
                    f"-h offers {path}={word!r} and validate refuses the word: {exc}"
                )


# -- the older, shorter names -------------------------------------------


@pytest.mark.feature("F-92")
@pytest.mark.parametrize(
    ("alias", "generated"),
    [
        ("--span", "--hands-max-span-semitones"),
        ("--width", "--visual-width"),
        ("--fps", "--visual-fps"),
        ("--encode", "--visual-encode"),
        ("--reverb", "--audio-reverb"),
        ("--tempo", "--practice-tempo"),
        ("--title-card", "--title-seconds"),
        ("--no-pedal", "--no-pedals-enabled"),
    ],
)
def test_the_names_that_were_here_first_still_work(alias: str, generated: str) -> None:
    """Kept as extra spellings, not replaced. They are in scripts and history."""
    offered = flags_of("run")
    assert alias in offered
    assert generated in offered


@pytest.mark.feature("F-92")
def test_silent_count_in_still_turns_the_clicks_off() -> None:
    """An older flag that names one direction of a boolean, kept working."""
    args = parse(["run", "x.mid", "-o", "y.mp4", "--silent-count-in"])
    updated = apply_overrides(Config.load(None), args)
    assert updated.practice.count_in_clicks is False


@pytest.mark.feature("F-92")
def test_every_described_setting_exists() -> None:
    """A help line for a setting that is gone is a help line nobody sees."""
    paths = {setting.path for setting in settings()}
    assert not set(HELP) - paths
    assert not set(CHOICES) - paths


@pytest.mark.feature("F-92")
def test_the_help_names_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    """Reading `-h` should answer "what is it now", not only "what can it be"."""
    with pytest.raises(SystemExit):
        main(["render", "-h"])
    out = capsys.readouterr().out
    assert "--visual-fps" in out
    assert "60" in out
