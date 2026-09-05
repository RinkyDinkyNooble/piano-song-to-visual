"""Configuration, loaded from TOML and validated before anything uses it.

Two rules shape this module.

The hand-span limit is a hard invariant, so it is validated here and cannot be
set to something the constraint engine would silently ignore.

Config values reach ffmpeg and the filesystem, so unknown keys are an error
rather than being quietly dropped. A typo in a colour key should say so, not
leave you wondering why the render looks wrong.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Self, get_args, get_origin, get_type_hints

from psv.model import DEFAULT_OVERLAP_TOLERANCE_S
from psv.rgb import is_grayscale, is_hex, parse_hex

#: The widest simultaneous reach the engine will ever allow, about 2.5 octaves.
MAX_ALLOWED_SPAN = 36

#: hands.max_span_semitones = 0 means leave the piece exactly as written.
#: Not a weaker guarantee, a different request: the promise is that output
#: never exceeds the *configured* span, and this configures no span.
UNLIMITED_SPAN = 0

#: Hand assignment still has to put every note in one hand or the other, so
#: it lays out against this when no limit is being enforced. It constrains
#: nothing; it only decides where the split between the hands sits.
NOMINAL_SPAN = 12

#: How hard the encoder works to make the file small, and what that costs in
#: render time. Named after the choice rather than after x264's preset names,
#: since the thing being traded is file size against waiting.
#:
#: Measured on a 40-second 1080p60 render: `small` is x264's default and is the
#: slowest, `balanced` encodes about 1.4x faster for about 1.3x the file, and
#: `fast` about 2.1x faster for about 2.8x the file. None of them changes the
#: picture in a way anyone can see; they change how long the encoder spends
#: looking for things to compress.
ENCODE_LEVELS = {
    "small": "medium",
    "balanced": "veryfast",
    "fast": "ultrafast",
}

DIFFICULTY_LEVELS = ("beginner", "easy", "medium", "hard", "original")
AUDIO_BACKENDS = ("fluidsynth", "mux", "builtin", "none")
PRACTICE_HANDS = ("both", "left", "right")

#: Slowest and fastest practice playback.
MIN_TEMPO = 0.1
MAX_TEMPO = 4.0

#: The middle of the reverb range, and what psv has always sounded like:
#: FluidSynth turns its own reverb on by default, so this is not a new opinion
#: about how wet a piano should be, it is the setting that was already there.
DEFAULT_REVERB = 0.5

#: A count-in longer than this is waiting, not counting.
MAX_COUNT_IN_BARS = 8

#: How a title card's opacity falls to nothing.
#:
#:   ease    (1-t)^2   holds, then clears decisively
#:   linear  1-t
#:   slow    sqrt(1-t) clears early and lingers faint
TITLE_CURVES = ("ease", "linear", "slow")

#: A card longer than this is a wait, not an introduction.
MAX_TITLE_SECONDS = 20.0

#: What fraction of the card's time it takes to clear, when `clear_at` is left
#: at 0. The rest is clear screen, so the first notes are plainly visible
#: falling before any of them lands.
TITLE_CLEAR_SHARE = 0.7


class ConfigError(ValueError):
    """A config file is malformed, or a value is out of range."""


@dataclass(frozen=True, slots=True)
class HandsConfig:
    #: Notes held together by one hand may span at most this many semitones.
    max_span_semitones: int = 12
    #: Overlaps shorter than this do not count as simultaneous.
    overlap_tolerance_s: float = DEFAULT_OVERLAP_TOLERANCE_S

    @property
    def is_limited(self) -> bool:
        """Whether a span limit is being enforced at all."""
        return self.max_span_semitones != UNLIMITED_SPAN

    @property
    def layout_span(self) -> int:
        """The span hand assignment lays out against, limit or no limit."""
        return self.max_span_semitones if self.is_limited else NOMINAL_SPAN

    def validate(self) -> None:
        if not UNLIMITED_SPAN <= self.max_span_semitones <= MAX_ALLOWED_SPAN:
            raise ConfigError(
                f"hands.max_span_semitones must be 0 (no limit) or between 1 "
                f"and {MAX_ALLOWED_SPAN}, got {self.max_span_semitones}"
            )
        if self.overlap_tolerance_s < 0:
            raise ConfigError(
                f"hands.overlap_tolerance_s cannot be negative, "
                f"got {self.overlap_tolerance_s}"
            )


@dataclass(frozen=True, slots=True)
class DifficultyConfig:
    level: str = "original"

    def validate(self) -> None:
        if self.level not in DIFFICULTY_LEVELS:
            raise ConfigError(
                f"difficulty.level must be one of {DIFFICULTY_LEVELS}, "
                f"got {self.level!r}"
            )


@dataclass(frozen=True, slots=True)
class ColorConfig:
    #: Hue says which hand; brightness says how loud.
    left_hand: str = "#4a90d9"
    right_hand: str = "#5fb87a"
    #: For a score that has not been through hand assignment. Neutral on
    #: purpose, so an unassigned note is visibly not claimed by either hand.
    unassigned: str = "#9aa0ac"
    #: Pedal lanes, dimmed by depth the way notes are dimmed by velocity.
    pedal: str = "#c8a44a"
    #: Brightness multipliers at the quietest and loudest velocities.
    quiet: float = 0.35
    loud: float = 1.0

    def validate(self) -> None:
        for name in ("left_hand", "right_hand", "unassigned", "pedal"):
            value = getattr(self, name)
            if not is_hex(value):
                raise ConfigError(
                    f"visual.colors.{name} must be a hex colour like '#4a90d9', "
                    f"got {value!r}"
                )
        if not 0.0 <= self.quiet <= self.loud <= 1.0:
            raise ConfigError(
                "visual.colors requires 0 <= quiet <= loud <= 1, got "
                f"quiet={self.quiet}, loud={self.loud}"
            )


@dataclass(frozen=True, slots=True)
class GridConfig:
    """The alignment aids.

    Named after what each line *marks*, not which way it runs. In a
    falling-notes view the horizontal axis is pitch and the vertical axis is
    time, so "the horizontal lines" and "the vertical lines" are easy to get
    backwards; "pitch lines" and "beat lines" are not.

    ``pitch_lines`` draw vertically at keyboard landmarks, for finding a key.
    ``beat_lines`` draw horizontally across the falling area, so notes an octave
    and a half apart can be seen to be simultaneous.
    """

    #: Vertical rules at pitch landmarks: every C, every fifth, or off.
    pitch_lines: str = "octave"
    #: Horizontal rules at time landmarks: every beat, every bar, or off.
    beat_lines: str = "beat"
    opacity: float = 0.15

    def validate(self) -> None:
        if self.pitch_lines not in ("octave", "fifth", "none"):
            raise ConfigError(
                "visual.grid.pitch_lines must be octave, fifth, or none, "
                f"got {self.pitch_lines!r}"
            )
        if self.beat_lines not in ("beat", "bar", "none"):
            raise ConfigError(
                "visual.grid.beat_lines must be beat, bar, or none, "
                f"got {self.beat_lines!r}"
            )
        if not 0.0 <= self.opacity <= 1.0:
            raise ConfigError(
                f"visual.grid.opacity must be between 0 and 1, got {self.opacity}"
            )


@dataclass(frozen=True, slots=True)
class EffectConfig:
    """One optional visual effect, and how strongly it is drawn.

    Two fields and no per-effect parameters, which was not the plan. Every
    effect turned out to fold its own numbers into one strength, the way the
    reverb and the border shade do, so a schema per kind would be a mechanism
    with nothing to hold. Add one when an effect needs a second number, not
    before.
    """

    kind: str = ""
    #: 0 draws nothing at all, 1 is as strong as the effect goes.
    intensity: float = 0.6

    def validate(self) -> None:
        # Imported here rather than at module scope: the renderer imports this
        # module, so naming it at the top would be a cycle.
        from psv.render.effects import KINDS

        if self.kind not in KINDS:
            raise ConfigError(
                f"unknown visual effect {self.kind!r}. "
                f"Available: {', '.join(sorted(KINDS))}"
            )
        if not 0.0 <= self.intensity <= 1.0:
            raise ConfigError(
                f"visual effect {self.kind!r} needs an intensity between 0 and "
                f"1, got {self.intensity}"
            )


@dataclass(frozen=True, slots=True)
class VisualConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 60
    #: Seconds of music visible above the keyboard at once.
    lookahead_s: float = 3.0
    #: Black-key bar width, as a fraction of a white-key bar.
    black_key_bar_width: float = 0.6
    #: How much darker a black-key bar is drawn, on top of its colour.
    black_key_darkening: float = 0.2
    background: str = "#101010"
    #: Outline drawn around each note bar, as a fraction of the frame width.
    #: Repeated notes on one key otherwise draw as a single block: there is
    #: already a horizontal gap between adjacent *pitches*, so a chord reads as
    #: separate notes, but nothing separates consecutive notes in the same
    #: column, and four fast repeats look like one long note.
    #:
    #: A fraction rather than pixels because the right amount depends on the
    #: resolution. At 320 wide, a border that looks right at 1080p swallows the
    #: bar. 0 turns it off.
    note_border: float = 0.0016
    #: How the outline is shaded away from the bar's own colour. -1 is black,
    #: 0 is the bar's colour and no visible outline, +1 is white. The default
    #: is the dark edge this always drew; positive values light a bar from
    #: inside instead of cutting it out of the background.
    #:
    #: A shade rather than a colour of its own. The outline is drawn inside the
    #: bar and eats a few pixels of it, so at speed a short note is mostly
    #: outline, and keeping it a shade of the bar's hue is what leaves which
    #: hand is playing readable at the edge.
    note_border_shade: float = -0.45
    #: Rounds the ends of every bar, as a fraction of the bar's own width.
    #: 0 is the square corner this always drew, 0.5 rounds each end into a
    #: half-circle.
    #:
    #: A fraction of the bar rather than of the frame, unlike `note_border`,
    #: because the right radius is set by how wide the bar is and nothing else:
    #: a black-key bar is narrower than a white-key one and wants less. It is
    #: also self-limiting, since half a bar's width is the most that can be
    #: rounded off either end.
    note_radius: float = 0.0
    #: A vertical brightness ramp down each bar. 0 is the flat fill. Positive
    #: fades the top of the bar, negative fades the bottom, so which end looks
    #: lit is a choice rather than a fixed opinion.
    bar_gradient: float = 0.0
    #: A vertical gradient behind everything, top colour and bottom colour.
    #: Both empty leaves `background` in charge.
    #:
    #: Separate keys rather than a relaxed rule on `background`, and any hue is
    #: allowed here because setting them is itself the opt-in. `background`
    #: stays grayscale so the practice default cannot drift into competing with
    #: the hues that say which hand is playing.
    gradient_top: str = ""
    gradient_bottom: str = ""
    #: Optional effects, drawn in the order they are listed. Empty by default,
    #: because a practice aid and a piece of spectacle want opposite things.
    #:
    #: The order is the composition order: a halo under particles is a different
    #: picture from particles under a halo.
    effects: tuple[EffectConfig, ...] = ()
    #: How many processes draw and encode the video at once. 0 asks for one
    #: per core, 1 renders in a single process the way this always did.
    #:
    #: Frames are independent and `render_frame` is a pure function of the score
    #: and a time, so the timeline can be cut into spans and each span rendered
    #: on its own. Splitting is exact rather than approximate: `frame_times`
    #: computes `start + index / fps` rather than adding repeatedly, so a span
    #: beginning at frame k produces the same timestamps counting from zero
    #: would.
    workers: int = 0
    #: One of ENCODE_LEVELS. Trades file size against render time.
    encode: str = "balanced"
    colors: ColorConfig = field(default_factory=ColorConfig)
    grid: GridConfig = field(default_factory=GridConfig)

    @property
    def encoder_preset(self) -> str:
        """The x264 preset `encode` names."""
        return ENCODE_LEVELS[self.encode]

    @property
    def gradient(self) -> tuple[str, str] | None:
        """The background gradient's two colours, or None for a flat fill."""
        if self.gradient_top and self.gradient_bottom:
            return self.gradient_top, self.gradient_bottom
        return None

    def validate(self) -> None:
        for name in ("width", "height", "fps"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"visual.{name} must be positive")
        for name in ("width", "height"):
            # h264 encodes in 2x2 chroma blocks. Rejecting odd sizes here means
            # the encoder never quietly pads the frame to a size nobody asked for.
            if getattr(self, name) % 2:
                raise ConfigError(
                    f"visual.{name} must be even, got {getattr(self, name)}"
                )
        if self.lookahead_s <= 0:
            raise ConfigError("visual.lookahead_s must be positive")
        if not 0.0 < self.black_key_bar_width <= 1.0:
            raise ConfigError(
                "visual.black_key_bar_width must be greater than 0 and at most 1, "
                f"got {self.black_key_bar_width}"
            )
        if not 0.0 <= self.note_border <= 0.02:
            raise ConfigError(
                f"visual.note_border must be between 0 and 0.02, got {self.note_border}"
            )
        if not 0.0 <= self.note_radius <= 0.5:
            raise ConfigError(
                f"visual.note_radius must be between 0 and 0.5, got {self.note_radius}"
            )
        if not -1.0 <= self.note_border_shade <= 1.0:
            raise ConfigError(
                "visual.note_border_shade must be between -1 and 1, got "
                f"{self.note_border_shade}"
            )
        if not -1.0 <= self.bar_gradient <= 1.0:
            raise ConfigError(
                f"visual.bar_gradient must be between -1 and 1, got {self.bar_gradient}"
            )
        if bool(self.gradient_top) != bool(self.gradient_bottom):
            raise ConfigError(
                "visual.gradient_top and visual.gradient_bottom go together: "
                "set both for a gradient background, or neither for a flat one"
            )
        for name in ("gradient_top", "gradient_bottom"):
            value = getattr(self, name)
            if value and not is_hex(value):
                raise ConfigError(
                    f"visual.{name} must be a hex colour like '#140e28', got {value!r}"
                )
        if not 0.0 <= self.black_key_darkening <= 1.0:
            raise ConfigError(
                "visual.black_key_darkening must be between 0 and 1, "
                f"got {self.black_key_darkening}"
            )
        if self.workers < 0:
            raise ConfigError(f"visual.workers must be 0 or more, got {self.workers}")
        if self.encode not in ENCODE_LEVELS:
            raise ConfigError(
                f"visual.encode must be one of {', '.join(ENCODE_LEVELS)}, "
                f"got {self.encode!r}"
            )
        if not is_hex(self.background):
            raise ConfigError(
                f"visual.background must be a hex colour, got {self.background!r}"
            )
        # The spec asks for a grayscale background, and it is right to: any hue
        # back there competes with the hues that carry which-hand information.
        if not is_grayscale(parse_hex(self.background)):
            raise ConfigError(
                "visual.background must be grayscale so it cannot compete with "
                f"the note colours, got {self.background!r}"
            )
        for effect in self.effects:
            effect.validate()
        self.colors.validate()
        self.grid.validate()


