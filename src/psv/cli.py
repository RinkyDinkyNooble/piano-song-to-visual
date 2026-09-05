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
from psv.config import (
    ENCODE_LEVELS,
    AudioConfig,
    Config,
    ConfigError,
    PracticeConfig,
    TitleConfig,
    VisualConfig,
)
from psv.constraints import ConstraintError
from psv.constraints import constrain as constrain_score
from psv.inspect import format_report, inspect_score
from psv.instruments import (
    GM_PROGRAMS,
    SUGGESTED,
    SoundFontError,
    soundfont_presets,
)
from psv.load import ScoreReadError, read_score
from psv.midi import write_midi_file
from psv.midi.read import MidiReadError
from psv.pipeline import run as run_pipeline
from psv.practice import prepare
from psv.presets import (
    DESCRIPTIONS,
    EFFECT_DESCRIPTIONS,
    EFFECT_SETS,
    PRESETS,
    THEME_DESCRIPTIONS,
    THEMES,
    apply_effect_set,
    apply_preset,
    apply_theme,
)
from psv.render.video import TAIL_S, VideoWriteError, render_video

log = logging.getLogger("psv")

#: Pipeline stages, in the order they run, plus the utilities either side.
PIPELINE_COMMANDS: dict[str, str] = {
    "inspect": "report what is inside a score",
    "export": "write a parsed score back out as MIDI",
    "arrange": "multi-instrument score -> two-hand piano MIDI",
    "constrain": "enforce hand-span and difficulty limits",
    "render": "arrangement -> falling-notes video",
    "run": "run the full pipeline end to end",
}

IMPLEMENTED = frozenset({"inspect", "export", "arrange", "constrain", "render", "run"})

#: Commands that answer a question instead of moving a file through a stage.
UTILITY_COMMANDS: dict[str, str] = {
    "instruments": "list the instruments audio.program can select",
    "presets": "describe what --preset, --theme and --effects accept",
}


