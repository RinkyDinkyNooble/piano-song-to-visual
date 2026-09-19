"""Command-line flags, read off the config dataclasses.

Every setting in `psv.config` gets a flag, and it gets one because this module
walks the dataclasses rather than because somebody remembered to write an
`add_argument` call. Thirty calls used to cover about seventy settings, which
is why editing source to change a colour was a normal thing to do here.

Three rules hold the scheme together.

**A flag is named after where the setting lives.** `visual.grid.opacity` is
`--visual-grid-opacity`. Nothing has to be looked up, and `psv render -h` lists
the config with the dots turned into dashes. The shorter names that were here
first are kept as extra spellings of the same flag, so `--fps` and
`--visual-fps` are one argument with two names.

**Nothing defaults to a value.** Every flag defaults to ``None``, and ``None``
means leave whatever the file said. That is what makes the layering work:
defaults, then the TOML, then the preset, then the flag, each only overriding
what it actually names. A flag that defaulted to the dataclass default would
stomp a TOML value every time it was left off.

**A command only offers the sections it reads.** `psv constrain` does not take
`--visual-fps`, because nothing it does could use one. Silently accepting a
flag that cannot have an effect is worse than refusing it.

Only this module and `psv.cli` know any of this exists; the config itself has
no idea it is being read by a command line.
"""

from __future__ import annotations

import argparse
from dataclasses import MISSING, fields, is_dataclass, replace
from typing import Any, get_type_hints

from psv.config import (
    AUDIO_BACKENDS,
    BEAT_LINES,
    DIFFICULTY_LEVELS,
    ENCODE_LEVELS,
    PITCH_LINES,
    PRACTICE_HANDS,
    TITLE_CURVES,
    Config,
)

#: Which config sections each command actually reads. A command gets flags for
#: these and nothing else.
#:
#: `render` has no `hands` or `difficulty` because it does not constrain; it
#: draws whatever it is given. It does take `title`, which it then refuses with
#: a message, because a card silently dropped by the mux step afterwards is the
#: worse failure.
SECTIONS_FOR: dict[str, tuple[str, ...]] = {
    "export": ("pedals",),
    "arrange": ("hands", "difficulty", "pedals"),
    "constrain": ("hands", "difficulty", "pedals"),
    "render": ("pedals", "visual", "practice", "title"),
    "run": ("hands", "difficulty", "pedals", "visual", "audio", "practice", "title"),
}

#: The names these settings had before the flags were generated. Kept as extra
#: spellings rather than replaced: they are shorter, they are what is in
#: people's scripts and shell history, and `--span` reads better than
#: `--hands-max-span-semitones` for the one setting used most.
#:
#: A boolean's alias gets its `--no-` form for free, so `pedals.enabled` listed
#: as `--pedal` is what produces `--no-pedal`.
ALIASES: dict[str, tuple[str, ...]] = {
    "hands.max_span_semitones": ("--span",),
    "pedals.enabled": ("--pedal",),
    "visual.width": ("--width",),
    "visual.height": ("--height",),
    "visual.fps": ("--fps",),
    "visual.encode": ("--encode",),
    "visual.workers": ("--workers",),
    "audio.reverb": ("--reverb",),
    "practice.tempo": ("--tempo",),
    "practice.hands": ("--hands",),
    "practice.count_in_bars": ("--count-in",),
    "practice.metronome": ("--metronome",),
    "title.seconds": ("--title-card",),
    "title.text": ("--title",),
    "title.composer": ("--composer",),
    "title.fade_out_s": ("--fade-out",),
    "title.hold_s": ("--hold-black",),
}

#: Settings that accept one of a fixed set of words. Taken from the same
#: constants the validator uses, so `-h` cannot offer something `validate`
#: would then refuse.
CHOICES: dict[str, tuple[str, ...]] = {
    "difficulty.level": DIFFICULTY_LEVELS,
    "audio.backend": AUDIO_BACKENDS,
    "practice.hands": PRACTICE_HANDS,
    "visual.encode": tuple(sorted(ENCODE_LEVELS)),
    "visual.grid.pitch_lines": PITCH_LINES,
    "visual.grid.beat_lines": BEAT_LINES,
    "title.curve": TITLE_CURVES,
}

