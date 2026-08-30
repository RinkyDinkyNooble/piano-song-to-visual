"""Command-line entry point for psv.

The pipeline stages (arrange -> constrain -> render) are not implemented yet;
this module defines the interface they will hang off of.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from psv import __version__

log = logging.getLogger("psv")

#: Pipeline stages, in the order they run, plus the end-to-end shortcut.
PIPELINE_COMMANDS: dict[str, str] = {
    "inspect": "report what is inside a MIDI file",
    "arrange": "multi-instrument MIDI -> two-hand piano MIDI",
    "constrain": "enforce hand-span and difficulty limits",
    "render": "arrangement -> falling-notes video",
    "run": "run the full pipeline end to end",
}


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
        help="increase log verbosity (repeatable)",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help="path to a psv config file (TOML)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, help_text in PIPELINE_COMMANDS.items():
        sub.add_parser(name, help=help_text)

    return parser


def configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.command is None:
        parser.print_help()
        return 0

    parser.error(f"'{args.command}' is not implemented yet")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