def _global_options(suppress: bool) -> argparse.ArgumentParser:
    """The options that work on either side of the subcommand.

    Defined once and attached twice, through `parents=`, because `psv -c x.toml
    run ...` working while `psv run ... -c x.toml` is a usage error is a
    distinction nothing about the flags suggests.

    The subcommand copies default to SUPPRESS. argparse parses a subcommand into
    its own namespace and copies every attribute back over the top-level one, so
    a real default there would overwrite what was given before the subcommand
    with the default of the copy that was not given after it.
    """
    parent = argparse.ArgumentParser(add_help=False)
    default: object = argparse.SUPPRESS if suppress else 0
    parent.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=default,
        help="increase log verbosity, and show more detail in reports (repeatable)",
    )
    parent.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        type=Path,
        default=argparse.SUPPRESS if suppress else None,
        help="path to a psv config file (TOML)",
    )
    parent.add_argument(
        "-p",
        "--preset",
        choices=sorted(PRESETS),
        default=argparse.SUPPRESS if suppress else None,
        help="a named bundle of settings; `psv presets` describes each one",
    )
    parent.add_argument(
        "--theme",
        choices=sorted(THEMES),
        default=argparse.SUPPRESS if suppress else None,
        help="a named colour scheme; `psv presets` describes each one",
    )
    parent.add_argument(
        "--effects",
        choices=sorted(EFFECT_SETS),
        default=argparse.SUPPRESS if suppress else None,
        help="a named bundle of optional visual effects; off unless asked for",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psv",
        description=(
            "Turn a MIDI or MusicXML score into a falling-notes piano "
            "practice video, arranged to be playable by human hands."
        ),
        parents=[_global_options(suppress=False)],
    )
    parser.add_argument("--version", action="version", version=f"psv {__version__}")

    shared = _global_options(suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    for name, help_text in PIPELINE_COMMANDS.items():
        child = sub.add_parser(
            name, help=help_text, description=help_text, parents=[shared]
        )
        child.add_argument("input", type=Path, help="input MIDI or MusicXML file")
        if name != "inspect":
            child.add_argument(
                "-o",
                "--output",
                type=Path,
                required=name in IMPLEMENTED,
                help="output file",
            )
        if name in {"arrange", "constrain", "run"}:
            child.add_argument(
                "--span",
                type=int,
                default=None,
                metavar="SEMITONES",
                help="override hands.max_span_semitones; 0 means no limit",
            )
        if name in {"render", "run"}:
            _add_render_options(child)

    for name, help_text in UTILITY_COMMANDS.items():
        sub.add_parser(name, help=help_text, description=help_text, parents=[shared])

    return parser


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    """Overrides for the config's visual settings.

    These exist for iteration speed. Debugging a render at full 1080p60 wastes
    minutes per attempt; `--seconds 3 --width 320 --height 180` turns the same
    loop into about a second.
    """
    parser.add_argument(
        "--start",
        type=float,
        default=None,
        metavar="S",
        help="start time in seconds, measured in the rendered video",
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
    parser.add_argument(
        "--encode",
        choices=sorted(ENCODE_LEVELS),
        default=None,
        help=(
            "how hard the encoder works: `small` is slowest and smallest, "
            "`fast` is quickest and about three times the file "
            "(default: balanced)"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "processes to render with; 0 picks one per core, 1 renders in "
            "a single process (default: 0)"
        ),
    )
    parser.add_argument(
        "--reverb",
        type=float,
        default=None,
        metavar="AMOUNT",
        help=(
            "how much room the piano is played in, 0 dry to 1 a large hall "
            "(default: 0.5, which is what it has always sounded like). "
            "fluidsynth backend only"
        ),
    )
    _add_practice_options(parser)
    _add_title_options(parser)


def _add_practice_options(parser: argparse.ArgumentParser) -> None:
    """How the finished arrangement is presented, rather than what is in it.

    These are how a piece actually gets learned: slow it down, take the hard
    forty bars on their own, count yourself in, and play one hand at a time.
    """
    group = parser.add_argument_group("practice")
    group.add_argument(
        "--tempo",
        type=float,
        default=None,
        metavar="FACTOR",
        help="playback speed; 0.75 is three-quarters of the written tempo",
    )
    group.add_argument(
        "--bars",
        type=bar_range,
        default=None,
        metavar="FIRST-LAST",
        help="render only these bars, counting from 1 (e.g. 20-40, or 31)",
    )
    group.add_argument(
        "--hands",
        choices=("both", "left", "right"),
        default=None,
        help="which hand to sound; the other stays on screen, faintly",
    )
    group.add_argument(
        "--count-in",
        type=int,
        default=None,
        metavar="BARS",
        help="bars of lead-in before the music starts",
    )
    group.add_argument(
        "--silent-count-in",
        action="store_false",
        dest="count_in_clicks",
        default=None,
        help="keep the lead-in but drop its clicks; the falling notes count it",
    )
    group.add_argument(
        "--metronome",
        action="store_true",
        default=None,
        help="keep clicking through the piece, not only into it",
    )


def _add_title_options(parser: argparse.ArgumentParser) -> None:
    """A card at the front and a fade at the end, for a video you will post.

    Off unless one of these is given, because a practice video wants neither
    and they cost a re-encode of the finished file.
    """
    group = parser.add_argument_group("title")
    group.add_argument(
        "--title-card",
        type=float,
        default=None,
        metavar="SECONDS",
        dest="title_seconds",
        help="show a title card for this long, fading to reveal the music",
    )
    group.add_argument(
        "--title",
        default=None,
        metavar="TEXT",
        dest="title_text",
        help="what the card says; the score's own title is used when omitted",
    )
    group.add_argument(
        "--composer",
        default=None,
        metavar="TEXT",
        dest="title_composer",
        help="the second line; read from MusicXML when omitted",
    )
    group.add_argument(
        "--fade-out",
        type=float,
        default=None,
        metavar="SECONDS",
        dest="title_fade_out_s",
        help="fade the picture and the sound to black over this long",
    )
    group.add_argument(
        "--hold-black",
        type=float,
        default=None,
        metavar="SECONDS",
        dest="title_hold_s",
        help="hold on black for this long after the fade",
    )


def bar_range(text: str) -> tuple[int, int]:
    """Parse ``--bars``: either ``20-40`` or a single bar, ``31``."""
    parts = text.split("-")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"bars must be numbers like 20-40, got {text!r}"
        ) from None
    if len(numbers) == 1:
        numbers *= 2
    if len(numbers) != 2:
        raise argparse.ArgumentTypeError(f"bars must be FIRST-LAST, got {text!r}")
    first, last = numbers
    if first < 1:
        raise argparse.ArgumentTypeError(f"bars are numbered from 1, got {first}")
    if last < first:
        raise argparse.ArgumentTypeError(f"bar range runs backwards: {text!r}")
    return first, last


def configure_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def _cmd_inspect(args: argparse.Namespace, _config: Config) -> int:
    score = read_score(args.input)
    print(format_report(inspect_score(score), verbose=args.verbose > 0))
    return 0


def _cmd_export(args: argparse.Namespace, _config: Config) -> int:
    score = read_score(args.input)
    path = write_midi_file(score, args.output)
    print(f"wrote {path}")
    return 0


def _visual_with_overrides(
    visual: VisualConfig, args: argparse.Namespace
) -> VisualConfig:
    overrides = {
        name: getattr(args, name)
        for name in ("width", "height", "fps", "encode", "workers")
        if getattr(args, name, None) is not None
    }
    if not overrides:
        return visual
    updated = replace(visual, **overrides)
    updated.validate()
    return updated


def _audio_with_overrides(audio: AudioConfig, args: argparse.Namespace) -> AudioConfig:
    """`--reverb` beats audio.reverb, as the size overrides do."""
    reverb = getattr(args, "reverb", None)
    if reverb is None:
        return audio
    updated = replace(audio, reverb=reverb)
    updated.validate()
    return updated


def _cmd_render(args: argparse.Namespace, config: Config) -> int:
    score = read_score(args.input)
    visual = _visual_with_overrides(config.visual, args)
    practice = _practice_with_overrides(config.practice, args)
    title = _title_with_overrides(config.title, args)
    _check_window_flags(args)

    if title.is_on:
        # `render` writes the picture only. A card here would be accepted and
        # then thrown away by whatever muxes the sound on afterwards.
        print("psv: the title options need `psv run`", file=sys.stderr)
        return 2

    if practice.metronome:
        # `render` writes a silent video, so saying nothing here would look like
        # the flag was accepted and the clicks went missing.
        print("psv: --metronome needs sound; use `psv run`", file=sys.stderr)

    show = prepare(
        score,
        practice,
        start=args.start or 0.0,
        seconds=args.seconds,
        bars=args.bars,
        tail=TAIL_S,
    )

    path = render_video(
        show.score,
        visual,
        args.output,
        start=show.start,
        duration=show.duration,
        pedal_lanes=config.pedals.lanes,
        focus=show.focus,
        on_frame=_progress(args),
    )
    if args.verbose > 0 and sys.stderr.isatty():
        print(file=sys.stderr)
    if show.label:
        print(f"  practice         {show.label}")
    print(f"wrote {path}")
    return 0


def _title_with_overrides(title: TitleConfig, args: argparse.Namespace) -> TitleConfig:
    """Command-line flags win over the config file, as the others do."""
    overrides = {
        field: getattr(args, name)
        for field, name in (
            ("seconds", "title_seconds"),
            ("text", "title_text"),
            ("composer", "title_composer"),
            ("fade_out_s", "title_fade_out_s"),
            ("hold_s", "title_hold_s"),
        )
        if getattr(args, name, None) is not None
    }
    if not overrides:
        return title
    updated = replace(title, **overrides)
    updated.validate()
    return updated


def _practice_with_overrides(
    practice: PracticeConfig, args: argparse.Namespace
) -> PracticeConfig:
    """Command-line flags win over the config file, as the size overrides do."""
    overrides = {
        field: getattr(args, name)
        for field, name in (
            ("tempo", "tempo"),
            ("hands", "hands"),
            ("count_in_bars", "count_in"),
            ("count_in_clicks", "count_in_clicks"),
            ("metronome", "metronome"),
        )
        if getattr(args, name, None) is not None
    }
    if not overrides:
        return practice
    updated = replace(practice, **overrides)
    updated.validate()
    return updated


def _check_window_flags(args: argparse.Namespace) -> None:
    """``--bars`` and the second-based flags say the same thing two ways."""
    if args.bars is None:
        return
    clashes = [
        flag
        for flag, value in (("--start", args.start), ("--seconds", args.seconds))
        if value is not None
    ]
    if clashes:
        raise ConfigError(f"--bars cannot be combined with {' or '.join(clashes)}")


def _cmd_presets(args: argparse.Namespace, config: Config) -> int:
    """What each preset and theme does, without rendering something to find out."""
    del args, config
    width = max(len(name) for name in (*PRESETS, *THEMES, *EFFECT_SETS))

    print("presets (--preset), which change how the piece is played:")
    for name in sorted(PRESETS):
        print(f"  {name:{width}}  {DESCRIPTIONS[name]}")
        for section, fields in sorted(PRESETS[name].items()):
            for key, value in sorted(fields.items()):
                print(f"  {'':{width}}    {section}.{key} = {value!r}")

    print()
    print("themes (--theme), which only change how it looks:")
    for name in sorted(THEMES):
        print(f"  {name:{width}}  {THEME_DESCRIPTIONS[name]}")
        for key, value in sorted(THEMES[name].items()):
            print(f"  {'':{width}}    visual.{key} = {value!r}")

    print()
    print("effects (--effects), drawn in the order listed, off by default:")
    for name in sorted(EFFECT_SETS):
        print(f"  {name:{width}}  {EFFECT_DESCRIPTIONS[name]}")
        for effect in EFFECT_SETS[name]:
            print(f"  {'':{width}}    {effect.kind} at {effect.intensity}")
    return 0


def _hands_with_overrides(config: Config, args: argparse.Namespace) -> Config:
    """`--span` beats hands.max_span_semitones, as the size overrides do."""
    span = getattr(args, "span", None)
    if span is None:
        return config
    hands = replace(config.hands, max_span_semitones=span)
    hands.validate()
    return replace(config, hands=hands)


def _cmd_instruments(args: argparse.Namespace, config: Config) -> int:
    """What `audio.program` can be set to, from the SoundFont where there is one.

    GM is a convention, not a guarantee. A SoundFont may put anything at any
    program number, so when one is configured its own names are what will
    actually sound and they are what gets printed.
    """
    font = config.audio.soundfont
    if font:
        try:
            presets = soundfont_presets(font)
        except (SoundFontError, OSError) as exc:
            print(f"psv: could not read {font}: {exc}", file=sys.stderr)
            print("falling back to the General MIDI names\n", file=sys.stderr)
        else:
            # Bank 0 is the General MIDI set. The variation banks are
            # usually the bulk of a font and rarely what someone is
            # looking for, so they sit behind -v.
            shown = [p for p in presets if not p.bank or args.verbose]
            hidden = len(presets) - len(shown)
            print(f"{Path(font).name}: {len(shown)} preset(s)")
            for preset in shown:
                label = f"  {preset.program:3}"
                if preset.bank:
                    label += f"  bank {preset.bank:3}"
                print(f"{label}  {preset.name}")
            if hidden:
                print(f"\n{hidden} more in variation banks; -v lists them too")
            return 0

    for program, name in enumerate(GM_PROGRAMS):
        mark = "  *" if program in SUGGESTED else "   "
        print(f"{mark}{program:4}  {name}")
    print("\n  * worth trying on a piano piece:")
    for program, why in sorted(SUGGESTED.items()):
        print(f"      {program:3}  {GM_PROGRAMS[program]} - {why}")
    return 0


def _cmd_constrain(args: argparse.Namespace, config: Config) -> int:
    score = read_score(args.input)
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
    score = read_score(args.input)
    result = arrange_score(
        score,
        max_span=config.hands.layout_span,
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
    practice = _practice_with_overrides(config.practice, args)
    title = _title_with_overrides(config.title, args)
    audio = _audio_with_overrides(config.audio, args)
    _check_window_flags(args)
    result = run_pipeline(
        args.input,
        args.output,
        replace(config, visual=visual, practice=practice, audio=audio, title=title),
        start=args.start or 0.0,
        duration=args.seconds,
        bars=args.bars,
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
    "instruments": _cmd_instruments,
    "presets": _cmd_presets,
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
        # Least specific to most: the file, then the preset, then the flags.
        config = Config.load(args.config)
        if args.preset is not None:
            config = apply_preset(config, args.preset)
        if args.theme is not None:
            config = apply_theme(config, args.theme)
        if args.effects is not None:
            config = apply_effect_set(config, args.effects)
        config = _hands_with_overrides(config, args)
        return HANDLERS[args.command](args, config)
    except (
        ConfigError,
        ScoreReadError,
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