@dataclass(frozen=True, slots=True)
class PedalsConfig:
    lanes: int = 1
    #: Controller value at or above which a pedal counts as engaged. The default
    #: shows half-pedalling; set it to 64 for the MIDI on/off convention.
    threshold: int = 1

    def validate(self) -> None:
        if not 0 <= self.lanes <= 3:
            raise ConfigError(f"pedals.lanes must be 0 to 3, got {self.lanes}")
        if not 1 <= self.threshold <= 127:
            raise ConfigError(
                f"pedals.threshold must be 1 to 127, got {self.threshold}"
            )


@dataclass(frozen=True, slots=True)
class AudioConfig:
    backend: str = "builtin"
    soundfont: str = ""
    #: Folder holding the native FluidSynth library. Given here rather than
    #: expected on PATH: pyfluidsynth finds the DLL through find_library, which
    #: searches PATH only, and editing a user's PATH to satisfy one optional
    #: backend is a poor trade.
    fluidsynth_bin: str = ""
    #: General MIDI program to play everything with. 0 is Acoustic Grand Piano;
    #: 4 and 5 are the electric pianos.
    program: int = 0
    audio_file: str = ""
    offset_s: float = 0.0
    #: Spread the built-in synth across the stereo field by register, low notes
    #: to the left and high to the right, as they sit at the instrument. 0 is
    #: mono; 1 puts the extremes hard left and hard right, wider than any real
    #: piano and mostly useful for hearing that it works.
    stereo_width: float = 0.5
    #: How much room the piano is played in. 0 is dry, 1 is a large hall.
    #:
    #: One number rather than FluidSynth's four. Exposing room size, damping,
    #: width and level means picking four numbers to find out that three of them
    #: barely matter, which is the difference between a setting you use and a
    #: setting you read about and skip.
    #:
    #: 0.5 is exactly what this always sounded like. FluidSynth enables its own
    #: reverb by default, so psv has never been dry, and the middle of this
    #: range is those defaults rather than a new opinion.
    #:
    #: fluidsynth only. The other backends do not go through it and say so.
    reverb: float = DEFAULT_REVERB

    def validate(self) -> None:
        if not 0.0 <= self.reverb <= 1.0:
            raise ConfigError(
                f"audio.reverb must be between 0 and 1, got {self.reverb}"
            )
        if not 0.0 <= self.stereo_width <= 1.0:
            raise ConfigError(
                f"audio.stereo_width must be between 0 and 1, got {self.stereo_width}"
            )
        if not 0 <= self.program <= 127:
            raise ConfigError(
                f"audio.program must be a GM program 0-127, got {self.program}"
            )
        if self.backend not in AUDIO_BACKENDS:
            raise ConfigError(
                f"audio.backend must be one of {AUDIO_BACKENDS}, got {self.backend!r}"
            )
        if self.backend == "fluidsynth" and not self.soundfont:
            raise ConfigError("audio.backend 'fluidsynth' requires audio.soundfont")
        if self.backend == "mux" and not self.audio_file:
            raise ConfigError("audio.backend 'mux' requires audio.audio_file")


