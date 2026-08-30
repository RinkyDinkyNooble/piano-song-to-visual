"""Command-line entry point for psv.

A thin shell over the core library. Every command here parses arguments, calls
one function, and prints the result; nothing in `psv` below this module knows
the CLI exists.

`inspect` and `export` are implemented. The pipeline stages are not yet.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from psv import __version__
from psv.config import Config, ConfigError
from psv.inspect import format_report, inspect_score
from psv.midi import read_midi_file, write_midi_file
from psv.midi.read import MidiReadError

log = logging.getLogger("psv")

#: Pipeline stages, in the order they run, plus the utilities either side.
PIPELINE_COMMANDS: dict[str, str] = {
    "inspect": "report what is inside a MIDI file",
    "export": "write a parsed score back out as MIDI",
    "arrange": "multi-instrument MIDI -> two-hand piano MIDI",
    "constrain": "enforce hand-span and difficulty limits",
    "render": "arrangement -> falling-notes video",
    "run": "run the full pipeline end to end",
}

IMPLEMENTED = frozenset({"inspect", "export"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psv",
        description=(
            "Turn a MIDI file into a falling-notes piano practice video, "
            "arranged to be playable by human hands."
        ),
    )
    parser.add_argument("--version", action="version", version=f"psv {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase log verbosity, and show more detail in reports (repeatable)",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        type=Path,
        help="path to a psv config file (TOML)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, help_text in PIPELINE_COMMANDS.items():
        child = sub.add_parser(name, help=help_text, description=help_text)
        child.add_argument("input", type=Path, help="input MIDI file")
        if name != "inspect":
            child.add_argument(
                "-o",
                "--output",
                type=Path,
                required=name in IMPLEMENTED,
                help="output file",
            )

    return parser


def configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _cmd_inspect(args: argparse.Namespace) -> int:
    score = read_midi_file(args.input)
    print(format_report(inspect_score(score), verbose=args.verbose > 0))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    score = read_midi_file(args.input)
    path = write_midi_file(score, args.output)
    print(f"wrote {path}")
    return 0


HANDLERS = {"inspect": _cmd_inspect, "export": _cmd_export}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command not in HANDLERS:
        parser.error(f"'{args.command}' is not implemented yet")

    try:
        # Loaded even where unused yet, so a bad config fails before any work.
        Config.load(args.config)
        return HANDLERS[args.command](args)
    except (ConfigError, MidiReadError) as exc:
        print(f"psv: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"psv: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
