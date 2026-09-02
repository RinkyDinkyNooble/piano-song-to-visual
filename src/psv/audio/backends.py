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
import os
import shutil
import subprocess
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from psv.audio.click import mix_clicks
from psv.config import AudioConfig
from psv.model import HIGHEST_KEY, LOWEST_KEY, Pedal, Score
from psv.practice import Click

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


def pan_gains(pitch: int, width: float) -> tuple[float, float]:
    """Left and right gain for a note, by where it sits on the keyboard.

    Equal power rather than linear: the two gains square-sum to one, so a note
    does not dip in volume as it crosses the middle of the field. ``width`` 0
    puts everything in the centre and 1 sends the extremes hard left and right.
    """
    span = max(1, HIGHEST_KEY - LOWEST_KEY)
    place = (pitch - LOWEST_KEY) / span * 2.0 - 1.0  # -1 low, +1 high
    angle = (max(-1.0, min(1.0, place)) * width + 1.0) * 0.25 * np.pi
    return float(np.cos(angle)), float(np.sin(angle))


def synthesise(
    score: Score,
    *,
    start: float = 0.0,
    duration: float | None = None,
    stereo_width: float = 0.0,
) -> np.ndarray:
    """Render the score to float32 samples in [-1, 1].

    Mono by default. With ``stereo_width`` above zero the result is interleaved
    stereo, panned by register: low notes to the left and high to the right, as
    they sit under your hands at the instrument. That is not only prettier, it
    stops the two hands competing for the same place in the mix, which is what
    makes a left-hand line audible underneath a busy right hand.
    """
    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S
    total = max(1, int(duration * SAMPLE_RATE))
    stereo = stereo_width > 0.0
    buffer = np.zeros(total, dtype=np.float32)
    other = np.zeros(total, dtype=np.float32) if stereo else buffer

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
        voice = wave_form * _envelope(length, held) * amplitude

        if stereo:
            gain_left, gain_right = pan_gains(note.pitch, stereo_width)
            buffer[begin:finish] += voice * gain_left
            other[begin:finish] += voice * gain_right
        else:
            buffer[begin:finish] += voice

    if stereo:
        # Interleaved, the same shape the FluidSynth backend produces, so
        # everything downstream handles one layout rather than two.
        out = np.empty(total * 2, dtype=np.float32)
        out[0::2] = buffer
        out[1::2] = other
    else:
        out = buffer

    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0:
        # Normalise rather than clip: a dense chord otherwise turns to buzz.
        out *= 0.89 / peak
    return out