#: What a setting is for, in one line, where the name does not already say it.
#: Anything absent falls back to naming the setting and its default, which is
#: honest but not much help.
HELP: dict[str, str] = {
    "hands.max_span_semitones": "widest reach one hand may be asked for; "
    "0 means no limit",
    "hands.overlap_tolerance_s": "overlaps shorter than this do not count as "
    "simultaneous",
    "hands.single_press": "never strike a key another note is still holding; "
    "separate from the span limit and still enforced when that is 0",
    "difficulty.level": "how much of the texture to keep",
    "pedals.enabled": "whether the piece is played with pedals at all; "
    "--no-pedal reads it as written without them, which changes the arrangement",
    "pedals.lanes": "pedal lanes drawn beside the keyboard; 0 hides them without "
    "changing the sound",
    "pedals.threshold": "controller value at which a pedal counts as engaged",
    "visual.width": "frame width in pixels",
    "visual.height": "frame height in pixels",
    "visual.fps": "frames per second",
    "visual.lookahead_s": "seconds of music visible above the keyboard at once",
    "visual.background": "any hex colour; grey keeps it out of the way of the notes",
    "visual.note_border": "outline on each bar, as a fraction of the frame width",
    "visual.note_radius": "rounds the ends of each bar, as a fraction of its width",
    "visual.bar_gradient": "brightness ramp down each bar; negative fades the bottom",
    "visual.gradient_top": "background gradient; set both ends or neither",
    "visual.gradient_bottom": "background gradient; set both ends or neither",
    "visual.workers": "processes to render with; 0 picks one per core",
    "visual.encode": "how hard the encoder works: `small` is slowest and smallest, "
    "`fast` is quickest and about three times the file",
    "visual.colors.left_hand": "hex colour for the left hand",
    "visual.colors.right_hand": "hex colour for the right hand",
    "visual.grid.opacity": "how faint the alignment rules are",
    "audio.backend": "what makes the sound",
    "audio.soundfont": "path to a .sf2, for the fluidsynth backend",
    "audio.program": "which instrument in that SoundFont; `psv instruments` lists them",
    "audio.reverb": "how much room the piano is played in, 0 dry to 1 a large hall",
    "audio.velocity_floor": "lift every velocity onto this floor, so a passage "
    "written at ppp stays quiet instead of vanishing; 0 is off, 30 is a sane start",
    "audio.audio_file": "your own recording, for the mux backend",
    "audio.offset_s": "nudge that recording into sync",
    "practice.tempo": "playback speed; 0.75 is three-quarters of the written tempo",
    "practice.hands": "which hand to sound; the other stays on screen, faintly",
    "practice.count_in_bars": "bars of lead-in before the music starts",
    "practice.count_in_clicks": "whether the lead-in clicks",
    "practice.metronome": "keep clicking through the piece, not only into it",
    "title.seconds": "show a title card for this long, fading to reveal the music",
    "title.text": "what the card says; the score's own title is used when omitted",
    "title.composer": "the second line; read from MusicXML when omitted",
    "title.footer": "a third line, fainter and lower",
    "title.font": "a .ttf or .otf file for the card",
    "title.fade_out_s": "fade the picture and the sound to black over this long",
    "title.hold_s": "hold on black for this long after the fade",
    "title.clear_at": "when the card reaches nothing, in seconds",
}


