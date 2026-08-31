"""Audio backends and the fallback chain.

The chain matters more than any one backend. This is a practice tool: being
handed a silent video with no explanation because a library was missing is much
worse than being handed a cheap-sounding one that says why.
"""

from __future__ import annotations

import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from psv.audio import (
    SAMPLE_RATE,
    fluidsynth_available,
    pitch_to_hz,
    render_audio,
    synthesise,
    write_wav,
)
from psv.audio.backends import AudioError, mux_into_video
from psv.config import AudioConfig
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Pedal, PedalEvent, Score
from tests.fixtures.midi_builder import FIXTURES


def one_note(pitch: int = 60, velocity: int = 100, end: float = 1.0) -> Score:
    return Score(
        parts=(
            Part(
                notes=(
                    Note(
                        pitch=pitch,
                        start=0.0,
                        end=end,
                        velocity=velocity,
                        hand=Hand.RIGHT,
                    ),
                ),
            ),
        )
    )


# -- pitch ---------------------------------------------------------------


@pytest.mark.feature("F-38")
def test_concert_a_is_440_hertz() -> None:
    assert pitch_to_hz(69) == pytest.approx(440.0)


@pytest.mark.feature("F-38")
def test_an_octave_up_doubles_the_frequency() -> None:
    assert pitch_to_hz(81) == pytest.approx(880.0)
    assert pitch_to_hz(57) == pytest.approx(220.0)


# -- the built-in synth --------------------------------------------------


@pytest.mark.feature("F-38")
def test_a_note_produces_sound_only_while_it_lasts() -> None:
    samples = synthesise(one_note(end=1.0), duration=3.0)
    during = np.abs(samples[:SAMPLE_RATE]).max()
    after = np.abs(samples[int(2.5 * SAMPLE_RATE) :]).max()
    assert during > 0.1
    assert after < 0.01, "the note should have stopped ringing"


@pytest.mark.feature("F-38")
def test_a_louder_note_is_louder() -> None:
    """Velocity has to reach the audio, or the dynamics are only a picture."""
    quiet = np.abs(synthesise(one_note(velocity=20), duration=1.5)).max()
    loud = np.abs(synthesise(one_note(velocity=127), duration=1.5)).max()
    assert quiet < loud


@pytest.mark.feature("F-38")
def test_the_output_never_clips() -> None:
    score = read_midi(FIXTURES["dynamic-levels"]())
    samples = synthesise(score)
    assert np.abs(samples).max() <= 1.0


@pytest.mark.feature("F-38")
def test_a_dense_chord_is_normalised_rather_than_clipped() -> None:
    chord = Score(
        parts=(
            Part(
                notes=tuple(
                    Note(pitch=p, start=0.0, end=1.0, velocity=127)
                    for p in (48, 52, 55, 60, 64, 67, 72)
                ),
            ),
        )
    )
    assert np.abs(synthesise(chord)).max() <= 1.0


@pytest.mark.feature("F-38")
def test_the_sustain_pedal_keeps_a_note_ringing_past_its_release() -> None:
    """The same fact the constraint engine uses to decide truncation is free.

    If the audio ignored it, a note the engine shortened under the pedal would
    fall silent early and the two halves of the tool would disagree.
    """
    notes = (Note(pitch=60, start=0.0, end=0.2, velocity=100),)
    dry = Score(parts=(Part(notes=notes),))
    wet = Score(
        parts=(Part(notes=notes),),
        pedals=(PedalEvent(pedal=Pedal.SUSTAIN, start=0.0, end=2.0),),
    )
    window = slice(int(1.0 * SAMPLE_RATE), int(1.5 * SAMPLE_RATE))
    assert np.abs(synthesise(dry, duration=3.0)[window]).max() < 0.01
    assert np.abs(synthesise(wet, duration=3.0)[window]).max() > 0.05


@pytest.mark.feature("F-38")
def test_an_empty_score_is_silence_not_a_crash() -> None:
    samples = synthesise(Score(), duration=1.0)
    assert samples.size == SAMPLE_RATE
    assert not np.any(samples)


@pytest.mark.feature("F-42")
def test_the_audio_is_as_long_as_the_render_asks_for() -> None:
    """Sync starts here: audio and video have to agree on the span."""
    for duration in (0.5, 2.0, 5.0):
        samples = synthesise(read_midi(FIXTURES["two-hands"]()), duration=duration)
        assert samples.size == int(duration * SAMPLE_RATE)


