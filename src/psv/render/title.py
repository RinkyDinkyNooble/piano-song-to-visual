"""A title card at the front, and a fade to black at the end.

A pass over a finished video rather than part of drawing it, for two reasons.

The card is a screen laid over the render with the music already playing behind
it, fading to nothing. Two clips joined end to end cannot show that: in the
first one there is nothing to see through to. An overlay whose alpha falls with
time can, and it needs no cooperation from the renderer at all.

And the end of the piece needs frames that no note occupies. Holding black past
the last note means asking for time the score does not have, which the frame
renderer has no way to express; `tpad` does it in one flag.

The cost is one re-encode of the finished file. Everything up to here is copied
rather than re-encoded, so this is the only place a psv render loses a
generation, which is why it is off until a title is asked for.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from psv.audio.backends import ffmpeg_exe
from psv.config import TitleConfig
from psv.rgb import parse_hex
from psv.text import MIN_SIZE, fit_text

if TYPE_CHECKING:  # pragma: no cover - types only
    from PIL import ImageDraw
    from PIL.ImageFont import FreeTypeFont
    from PIL.ImageFont import ImageFont as BitmapFont

    AnyFont = FreeTypeFont | BitmapFont

log = logging.getLogger(__name__)


class TitleError(RuntimeError):
    """The title pass could not be applied."""


#: How the card's opacity falls, as an ffmpeg expression in `t`, which runs 0
#: to 1 across the clearing time. The commas are escaped because ffmpeg splits
#: a filter chain on them before any expression is parsed.
CURVES = {
    "ease": "pow(1-min(T/{clear}\\,1)\\,2)",
    "linear": "(1-min(T/{clear}\\,1))",
    "slow": "sqrt(1-min(T/{clear}\\,1))",
}

#: Type sizes, as a fraction of the frame height.
TITLE_SIZE = 0.062
COMPOSER_SIZE = 0.028
FOOTER_SIZE = 0.022

#: Space added between letters, as a fraction of the type size. Wide-tracked
#: capitals are most of what makes a title card look like one, and Pillow has
#: no setting for it, so the glyphs are drawn one at a time.
TITLE_TRACKING = 0.34
SMALL_TRACKING = 0.30

#: Where each line sits down the frame.
TITLE_Y = 0.38
COMPOSER_Y = 0.52
FOOTER_Y = 0.70

#: How much of the frame's width the type may use. The margin is what stops a
#: long title reading as though it had been cropped.
TEXT_WIDTH_SHARE = 0.82

#: Leading between wrapped lines, as a fraction of the type size.
LINE_SPACING = 1.25

#: Clear space kept under a block that has grown past where the next one sits,
#: as a fraction of the frame height.
BLOCK_GAP = 0.03

TITLE_INK = (245, 243, 238, 255)
COMPOSER_INK = (188, 184, 176, 255)
RULE_INK = (140, 137, 130, 255)
FOOTER_INK = (128, 124, 118, 255)

#: Looked at in order for a serif face when `title.font` is empty. A missing
#: font is never fatal: the card falls back to a built-in face and says so,
#: because a video is worth more than the typeface it was going to be set in.
FONT_NAMES = ("georgia.ttf", "Georgia.ttf", "times.ttf", "DejaVuSerif.ttf")
FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts"),
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
)


@dataclass(frozen=True, slots=True)
class _Block:
    """Where one drawn block of type ended up, so the next can clear it."""

    left: float
    span: float
    size: int
    bottom: float


@dataclass(frozen=True, slots=True)
class Card:
    """What the card says, after the score and the config have both had a say."""

    title: str
    composer: str
    footer: str

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.composer or self.footer)


def card_text(config: TitleConfig, *, title: str = "", composer: str = "") -> Card:
    """What to print: the config's words, or the score's where it has none.

    Config wins so a bad transcription can be overridden without editing the
    file, and the score fills in so the usual case needs nothing typed.
    """
    return Card(
        title=(config.text or title).strip(),
        composer=(config.composer or composer).strip(),
        footer=config.footer.strip(),
    )


def find_font(name: str = "") -> Path | None:
    """A font file, or None to fall back to something built in."""
    if name:
        path = Path(name).expanduser()
        if not path.is_file():
            raise TitleError(f"no font file at {path}")
        return path
    for directory in FONT_DIRS:
        for candidate in FONT_NAMES:
            found = directory / candidate
            if found.is_file():
                return found
    return None


def _tracked_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: AnyFont,
    size: int,
    tracking: float,
) -> float:
    """The size is passed rather than read off the font.

    The built-in fallback face has no `size` attribute, and it is only ever
    reached when no font file could be found, which must not be the one case
    that crashes.
    """
    extra = tracking * size
    glyphs = sum(float(draw.textlength(char, font=font)) for char in text)
    return glyphs + extra * max(0, len(text) - 1)


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: AnyFont,
    size: int,
    tracking: float,
    ink: tuple[int, int, int, int],
) -> None:
    extra = tracking * size
    for char in text:
        draw.text((x, y), char, font=font, fill=ink)
        x += float(draw.textlength(char, font=font)) + extra


def build_card(
    config: TitleConfig, card: Card, width: int, height: int, path: Path
) -> Path:
    """Draw the card once, to an RGBA png. The fade animates its alpha."""
    from PIL import Image, ImageDraw, ImageFont

    screen = parse_hex(config.screen)
    image = Image.new("RGBA", (width, height), (*screen, round(255 * config.opacity)))
    draw = ImageDraw.Draw(image)

    source = find_font(config.font)
    if source is None:
        log.warning("no serif font found; setting the title card in the default face")

    def face(fraction: float, size: int | None = None) -> tuple[AnyFont, int]:
        if size is None:
            size = max(MIN_SIZE, round(height * fraction))
        if source is None:
            return ImageFont.load_default(size), size
        return ImageFont.truetype(str(source), size), size

    room = width * TEXT_WIDTH_SHARE

    def measured(fraction: float, tracking: float) -> Callable[[str, int], float]:
        """Width of a string at a size, with this line's tracking included."""

        def width_of(text: str, size: int) -> float:
            font, _ = face(fraction, size)
            return _tracked_width(draw, text, font, size, tracking)

        return width_of

    def set_line(
        text: str,
        fraction: float,
        tracking: float,
        top: float,
        ink: tuple[int, int, int, int],
    ) -> _Block:
        """Draw one centred line, shrunk and wrapped to fit. Returns its box."""
        asked = max(MIN_SIZE, round(height * fraction))
        fitted = fit_text(
            text,
            width=room,
            size=asked,
            measure=measured(fraction, tracking),
        )
        if not fitted.fits:
            log.warning("%r does not fit the card even at its smallest", text)

        font, size = face(fraction, fitted.size)
        widest, left_of_widest = 0.0, 0.0
        y = top
        for line in fitted.lines:
            span = _tracked_width(draw, line, font, size, tracking)
            left = (width - span) / 2
            _draw_tracked(draw, left, y, line, font, size, tracking, ink)
            if span > widest:
                widest, left_of_widest = span, left
            y += size * LINE_SPACING
        return _Block(left=left_of_widest, span=widest, size=size, bottom=y)

    # Each block sits where its fraction says, unless the one above wrapped and
    # grew down into it. Pushing down rather than leaving them to overlap: the
    # positions are chosen for the usual one-line card, and a title that needed
    # two lines has taken room the composer was going to use.
    floor_y = 0.0
    if card.title:
        title_block = set_line(
            card.title.upper(), TITLE_SIZE, TITLE_TRACKING, height * TITLE_Y, TITLE_INK
        )
        floor_y = title_block.bottom + height * BLOCK_GAP

    if card.composer:
        top = max(height * COMPOSER_Y, floor_y)
        composer_block = set_line(
            card.composer.upper(), COMPOSER_SIZE, SMALL_TRACKING, top, COMPOSER_INK
        )
        left, span, size = (
            composer_block.left,
            composer_block.span,
            composer_block.size,
        )
        floor_y = composer_block.bottom + height * BLOCK_GAP

        # A rule either side, set on the type's midline.
        gap = size * 1.4
        length = size * 2.2
        middle = top + size * 0.62
        for start, end in (
            (left - gap - length, left - gap),
            (left + span + gap, left + span + gap + length),
        ):
            draw.line([(start, middle), (end, middle)], fill=RULE_INK, width=1)

    if card.footer:
        set_line(
            card.footer.upper(),
            FOOTER_SIZE,
            SMALL_TRACKING,
            max(height * FOOTER_Y, floor_y),
            FOOTER_INK,
        )

    image.save(path)
    return path


