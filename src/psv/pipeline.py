"""The whole thing, end to end.

MIDI in, practice video out. Each stage still stands alone and can be run by
itself on an intermediate file; this module is just the order they go in, and
the one place that knows a video needs a soundtrack muxed onto it.

    parse -> arrange -> constrain -> render -> synthesise -> mux
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from psv.arrange import ArrangeResult, arrange
from psv.audio import AudioResult, mux_into_video, render_audio
from psv.audio.backends import AudioError
from psv.config import Config
from psv.constraints import ConstrainResult, constrain
from psv.load import read_score
from psv.model import Score
from psv.practice import prepare
from psv.render.video import TAIL_S, render_video

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Everything that happened, so the CLI can report it without re-deriving."""

    output: Path
    score: Score
    arranged: ArrangeResult
    constrained: ConstrainResult
    audio: AudioResult
    #: What the practice settings did, empty when they were all defaults.
    practice: str = ""

    def summary(self) -> str:
        lines = [
            f"  arrange          {self.arranged.summary()}",
            f"  constrain        {self.constrained.summary().splitlines()[0]}",
        ]
        for strategy, count in sorted(self.constrained.counts.items()):
            lines.append(f"    {strategy:14} {count}")
        audio = self.audio.backend
        if self.audio.note:
            audio += f" ({self.audio.note})"
        lines.append(f"  audio            {audio}")
        if self.practice:
            lines.append(f"  practice         {self.practice}")
        lines.append(f"  notes            {len(self.score.notes)}")
        return "\n".join(lines)


def run(
    source: Path | str,
    output: Path | str,
    config: Config,
    *,
    start: float = 0.0,
    duration: float | None = None,
    bars: tuple[int, int] | None = None,
    on_frame: Callable[[int, int], None] | None = None,
) -> PipelineResult:
    """Run every stage and write a finished video.

    The video is rendered silent to a temporary file and the soundtrack muxed on
    afterwards, rather than being interleaved. That keeps the renderer a pure
    function of the score and means a failure in either half says which half.

    The practice settings in ``config`` are applied last of all, after the
    arrangement is settled, so the same file gives the same arrangement whatever
    speed or section you asked to practise.
    """
    output = Path(output)
    score = read_score(source)

    arranged = arrange(
        score,
        max_span=config.hands.layout_span,
        tolerance=config.hands.overlap_tolerance_s,
    )
    constrained = constrain(arranged.score, config)

    show = prepare(
        constrained.score,
        config.practice,
        start=start,
        seconds=duration,
        bars=bars,
        tail=TAIL_S,
    )
    final = show.score
    audible = show.audio_score
    if show.focus is not None and audible.is_empty:
        log.warning(
            "no notes are assigned to the %s hand; the soundtrack will be silent",
            show.focus.value,
        )

    with tempfile.TemporaryDirectory(prefix="psv-") as scratch:
        workspace = Path(scratch)
        silent = render_video(
            final,
            config.visual,
            workspace / "video.mp4",
            start=show.start,
            duration=show.duration,
            pedal_lanes=config.pedals.lanes,
            focus=show.focus,
            on_frame=on_frame,
        )

        audio = render_audio(
            audible,
            config.audio,
            workspace,
            start=show.start,
            duration=show.duration,
            clicks=show.clicks,
        )

        if audio.path is None:
            output.parent.mkdir(parents=True, exist_ok=True)
            silent.replace(output) if silent.drive == output.drive else _copy(
                silent, output
            )
        else:
            try:
                mux_into_video(
                    silent, audio.path, output, offset_s=config.audio.offset_s
                )
            except AudioError as exc:
                # A soundtrack that will not mux is not worth losing the video
                # over: hand back the picture and say what went wrong.
                log.warning("%s; writing the video without audio", exc)
                _copy(silent, output)
                audio = AudioResult(path=None, backend="none", note=str(exc))

    log.info("wrote %s", output)
    return PipelineResult(
        output=output,
        score=final,
        arranged=arranged,
        constrained=constrained,
        audio=audio,
        practice=show.label,
    )


def _copy(source: Path, destination: Path) -> None:
    """Copy across filesystems, since the scratch directory may be elsewhere."""
    import shutil

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
