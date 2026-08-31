"""Command-line entry point for psv.

A thin shell over the core library. Every command here parses arguments, calls
one function, and prints the result; nothing in `psv` below this module knows
the CLI exists.

Every stage works, and `run` chains them into a finished video with sound.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from psv import __version__
from psv.arrange import arrange as arrange_score
from psv.audio.backends import AudioError
from psv.config import Config, ConfigError, VisualConfig
from psv.constraints import ConstraintError
from psv.constraints import constrain as constrain_score
from psv.inspect import format_report, inspect_score
from psv.midi import read_midi_file, write_midi_file
from psv.midi.read import MidiReadError
from psv.pipeline import run as run_pipeline
from psv.render.video import VideoWriteError, render_video

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

IMPLEMENTED = frozenset({"inspect", "export", "arrange", "constrain", "render", "run"})


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
        if name in {"render", "run"}:
            _add_render_options(child)

    return parser


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    """Overrides for the config's visual settings.

    These exist for iteration speed. Debugging a render at full 1080p60 wastes
    minutes per attempt; `--seconds 3 --width 320 --height 180` turns the same
    loop into about a second.
    """
    parser.add_argument(
        "--start", type=float, default=0.0, metavar="S", help="start time in seconds"
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        metavar="S",
        help="render only this many seconds (default: the whole piece)",
    )
    parser.add_argument("--width", type=int, default=None, help="override frame width")
    parser.add_argument(
        "--height", type=int, default=None, help="override frame height"
    )
    parser.add_argument("--fps", type=int, default=None, help="override frame rate")


def configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _cmd_inspect(args: argparse.Namespace, _config: Config) -> int:
    score = read_midi_file(args.input)
    print(format_report(inspect_score(score), verbose=args.verbose > 0))
    return 0


def _cmd_export(args: argparse.Namespace, _config: Config) -> int:
    score = read_midi_file(args.input)
    path = write_midi_file(score, args.output)
    print(f"wrote {path}")
    return 0


def _visual_with_overrides(
    visual: VisualConfig, args: argparse.Namespace
) -> VisualConfig:
    overrides = {
        name: getattr(args, name)
        for name in ("width", "height", "fps")
        if getattr(args, name, None) is not None
    }
    if not overrides:
        return visual
    updated = replace(visual, **overrides)
    updated.validate()
    return updated


def _cmd_render(args: argparse.Namespace, config: Config) -> int:
    score = read_midi_file(args.input)
    visual = _visual_with_overrides(config.visual, args)

    show_progress = args.verbose > 0 and sys.stderr.isatty()

    def on_frame(done: int, total: int) -> None:
        if show_progress and (done % 30 == 0 or done == total):
            print(f"\r  frame {done}/{total}", end="", file=sys.stderr)

    path = render_video(
        score,
        visual,
        args.output,
        start=args.start,
        duration=args.seconds,
        pedal_lanes=config.pedals.lanes,
        on_frame=on_frame,
    )
    if show_progress:
        print(file=sys.stderr)
    print(f"wrote {path}")
    return 0


def _cmd_constrain(args: argparse.Namespace, config: Config) -> int:
    score = read_midi_file(args.input)
    result = constrain_score(score, config)

    print(result.summary())
    if args.verbose > 1:
        for repair in result.repairs:
            print(f"  {repair}")
    print(f"  notes            {len(score.notes)} -> {len(result.score.notes)}")

    path = write_midi_file(result.score, args.output)
    print(f"wrote {path}")
    return 0


def _cmd_arrange(args: argparse.Namespace, config: Config) -> int:
    score = read_midi_file(args.input)
    result = arrange_score(
        score,
        max_span=config.hands.max_span_semitones,
        tolerance=config.hands.overlap_tolerance_s,
    )
    print(result.summary())
    print(f"  notes            {len(score.notes)} -> {len(result.score.notes)}")
    print(f"wrote {write_midi_file(result.score, args.output)}")
    return 0


def _progress(args: argparse.Namespace) -> Callable[[int, int], None]:
    """A one-line frame counter, only when asked for and only to a terminal."""
    show = args.verbose > 0 and sys.stderr.isatty()

    def on_frame(done: int, total: int) -> None:
        if show and (done % 30 == 0 or done == total):
            print("\r  frame", f"{done}/{total}", end="", file=sys.stderr)

    return on_frame


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    visual = _visual_with_overrides(config.visual, args)
    result = run_pipeline(
        args.input,
        args.output,
        replace(config, visual=visual),
        start=args.start,
        duration=args.seconds,
        on_frame=_progress(args),
    )
    if args.verbose > 0 and sys.stderr.isatty():
        print(file=sys.stderr)
    print(result.summary())
    print(f"wrote {result.output}")
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace, Config], int]] = {
    "inspect": _cmd_inspect,
    "export": _cmd_export,
    "arrange": _cmd_arrange,
    "constrain": _cmd_constrain,
    "render": _cmd_render,
    "run": _cmd_run,
}


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
        # Loaded first, so a bad config fails before any work is done.
        config = Config.load(args.config)
        return HANDLERS[args.command](args, config)
    except (
        ConfigError,
        MidiReadError,
        VideoWriteError,
        ConstraintError,
        AudioError,
    ) as exc:
        print(f"psv: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"psv: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