class Setting:
    """One config field, and the flag that sets it.

    A small class rather than a tuple because five parallel values passed
    around positionally is how the wrong one ends up in the wrong slot.
    """

    __slots__ = ("default", "kind", "path")

    def __init__(self, path: str, kind: type, default: Any) -> None:
        self.path = path
        self.kind = kind
        self.default = default

    @property
    def section(self) -> str:
        return self.path.split(".", 1)[0]

    @property
    def flag(self) -> str:
        return "--" + self.path.replace(".", "-").replace("_", "-")

    @property
    def dest(self) -> str:
        """Where argparse puts it. Distinct from every hand-written flag's."""
        return "cfg_" + self.path.replace(".", "__")

    @property
    def option_strings(self) -> tuple[str, ...]:
        return (self.flag, *ALIASES.get(self.path, ()))

    @property
    def help(self) -> str:
        described = HELP.get(self.path)
        if described is None:
            described = f"set {self.path}"
        return f"{described} (default: {self.default!r})"


def settings(target: type = Config, prefix: str = "") -> list[Setting]:
    """Every scalar setting in the config, depth first, with its dotted path.

    Nested dataclasses are walked into. `visual.effects` is not: it is a list
    of tables, and a list of tables is a thing you write in a file rather than
    spell on a command line. `--effects` names a whole bundle instead.
    """
    found: list[Setting] = []
    hints = get_type_hints(target)
    for field in fields(target):
        hint = hints[field.name]
        path = f"{prefix}{field.name}"
        if isinstance(hint, type) and is_dataclass(hint):
            found.extend(settings(hint, prefix=f"{path}."))
        elif hint in (bool, int, float, str):
            found.append(Setting(path, hint, _default_of(target, field.name)))
    return found


def _default_of(target: type, name: str) -> Any:
    """What the dataclass says this field is when nobody sets it."""
    for field in fields(target):
        if field.name != name:
            continue
        if field.default is not MISSING:
            return field.default
        if field.default_factory is not MISSING:
            return field.default_factory()
    return None


def add_config_options(parser: argparse.ArgumentParser, command: str) -> None:
    """Attach the flags this command can act on, one argument group per section."""
    wanted = SECTIONS_FOR.get(command, ())
    for section in wanted:
        chosen = [s for s in settings() if s.section == section]
        if not chosen:
            continue
        group = parser.add_argument_group(section)
        for setting in chosen:
            _add(group, setting)


def _add(group: argparse._ArgumentGroup, setting: Setting) -> None:
    if setting.kind is bool:
        group.add_argument(
            *setting.option_strings,
            action=argparse.BooleanOptionalAction,
            dest=setting.dest,
            default=None,
            help=setting.help,
        )
        return
    group.add_argument(
        *setting.option_strings,
        type=setting.kind,
        dest=setting.dest,
        default=None,
        choices=CHOICES.get(setting.path),
        metavar=_metavar(setting),
        help=setting.help,
    )


def _metavar(setting: Setting) -> str | None:
    if setting.path in CHOICES:
        return None  # argparse prints the choices, which say more
    return {int: "N", float: "X", str: "TEXT"}.get(setting.kind)


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    """Lay the flags that were given over the config, and re-validate.

    Re-validating matters as much as the replacing does. A flag can produce a
    combination no file ever contained, and a config nothing checked is how a
    render gets forty minutes in before anyone finds out.
    """
    given = {
        setting.path: value
        for setting in settings()
        if (value := getattr(args, setting.dest, None)) is not None
    }
    if not given:
        return config

    updated: Config = _lay_over(config, given, prefix="")
    updated.validate()
    return updated


def _lay_over(target: Any, given: dict[str, Any], prefix: str) -> Any:
    """Rebuild one dataclass with whatever of `given` belongs to it."""
    changes: dict[str, Any] = {}
    hints = get_type_hints(type(target))
    for field in fields(target):
        path = f"{prefix}{field.name}"
        hint = hints[field.name]
        if isinstance(hint, type) and is_dataclass(hint):
            if any(key.startswith(f"{path}.") for key in given):
                changes[field.name] = _lay_over(
                    getattr(target, field.name), given, prefix=f"{path}."
                )
        elif path in given:
            changes[field.name] = given[path]
    return replace(target, **changes) if changes else target