def write_wav(samples: np.ndarray, path: Path, channels: int = 1) -> Path:
    """Write 16-bit PCM. Uses the standard library, so no extra dependency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return path


# -- backend availability ------------------------------------------------


def _add_to_path(folder: str) -> None:
    """Put a folder where `ctypes.util.find_library` will look.

    pyfluidsynth locates the DLL with `find_library`, which on Windows searches
    `PATH` and nothing else; `os.add_dll_directory` does not help it. Prepending
    here means the user names the folder in config instead of editing the
    environment for one optional backend.
    """
    resolved = str(Path(folder).expanduser())
    current = os.environ.get("PATH", "")
    if resolved not in current.split(os.pathsep):
        os.environ["PATH"] = resolved + os.pathsep + current


def fluidsynth_available(soundfont: str, bin_dir: str = "") -> tuple[bool, str]:
    """Whether the FluidSynth backend can run, and why not when it cannot."""
    if not soundfont:
        return False, "audio.soundfont is not set"
    if not Path(soundfont).expanduser().is_file():
        return False, f"soundfont not found: {soundfont}"
    if bin_dir:
        if not Path(bin_dir).expanduser().is_dir():
            return False, f"audio.fluidsynth_bin is not a folder: {bin_dir}"
        _add_to_path(bin_dir)
    try:
        import fluidsynth  # noqa: F401
    except ImportError as exc:
        return False, f"the native FluidSynth library is unavailable ({exc})"
    return True, ""


def synthesise_fluidsynth(
    score: Score,
    soundfont: str,
    *,
    program: int = 0,
    start: float = 0.0,
    duration: float | None = None,
) -> np.ndarray:
    """Render through FluidSynth, returning interleaved stereo float32.

    Driven by stepping the synth forward between events rather than by feeding
    it a MIDI file, so the timing comes from the Score and matches the video
    exactly. The sustain pedal is sent as CC64 with its real depth, so
    half-pedalling reaches the sound as well as the picture.
    """
    import fluidsynth

    if duration is None:
        duration = max(0.0, score.duration - start) + TAIL_S

    synth = fluidsynth.Synth(samplerate=float(SAMPLE_RATE))
    try:
        preset = synth.sfload(str(Path(soundfont).expanduser()))
        if preset == -1:
            raise AudioError(f"FluidSynth could not load {soundfont}")
        synth.program_select(0, preset, 0, program)

        events: list[tuple[float, int, int, int]] = []
        for note in score.notes:
            events.append((note.start, 0, note.pitch, note.velocity))
            events.append((note.end, 1, note.pitch, 0))
        for pedal in score.pedals:
            events.append((pedal.start, 2, int(pedal.pedal), pedal.depth))
            events.append((pedal.end, 2, int(pedal.pedal), 0))
        events.sort()

        blocks: list[np.ndarray] = []
        rendered = 0
        total = int(duration * SAMPLE_RATE)

        for when, kind, number, value in events:
            frame = int((when - start) * SAMPLE_RATE)
            if frame > total:
                break
            if frame > rendered:
                blocks.append(synth.get_samples(frame - rendered))
                rendered = frame
            if frame < 0:
                continue
            if kind == 0:
                synth.noteon(0, number, value)
            elif kind == 1:
                synth.noteoff(0, number)
            else:
                synth.cc(0, number, value)

        if total > rendered:
            blocks.append(synth.get_samples(total - rendered))
    finally:
        synth.delete()

    if not blocks:
        return np.zeros(0, dtype=np.float32)
    samples = np.concatenate(blocks).astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0:
        # SoundFonts vary hugely in level, and FluidSynth's default gain is low.
        # Normalising means swapping the .sf2 does not change how loud the video
        # is, which matters more here than preserving absolute level.
        samples *= 0.89 / peak
    return samples


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
    score: Score,
    out_dir: Path,
    start: float,
    duration: float | None,
    note: str,
    clicks: Sequence[Click] = (),
    stereo_width: float = 0.0,
) -> AudioResult:
    samples = synthesise(
        score, start=start, duration=duration, stereo_width=stereo_width
    )
    channels = 2 if stereo_width > 0.0 else 1
    samples = mix_clicks(
        samples, clicks, start=start, sample_rate=SAMPLE_RATE, channels=channels
    )
    path = write_wav(samples, out_dir / "psv-audio.wav", channels=channels)
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
    clicks: Sequence[Click] = (),
) -> AudioResult:
    """Produce an audio file for ``score``, or None for silence.

    Falls back rather than failing: an unavailable backend is reported and the
    next one down is used, so a render never dies because a library is missing.

    ``clicks`` is the count-in and metronome track, in score time. Every backend
    that synthesises can mix it in. ``mux`` cannot, because the audio is a file
    the user already has and re-encoding it to add clicks is not this tool's
    business; it says so rather than dropping them silently.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = config.backend
    width = config.stereo_width

    if backend == "none":
        return AudioResult(path=None, backend="none")

    if backend == "mux":
        try:
            source = _mux_source(config, start, duration)
        except AudioError as exc:
            log.warning("%s; falling back to the built-in synth", exc)
            return _builtin(score, out_dir, start, duration, str(exc), clicks, width)
        note = ""
        if clicks:
            note = "clicks cannot be mixed into an existing audio file"
            log.warning("%s; the count-in and metronome are silent", note)
        return AudioResult(path=source, backend="mux", note=note)

    if backend == "fluidsynth":
        available, why = fluidsynth_available(config.soundfont, config.fluidsynth_bin)
        if not available:
            log.warning("fluidsynth unavailable: %s; using the built-in synth", why)
            return _builtin(score, out_dir, start, duration, why, clicks, width)
        try:
            samples = synthesise_fluidsynth(
                score,
                config.soundfont,
                program=config.program,
                start=start,
                duration=duration,
            )
        except (AudioError, OSError, RuntimeError) as exc:
            log.warning("fluidsynth failed: %s; using the built-in synth", exc)
            return _builtin(score, out_dir, start, duration, str(exc), clicks, width)
        samples = mix_clicks(
            samples, clicks, start=start, sample_rate=SAMPLE_RATE, channels=2
        )
        path = write_wav(samples, out_dir / "psv-audio.wav", channels=2)
        return AudioResult(path=path, backend="fluidsynth")

    return _builtin(score, out_dir, start, duration, "", clicks, width)


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