def probe(path: Path) -> tuple[int, int, float, float]:
    """Width, height, frame rate and duration of a video."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - depends on install
        raise TitleError(f"the title pass needs imageio-ffmpeg: {exc}") from exc

    reader = imageio_ffmpeg.read_frames(str(path))
    try:
        meta = next(reader)
    finally:
        reader.close()
    width, height = meta["size"]
    return int(width), int(height), float(meta["fps"]), float(meta["duration"])


def filter_chain(config: TitleConfig, duration: float, *, has_audio: bool) -> str:
    """The one filter graph that does all of it.

    `geq` runs per pixel per frame, but only over the card's own frames, so it
    costs a few dozen frames rather than the whole piece.
    """
    steps: list[str] = []
    video = "[0:v]"

    tail: list[str] = []
    if config.fade_out_s > 0.0:
        start = max(0.0, duration - config.fade_out_s)
        tail.append(f"fade=t=out:st={start:.3f}:d={config.fade_out_s:g}")
    if config.hold_s > 0.0:
        tail.append(f"tpad=stop_mode=add:stop_duration={config.hold_s:g}:color=black")
    steps.append(f"{video}{','.join(tail) if tail else 'null'}[base]")

    if config.seconds > 0.0:
        alpha = CURVES[config.curve].format(clear=f"{config.clears_at:.4f}")
        steps.append(
            "[1:v]format=rgba,"
            "geq=r='r(X\\,Y)':g='g(X\\,Y)':b='b(X\\,Y)':"
            f"a='alpha(X\\,Y)*{alpha}'[card]"
        )
        steps.append("[base][card]overlay=0:0:eof_action=pass[v]")
    else:
        steps.append("[base]null[v]")

    if has_audio:
        audio: list[str] = []
        if config.fade_out_s > 0.0:
            start = max(0.0, duration - config.fade_out_s)
            audio.append(f"afade=t=out:st={start:.3f}:d={config.fade_out_s:g}")
        if config.hold_s > 0.0:
            audio.append(f"apad=pad_dur={config.hold_s:g}")
        steps.append(f"[0:a]{','.join(audio) if audio else 'anull'}[a]")

    return ";".join(steps)


def has_audio_stream(path: Path) -> bool:
    """Whether the file carries a soundtrack.

    The pipeline writes a silent video when every audio backend fails, and
    mapping an audio stream that is not there makes ffmpeg refuse the whole
    graph rather than the one filter.
    """
    try:
        import imageio_ffmpeg
    except ImportError:  # pragma: no cover - depends on install
        return False
    reader = imageio_ffmpeg.read_frames(str(path))
    try:
        meta = next(reader)
    finally:
        reader.close()
    return bool(meta.get("audio_codec"))


def add_title(
    video: Path,
    output: Path,
    config: TitleConfig,
    *,
    title: str = "",
    composer: str = "",
    workspace: Path | None = None,
) -> Path:
    """Put the card and the fades onto ``video``, writing ``output``."""
    if not config.is_on:
        raise TitleError("the title pass was asked for with nothing turned on")

    width, height, fps, duration = probe(video)
    card = card_text(config, title=title, composer=composer)

    inputs = [str(video)]
    scratch = workspace or output.parent
    card_path = scratch / f".{output.stem}-card.png"
    draw_card = config.seconds > 0.0 and not card.is_empty
    if config.seconds > 0.0 and card.is_empty:
        log.warning("title.seconds is set but there is nothing to print on the card")

    effective = config
    if not draw_card:
        effective = _without_card(config)
        if not effective.is_on:
            raise TitleError("nothing to draw and nothing to fade")

    chain = filter_chain(effective, duration, has_audio=has_audio_stream(video))

    command = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", inputs[0]]
    if draw_card:
        build_card(config, card, width, height, card_path)
        command += ["-loop", "1", "-t", f"{config.seconds:g}", "-i", str(card_path)]
    command += ["-filter_complex", chain, "-map", "[v]"]
    if "[a]" in chain:
        command += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        f"{fps:g}",
        "-movflags",
        "+faststart",
        str(output),
    ]

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, check=False
        )
    finally:
        card_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise TitleError(f"ffmpeg could not add the title: {result.stderr.strip()}")
    return output


def _without_card(config: TitleConfig) -> TitleConfig:
    from dataclasses import replace

    return replace(config, seconds=0.0)


def summary(config: TitleConfig, card: Card) -> str:
    """One line for the CLI, saying what the pass did."""
    parts: list[str] = []
    if config.seconds > 0.0 and not card.is_empty:
        named = card.title or "untitled"
        if card.composer:
            named += f", {card.composer}"
        parts.append(f"{config.seconds:g}s card ({named})")
    if config.fade_out_s > 0.0:
        parts.append(f"{config.fade_out_s:g}s fade out")
    if config.hold_s > 0.0:
        parts.append(f"{config.hold_s:g}s held black")
    return ", ".join(parts)
