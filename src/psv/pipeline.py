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
from psv.midi import read_midi_file
from psv.model import Score
from psv.render.video import render_video

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Everything that happened, so the CLI can report it without re-deriving."""

    output: Path
    score: Score
    arranged: ArrangeResult
    constrained: ConstrainResult
    audio: AudioResult

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
        lines.append(f"  notes            {len(self.score.notes)}")
        return "\n".join(lines)


def run(
    source: Path | str,
    output: Path | str,
    config: Config,
    *,
    start: float = 0.0,
    duration: float | None = None,
    on_frame: Callable[[int, int], None] | None = None,
) -> PipelineResult:
    """Run every stage and write a finished video.

    The video is rendered silent to a temporary file and the soundtrack muxed on
    afterwards, rather than being interleaved. That keeps the renderer a pure
    function of the score and means a failure in either half says which half.
    """
    output = Path(output)
    score = read_midi_file(source)

    arranged = arrange(
        score,
        max_span=config.hands.max_span_semitones,
        tolerance=config.hands.overlap_tolerance_s,
    )
    constrained = constrain(arranged.score, config)
    final = constrained.score

    with tempfile.TemporaryDirectory(prefix="psv-") as scratch:
        workspace = Path(scratch)
        silent = render_video(
            final,
            config.visual,
            workspace / "video.mp4",
            start=start,
            duration=duration,
            pedal_lanes=config.pedals.lanes,
            on_frame=on_frame,
        )

        audio = render_audio(
            final, config.audio, workspace, start=start, duration=duration
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
    )


def _copy(source: Path, destination: Path) -> None:
    """Copy across filesystems, since the scratch directory may be elsewhere."""
    import shutil

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