@dataclass(frozen=True, slots=True)
class PracticeConfig:
    """How the finished arrangement is presented, not what it contains.

    None of these change a note. They are applied after arrange and constrain,
    so the same file practised at half speed with one hand is the same
    arrangement you get at full speed with both.
    """

    #: Playback speed. 0.75 renders at three-quarters of the written tempo.
    tempo: float = 1.0
    #: Which hand sounds. The other is still drawn, faintly.
    hands: str = "both"
    #: Bars of lead-in before the music starts. 0 for none.
    count_in_bars: int = 0
    #: Whether the lead-in clicks. False keeps the time and drops the sound,
    #: which is what you want once the falling notes are doing the counting:
    #: you can see the beat arriving, and the beeps are just noise over it.
    count_in_clicks: bool = True
    #: Keep clicking through the piece, not only into it.
    metronome: bool = False

    @property
    def is_default(self) -> bool:
        return self == PracticeConfig()

    def validate(self) -> None:
        if not MIN_TEMPO <= self.tempo <= MAX_TEMPO:
            raise ConfigError(
                f"practice.tempo must be between {MIN_TEMPO} and {MAX_TEMPO}, "
                f"got {self.tempo}"
            )
        if self.hands not in PRACTICE_HANDS:
            raise ConfigError(
                f"practice.hands must be one of {PRACTICE_HANDS}, got {self.hands!r}"
            )
        if not 0 <= self.count_in_bars <= MAX_COUNT_IN_BARS:
            raise ConfigError(
                f"practice.count_in_bars must be 0 to {MAX_COUNT_IN_BARS}, "
                f"got {self.count_in_bars}"
            )


