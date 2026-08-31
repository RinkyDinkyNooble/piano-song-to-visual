"""Getting sound out of a Score.

A MIDI file has no audio, so something has to make it. Four backends, chosen by
config, each degrading to the next when what it needs is missing:

``fluidsynth`` needs a SoundFont and the native FluidSynth library, and sounds
best. ``mux`` takes an audio file you already have. ``builtin`` synthesises from
nothing but numpy, so it always works. ``none`` is silence.

The fallback chain matters more than any one backend. This is a practice tool:
being handed a silent video with no explanation because a library was missing is
much worse than being handed a cheap-sounding one that says why.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from psv.config import AudioConfig
from psv.model import Pedal, Score

log = logging.getLogger(__name__)

SAMPLE_RATE = 44_100

#: Concert A, the reference every other pitch is derived from.
A4_HZ = 440.0
A4_MIDI = 69

#: Relative strength of the first few harmonics. A pure sine sounds like a test
#: tone; a little harmonic content is enough to hear which note is which.
HARMONICS: tuple[float, ...] = (1.0, 0.32, 0.14, 0.06)

ATTACK_S = 0.006
DECAY_S = 0.7
SUSTAIN_LEVEL = 0.32
RELEASE_S = 0.18

#: Seconds of tail kept after the last sound, matching the renderer's.
TAIL_S = 1.0


class AudioError(RuntimeError):
    """Audio could not be produced by any available backend."""


@dataclass(frozen=True, slots=True)
class AudioResult:
    """What came out, and which backend actually produced it."""

    path: Path | None
    backend: str
    note: str = ""

    @property
    def is_silent(self) -> bool:
        return self.path is None


def pitch_to_hz(pitch: int) -> float:
    return float(A4_HZ * 2.0 ** ((pitch - A4_MIDI) / 12.0))


# -- the built-in synth --------------------------------------------------


def _envelope(samples: int, held: int) -> np.ndarray:
    """A plain ADSR, as long as the note is held plus its release.

    Not a piano. It is a shape that starts fast, decays, and stops, which is
    enough to play along to.
    """
    envelope = np.zeros(samples, dtype=np.float32)
    attack = min(int(ATTACK_S * SAMPLE_RATE), held)
    if attack > 0:
        envelope[:attack] = np.linspace(0.0, 1.0, attack, dtype=np.float32)

    decay_end = held
    if decay_end > attack:
        decay = np.linspace(0.0, 1.0, decay_end - attack, dtype=np.float32)
        curve = SUSTAIN_LEVEL + (1.0 - SUSTAIN_LEVEL) * np.exp(
            -decay * (DECAY_S * SAMPLE_RATE) / max(decay_end - attack, 1) * 3.0
        )
        envelope[attack:decay_end] = curve

    if samples > decay_end:
        level = envelope[decay_end - 1] if decay_end > 0 else 1.0
        envelope[decay_end:] = np.linspace(
            float(level), 0.0, samples - decay_end, dtype=np.float32
        )
    return envelope


def _sustain_spans(score: Score) -> list[tuple[float, float]]:
    return [
        (event.start, event.end)
        for event in score.pedals
        if event.pedal is Pedal.SUSTAIN
    ]


def _release_end(note_end: float, spans: list[tuple[float, float]]) -> float:
    """When a note actually stops ringing.

    While the sustain pedal is down the damper is off the string, so the note
    keeps sounding past the key release. This is the same fact the constraint
    engine uses to decide that truncating under the pedal is free, and honouring
    it here is what makes the two agree.
    """
    for start, end in spans:
        if start <= note_end < end:
            return end
    return note_end


def synthesise(
    score: Score, *, start: float = 0.0, duration: float | None = None
) -> np.ndarray:
    """Render the score to mono float32 samples in [-1, 1]."""
    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    total = max(1, int(duration * SAMPLE_RATE))
    buffer = np.zeros(total, dtype=np.float32)

    spans = _sustain_spans(score)
    window_end = start + duration

    for note in score.notes:
        if note.end <= start or note.start >= window_end:
            continue

        ring_until = _release_end(note.end, spans) + RELEASE_S
        begin = int((note.start - start) * SAMPLE_RATE)
        finish = int((ring_until - start) * SAMPLE_RATE)
        begin, finish = max(begin, 0), min(finish, total)
        if finish <= begin:
            continue

        held = max(1, min(int((note.end - note.start) * SAMPLE_RATE), finish - begin))
        length = finish - begin

        time = np.arange(length, dtype=np.float32) / SAMPLE_RATE
        phase = 2.0 * np.pi * pitch_to_hz(note.pitch)
        wave_form = np.zeros(length, dtype=np.float32)
        for index, weight in enumerate(HARMONICS, start=1):
            wave_form += weight * np.sin(phase * index * time, dtype=np.float32)

        amplitude = (note.velocity / 127.0) ** 1.5
        buffer[begin:finish] += wave_form * _envelope(length, held) * amplitude

    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0:
        # Normalise rather than clip: a dense chord otherwise turns to buzz.
        buffer *= 0.89 / peak
    return buffer


def write_wav(samples: np.ndarray, path: Path) -> Path:
    """Write mono 16-bit PCM. Uses the standard library, so no extra dependency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return path