@pytest.mark.feature("F-42")
def test_rendering_from_an_offset_starts_at_that_moment() -> None:
    score = read_midi(FIXTURES["full-keyboard"]())
    late = synthesise(score, start=5.0, duration=1.0)
    assert late.size == SAMPLE_RATE
    assert np.any(late), "there is music at 5s in this fixture"


# -- wav writing ---------------------------------------------------------


@pytest.mark.feature("F-38")
def test_a_wav_file_round_trips(tmp_path: Path) -> None:
    samples = synthesise(one_note(), duration=1.0)
    path = write_wav(samples, tmp_path / "out.wav")

    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnframes() == samples.size


# -- backend selection and fallback --------------------------------------


@pytest.mark.feature("F-37")
def test_the_silent_backend_produces_no_file(tmp_path: Path) -> None:
    result = render_audio(one_note(), AudioConfig(backend="none"), tmp_path)
    assert result.is_silent
    assert result.backend == "none"


@pytest.mark.feature("F-38")
def test_the_builtin_backend_writes_a_wav(tmp_path: Path) -> None:
    result = render_audio(one_note(), AudioConfig(backend="builtin"), tmp_path)
    assert result.backend == "builtin"
    assert result.path is not None
    assert result.path.stat().st_size > 1000


@pytest.mark.feature("F-40")
def test_the_mux_backend_uses_the_file_it_is_given(tmp_path: Path) -> None:
    source = write_wav(synthesise(one_note(), duration=0.5), tmp_path / "mine.wav")
    config = AudioConfig(backend="mux", audio_file=str(source))
    result = render_audio(one_note(), config, tmp_path)
    assert result.backend == "mux"
    assert result.path == source


@pytest.mark.feature("F-41")
def test_a_missing_audio_file_falls_back_and_says_why(tmp_path: Path) -> None:
    config = AudioConfig(backend="mux", audio_file=str(tmp_path / "absent.wav"))
    result = render_audio(one_note(), config, tmp_path)
    assert result.backend == "builtin"
    assert "absent.wav" in result.note
    assert result.path is not None


@pytest.mark.feature("F-41")
def test_fluidsynth_reports_exactly_why_it_cannot_run() -> None:
    available, why = fluidsynth_available("")
    assert not available
    assert "soundfont" in why.lower()

    available, why = fluidsynth_available("/no/such/font.sf2")
    assert not available
    assert "not found" in why


@pytest.mark.feature("F-41")
def test_choosing_fluidsynth_still_produces_audio(tmp_path: Path) -> None:
    """It is not implemented and the native library is usually absent. Either
    way the render must come back with sound and an explanation, not silence."""
    config = AudioConfig(backend="fluidsynth", soundfont=str(tmp_path / "none.sf2"))
    result = render_audio(one_note(), config, tmp_path)
    assert result.backend == "builtin"
    assert result.note
    assert result.path is not None


# -- muxing --------------------------------------------------------------


@pytest.mark.feature("F-42")
def test_video_and_audio_are_combined_with_matching_durations(
    tmp_path: Path,
) -> None:
    from psv.config import VisualConfig
    from psv.render.video import render_video

    tiny = VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0)
    score = read_midi(FIXTURES["dynamic-levels"]())

    silent = render_video(score, tiny, tmp_path / "silent.mp4", duration=2.0)
    audio = write_wav(synthesise(score, duration=2.0), tmp_path / "audio.wav")
    combined = mux_into_video(silent, audio, tmp_path / "out.mp4")

    import imageio_ffmpeg

    reader = imageio_ffmpeg.read_frames(str(combined))
    meta = next(reader)
    reader.close()
    assert meta["size"] == (160, 120)
    assert meta["duration"] == pytest.approx(2.0, abs=0.15)
    assert combined.stat().st_size > silent.stat().st_size


@pytest.mark.feature("F-41")
def test_muxing_a_missing_audio_file_raises_a_clear_error(tmp_path: Path) -> None:
    from psv.config import VisualConfig
    from psv.render.video import render_video

    tiny = VisualConfig(width=160, height=120, fps=10, lookahead_s=2.0)
    silent = render_video(Score(), tiny, tmp_path / "silent.mp4", duration=0.5)
    with pytest.raises(AudioError, match="ffmpeg"):
        mux_into_video(silent, tmp_path / "absent.wav", tmp_path / "out.mp4")


@pytest.mark.feature("F-38")
def test_the_default_config_uses_a_backend_that_always_works() -> None:
    """A fresh install must produce sound with no setup at all."""
    assert replace(AudioConfig()).backend == "builtin"