@dataclass(frozen=True, slots=True)
class TitleConfig:
    """A title card at the front, and a fade to black at the end.

    Off until `seconds` is set, because a practice video wants neither.

    The card is a screen laid over the render with the music already playing
    behind it, fading to nothing rather than a separate clip spliced on the
    front. Two clips joined end to end cannot show the notes already falling,
    because in the first one there is nothing to see through to.
    """

    #: How long the card is on screen. 0 turns the whole feature off.
    seconds: float = 0.0
    #: The piece and who wrote it. Empty takes them from the score, which
    #: MusicXML carries and MIDI does not.
    text: str = ""
    composer: str = ""
    #: A third line, fainter and lower. For a channel name.
    footer: str = ""
    #: A .ttf or .otf file. Empty looks for a serif in the usual places and
    #: falls back to a built-in face rather than failing the render.
    font: str = ""
    #: The screen behind the text, and how opaque it starts.
    screen: str = "#0a0a0a"
    opacity: float = 1.0
    #: When the card reaches nothing, in seconds. 0 means a share of `seconds`.
    clear_at: float = 0.0
    #: One of TITLE_CURVES.
    curve: str = "ease"
    #: The end: a fade to black over this long, then this long held on black.
    fade_out_s: float = 0.0
    hold_s: float = 0.0

    @property
    def is_on(self) -> bool:
        return self.seconds > 0.0 or self.fade_out_s > 0.0 or self.hold_s > 0.0

    @property
    def clears_at(self) -> float:
        """When the card reaches nothing, resolved."""
        if self.clear_at > 0.0:
            return self.clear_at
        return self.seconds * TITLE_CLEAR_SHARE

    def validate(self) -> None:
        for name in ("seconds", "fade_out_s", "hold_s", "clear_at"):
            if getattr(self, name) < 0.0:
                raise ConfigError(f"title.{name} cannot be negative")
        if self.seconds > MAX_TITLE_SECONDS:
            raise ConfigError(
                f"title.seconds must be at most {MAX_TITLE_SECONDS}, got {self.seconds}"
            )
        if self.clear_at > self.seconds:
            raise ConfigError(
                f"title.clear_at is {self.clear_at} but the card is only "
                f"{self.seconds}s long, so it would never clear"
            )
        if self.curve not in TITLE_CURVES:
            raise ConfigError(
                f"title.curve must be one of {', '.join(TITLE_CURVES)}, "
                f"got {self.curve!r}"
            )
        if not 0.0 <= self.opacity <= 1.0:
            raise ConfigError(
                f"title.opacity must be between 0 and 1, got {self.opacity}"
            )
        if not is_hex(self.screen):
            raise ConfigError(f"title.screen must be a hex colour, got {self.screen!r}")


