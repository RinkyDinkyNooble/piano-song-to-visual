"""Smoke tests for the CLI surface."""

from __future__ import annotations

import pytest

from psv import __version__
from psv.cli import PIPELINE_COMMANDS, build_parser, main


def test_version_is_set() -> None:
    assert __version__


def test_parser_exposes_every_pipeline_command() -> None:
    parser = build_parser()
    for command in PIPELINE_COMMANDS:
        # Parsing succeeds only if the subcommand is registered.
        assert parser.parse_args([command]).command == command


def test_pipeline_commands_cover_every_stage() -> None:
    assert set(PIPELINE_COMMANDS) == {
        "inspect",
        "arrange",
        "constrain",
        "render",
        "run",
    }


def test_bare_invocation_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: psv" in capsys.readouterr().out


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_unimplemented_command_errors() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["render"])
    assert exc.value.code == 2