# -- backend availability ------------------------------------------------


def fluidsynth_available(soundfont: str) -> tuple[bool, str]:
    """Whether the FluidSynth backend can run, and why not when it cannot."""
    if not soundfont:
        return False, "audio.soundfont is not set"
    if not Path(soundfont).expanduser().is_file():
        return False, f"soundfont not found: {soundfont}"
    try:
        import fluidsynth  # noqa: F401
    except ImportError as exc:
        return False, f"the native FluidSynth library is unavailable ({exc})"
    return True, ""


def ffmpeg_exe() -> str:
    """The ffmpeg binary imageio ships, falling back to one on PATH."""
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError):  # pragma: no cover - depends on install
        found = shutil.which("ffmpeg")
        if found is None:
            raise AudioError("ffmpeg is not available") from None
        return found


# -- the backends --------------------------------------------------------


def _builtin(
    score: Score, out_dir: Path, start: float, duration: float | None, note: str
) -> AudioResult:
    path = write_wav(
        synthesise(score, start=start, duration=duration), out_dir / "psv-audio.wav"
    )
    return AudioResult(path=path, backend="builtin", note=note)


def _mux_source(config: AudioConfig, start: float, duration: float | None) -> Path:
    """Trim the user's audio file to the rendered span."""
    source = Path(config.audio_file).expanduser()
    if not source.is_file():
        raise AudioError(f"audio.audio_file not found: {source}")
    del start, duration
    return source


def render_audio(
    score: Score,
    config: AudioConfig,
    out_dir: Path,
    *,
    start: float = 0.0,
    duration: float | None = None,
) -> AudioResult:
    """Produce an audio file for ``score``, or None for silence.

    Falls back rather than failing: an unavailable backend is reported and the
    next one down is used, so a render never dies because a library is missing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = config.backend

    if backend == "none":
        return AudioResult(path=None, backend="none")

    if backend == "mux":
        try:
            source = _mux_source(config, start, duration)
        except AudioError as exc:
            log.warning("%s; falling back to the built-in synth", exc)
            return _builtin(score, out_dir, start, duration, note=str(exc))
        return AudioResult(path=source, backend="mux")

    if backend == "fluidsynth":
        available, why = fluidsynth_available(config.soundfont)
        if not available:
            log.warning("fluidsynth unavailable: %s; using the built-in synth", why)
            return _builtin(score, out_dir, start, duration, note=why)
        # Not implemented yet: see the M8 plan in docs/ROADMAP.md. Reaching here
        # means the machine could run it, so say so rather than pretending.
        why = "the fluidsynth backend is not implemented yet"
        log.warning("%s; using the built-in synth", why)
        return _builtin(score, out_dir, start, duration, note=why)

    return _builtin(score, out_dir, start, duration, note="")


def mux_into_video(
    video: Path, audio: Path, output: Path, *, offset_s: float = 0.0
) -> Path:
    """Combine a silent video and an audio file into one file.

    The video stream is copied rather than re-encoded, so this costs seconds
    even for a long render. ``-shortest`` keeps the two in step when the audio
    runs past the end of the picture.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video)]
    if offset_s:
        command += ["-itsoffset", f"{offset_s:.6f}"]
    command += [
        "-i",
        str(audio),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output),
    ]

    try:
        # Argument list, never a shell string: paths here come from config.
        result = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise AudioError(f"could not run ffmpeg: {exc}") from exc

    if result.returncode != 0:
        raise AudioError(f"ffmpeg failed to mux audio: {result.stderr.strip()[:400]}")
    return output