@dataclass(frozen=True, slots=True)
class Config:
    hands: HandsConfig = field(default_factory=HandsConfig)
    difficulty: DifficultyConfig = field(default_factory=DifficultyConfig)
    visual: VisualConfig = field(default_factory=VisualConfig)
    pedals: PedalsConfig = field(default_factory=PedalsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    practice: PracticeConfig = field(default_factory=PracticeConfig)
    title: TitleConfig = field(default_factory=TitleConfig)

    def validate(self) -> None:
        self.hands.validate()
        self.difficulty.validate()
        self.visual.validate()
        self.pedals.validate()
        self.audio.validate()
        self.practice.validate()
        self.title.validate()

    @classmethod
    def load(cls, path: Path | str | None) -> Self:
        """Load and validate a config file, or return defaults when given None."""
        if path is None:
            config = cls()
            config.validate()
            return config

        path = Path(path)
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except OSError as exc:
            raise ConfigError(f"could not read config {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

        config = cls.from_dict(raw)
        config.validate()
        return config

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        built: Self = _build(cls, raw, prefix="")
        return built


def _build(target: type[Any], raw: dict[str, Any], prefix: str) -> Any:
    """Recursively build a config dataclass, rejecting unknown keys.

    Silently dropping a misspelled key is the failure mode this exists to
    prevent: the render comes out wrong and nothing says why.
    """
    known = {f.name for f in fields(target)}
    unknown = set(raw) - known
    if unknown:
        location = prefix.rstrip(".") or "the config file"
        raise ConfigError(
            f"unknown key(s) in {location}: {', '.join(sorted(unknown))}. "
            f"Valid keys here: {', '.join(sorted(known))}"
        )

    # `from __future__ import annotations` leaves field.type as a string, so
    # resolve the real types rather than comparing against annotation text.
    hints = get_type_hints(target)

    values: dict[str, Any] = {}
    for name in known:
        if name not in raw:
            continue
        value = raw[name]
        hint = hints[name]
        if isinstance(hint, type) and is_dataclass(hint):
            if not isinstance(value, dict):
                raise ConfigError(
                    f"{prefix}{name} must be a table, got {type(value).__name__}"
                )
            values[name] = _build(hint, value, prefix=f"{prefix}{name}.")
        elif (item := _element_of(hint)) is not None:
            values[name] = _build_list(item, value, f"{prefix}{name}")
        else:
            values[name] = _coerce(value, hint, f"{prefix}{name}")
    return target(**values)


def _element_of(hint: Any) -> type[Any] | None:
    """The dataclass a ``tuple[Thing, ...]`` field holds, if it is one.

    Only this shape. A field typed as a tuple of anything else would be a new
    kind of config value with its own error messages, and there is one list in
    this config: the effects.
    """
    if get_origin(hint) is not tuple:
        return None
    args = get_args(hint)
    if (
        len(args) == 2
        and args[1] is Ellipsis
        and isinstance(args[0], type)
        and is_dataclass(args[0])
    ):
        return args[0]
    return None


def _build_list(element: type[Any], value: Any, location: str) -> tuple[Any, ...]:
    """Build an array of tables, such as ``[[visual.effects]]``."""
    if not isinstance(value, list):
        raise ConfigError(
            f"{location} must be a list of tables, got {type(value).__name__}"
        )
    built = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(
                f"{location}[{index}] must be a table, got {type(item).__name__}"
            )
        built.append(_build(element, item, prefix=f"{location}[{index}]."))
    return tuple(built)


def _coerce(value: Any, expected: Any, location: str) -> Any:
    """Accept an int where a float is wanted; reject anything else mistyped.

    Booleans are checked separately because TOML has them and Python makes
    ``bool`` a subclass of ``int``, so ``pedals.lanes = true`` would otherwise
    be accepted as 1.
    """
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    mistyped = expected in (int, float, str) and not isinstance(value, expected)
    swapped = isinstance(value, bool) is not (expected is bool)
    if expected in (bool, int, float, str) and (mistyped or swapped):
        raise ConfigError(
            f"{location} must be {expected.__name__}, got "
            f"{type(value).__name__} ({value!r})"
        )
    return value
