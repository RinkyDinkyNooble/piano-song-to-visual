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
from psv.audio.backends import (
    REVERB_ANCHORS,
    AudioError,
    mux_into_video,
    pan_gains,
    reverb_settings,
    synthesise_fluidsynth,
)
from psv.audio.click import click_wave, mix_clicks
from psv.config import DEFAULT_REVERB, AudioConfig
from psv.midi import read_midi
from psv.model import Hand, Note, Part, Pedal, PedalEvent, Score
from psv.practice import Click
from tests.fixtures.midi_builder import FIXTURES
from tests.probe import video_meta


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
def test_a_missing_soundfont_falls_back_with_sound_and_a_reason(
    tmp_path: Path,
) -> None:
    """The machine may have no SoundFont, or no native library. Either way the
    render must come back with audio and an explanation, not silence."""
    config = AudioConfig(backend="fluidsynth", soundfont=str(tmp_path / "none.sf2"))
    result = render_audio(one_note(), config, tmp_path)
    assert result.backend == "builtin"
    assert result.note
    assert result.path is not None


@pytest.mark.feature("F-41")
def test_a_bad_fluidsynth_bin_folder_is_reported(tmp_path: Path) -> None:
    font = tmp_path / "fake.sf2"
    font.write_bytes(b"not really a soundfont")
    available, why = fluidsynth_available(str(font), str(tmp_path / "nope"))
    assert not available
    assert "fluidsynth_bin" in why


# FluidSynth is optional: skip rather than fail where it is not installed.
_SOUNDFONT = Path.home() / ".local" / "fluidsynth" / "GeneralUser-GS.sf2"
_BIN = Path.home() / ".local" / "fluidsynth" / "bin"
needs_fluidsynth = pytest.mark.skipif(
    not fluidsynth_available(str(_SOUNDFONT), str(_BIN))[0],
    reason="FluidSynth library or SoundFont not installed",
)


@pytest.mark.feature("F-39")
@needs_fluidsynth
def test_fluidsynth_renders_stereo_audio_of_the_right_length(tmp_path: Path) -> None:
    from psv.audio.backends import synthesise_fluidsynth

    samples = synthesise_fluidsynth(
        read_midi(FIXTURES["dynamic-levels"]()), str(_SOUNDFONT), duration=3.0
    )
    # Interleaved stereo, so two samples per frame.
    assert samples.size == int(3.0 * SAMPLE_RATE) * 2
    assert np.abs(samples).max() > 0.1, "should be audible"
    assert np.abs(samples).max() <= 1.0, "and must not clip"


@pytest.mark.feature("F-39")
@needs_fluidsynth
def test_the_fluidsynth_backend_is_selected_when_it_can_run(tmp_path: Path) -> None:
    config = AudioConfig(
        backend="fluidsynth", soundfont=str(_SOUNDFONT), fluidsynth_bin=str(_BIN)
    )
    result = render_audio(one_note(), config, tmp_path, duration=2.0)
    assert result.backend == "fluidsynth"
    assert result.note == ""
    assert result.path is not None and result.path.stat().st_size > 10_000


@pytest.mark.feature("F-39")
@needs_fluidsynth
def test_choosing_a_different_instrument_changes_the_sound(tmp_path: Path) -> None:
    """audio.program picks the General MIDI instrument, so an electric piano
    and a grand do not come out identical."""
    from psv.audio.backends import synthesise_fluidsynth

    score = read_midi(FIXTURES["dynamic-levels"]())
    grand = synthesise_fluidsynth(score, str(_SOUNDFONT), program=0, duration=2.0)
    rhodes = synthesise_fluidsynth(score, str(_SOUNDFONT), program=4, duration=2.0)
    assert not np.allclose(grand, rhodes)


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

    meta = video_meta(combined)
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


# -- the metronome click -------------------------------------------------


@pytest.mark.feature("F-53")
def test_a_click_lands_where_it_was_asked_for() -> None:
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mixed = mix_clicks(
        silence, [Click(0.5, accent=False)], start=0.0, sample_rate=SAMPLE_RATE
    )
    before = np.abs(mixed[: int(0.4 * SAMPLE_RATE)]).max()
    at = np.abs(mixed[int(0.5 * SAMPLE_RATE) : int(0.55 * SAMPLE_RATE)]).max()
    assert before == 0.0
    assert at > 0.3


@pytest.mark.feature("F-53")
def test_a_count_in_click_before_the_buffer_starts_lines_up() -> None:
    """A count-in makes the buffer start at a negative score time. The click
    times are in score time too, so the two have to meet there."""
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mixed = mix_clicks(
        silence, [Click(-1.5, accent=True)], start=-2.0, sample_rate=SAMPLE_RATE
    )
    assert np.abs(mixed[int(0.5 * SAMPLE_RATE) : int(0.55 * SAMPLE_RATE)]).max() > 0.3


@pytest.mark.feature("F-53")
def test_the_accent_is_higher_pitched_than_the_beat() -> None:
    """Louder alone stops working once the music is loud too; pitch survives."""
    accent = click_wave(True, SAMPLE_RATE)
    beat = click_wave(False, SAMPLE_RATE)
    assert _dominant_hz(accent) > _dominant_hz(beat)


def _dominant_hz(wave: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(wave))
    return float(np.fft.rfftfreq(wave.size, 1 / SAMPLE_RATE)[int(spectrum.argmax())])


@pytest.mark.feature("F-53")
def test_clicks_never_push_the_mix_into_clipping() -> None:
    loud = np.full(SAMPLE_RATE, 0.89, dtype=np.float32)
    clicks = [Click(index * 0.1, accent=index % 4 == 0) for index in range(10)]
    mixed = mix_clicks(loud, clicks, start=0.0, sample_rate=SAMPLE_RATE)
    assert np.abs(mixed).max() <= 0.89 + 1e-6


@pytest.mark.feature("F-53")
def test_a_click_reaches_both_channels_of_a_stereo_buffer() -> None:
    """FluidSynth hands back interleaved stereo, so a click has to be written
    into every channel of a frame rather than into every other sample."""
    silence = np.zeros(2 * SAMPLE_RATE, dtype=np.float32)
    mixed = mix_clicks(
        silence,
        [Click(0.25, accent=False)],
        start=0.0,
        sample_rate=SAMPLE_RATE,
        channels=2,
    )
    left, right = mixed[0::2], mixed[1::2]
    assert np.abs(left).max() > 0.3
    assert np.array_equal(left, right)


def test_mixing_no_clicks_changes_nothing() -> None:
    samples = np.linspace(-0.5, 0.5, 100, dtype=np.float32)
    assert mix_clicks(samples, [], start=0.0, sample_rate=SAMPLE_RATE) is samples


@pytest.mark.feature("F-53")
def test_the_builtin_backend_mixes_the_clicks_in(tmp_path: Path) -> None:
    """End to end through the backend, because that is where it has to work."""
    score = Score(parts=(Part(notes=(Note(pitch=60, start=2.0, end=3.0),)),))
    clicks = [Click(0.5, accent=True), Click(1.0, accent=False)]
    result = render_audio(
        score, AudioConfig(), tmp_path, start=0.0, duration=4.0, clicks=clicks
    )
    assert result.path is not None
    with wave.open(str(result.path)) as handle:
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    # Nothing sounds before the note except the two clicks.
    lead = np.abs(data[: int(1.5 * SAMPLE_RATE)].astype(np.float32))
    assert lead.max() > 0.2 * 32767


@pytest.mark.feature("F-53")
def test_the_mux_backend_says_it_cannot_carry_the_clicks(tmp_path: Path) -> None:
    """Silently dropping them would look like the flag did nothing."""
    source = write_wav(np.zeros(SAMPLE_RATE, dtype=np.float32), tmp_path / "in.wav")
    config = replace(AudioConfig(), backend="mux", audio_file=str(source))
    result = render_audio(Score(), config, tmp_path, clicks=[Click(0.0, accent=True)])
    assert result.backend == "mux"
    assert "cannot be mixed" in result.note


@pytest.mark.feature("F-53")
def test_clicks_past_the_end_of_the_buffer_are_dropped() -> None:
    """The last beat of a section can fall after the tail runs out. Writing it
    would be an index error; dropping it is what the picture already does."""
    silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
    mixed = mix_clicks(
        silence,
        [Click(9.0, accent=True), Click(0.25, accent=False)],
        start=0.0,
        sample_rate=SAMPLE_RATE,
    )
    assert mixed.size == silence.size
    assert np.abs(mixed).max() > 0.3


@pytest.mark.feature("F-53")
def test_a_click_entirely_before_the_buffer_is_dropped() -> None:
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mixed = mix_clicks(
        silence, [Click(-5.0, accent=True)], start=0.0, sample_rate=SAMPLE_RATE
    )
    assert np.abs(mixed).max() == 0.0


# -- stereo ---------------------------------------------------------------


@pytest.mark.feature("F-60")
def test_low_notes_sit_left_and_high_notes_sit_right() -> None:
    """As they do under your hands. It is not only prettier: it stops the two
    hands competing for the same place in the mix, which is what makes a
    left-hand line audible underneath a busy right hand."""
    low_left, low_right = pan_gains(30, 1.0)
    high_left, high_right = pan_gains(100, 1.0)
    assert low_left > low_right
    assert high_right > high_left


@pytest.mark.feature("F-60")
def test_panning_holds_its_power_across_the_field() -> None:
    """Equal power, not linear: a note must not dip in volume as it crosses the
    middle."""
    for pitch in (21, 40, 60, 80, 108):
        left, right = pan_gains(pitch, 1.0)
        assert left**2 + right**2 == pytest.approx(1.0, abs=1e-6)


@pytest.mark.feature("F-60")
def test_zero_width_puts_everything_in_the_centre() -> None:
    for pitch in (21, 60, 108):
        left, right = pan_gains(pitch, 0.0)
        assert left == pytest.approx(right)


@pytest.mark.feature("F-60")
def test_stereo_synthesis_is_interleaved_and_twice_as_long() -> None:
    """The same shape FluidSynth produces, so everything downstream handles one
    layout rather than two."""
    score = Score(parts=(Part(notes=(Note(pitch=30, start=0.0, end=1.0),)),))
    mono = synthesise(score, duration=1.5)
    stereo = synthesise(score, duration=1.5, stereo_width=1.0)

    assert stereo.size == mono.size * 2
    assert np.abs(stereo[0::2]).max() > np.abs(stereo[1::2]).max(), "a low note"


@pytest.mark.feature("F-60")
def test_the_builtin_backend_writes_a_stereo_file(tmp_path: Path) -> None:
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=1.0),)),))
    config = replace(AudioConfig(), stereo_width=0.6)
    result = render_audio(score, config, tmp_path, duration=1.5)

    assert result.path is not None
    with wave.open(str(result.path)) as handle:
        assert handle.getnchannels() == 2


@pytest.mark.feature("F-60")
def test_mono_stays_reachable(tmp_path: Path) -> None:
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=1.0),)),))
    config = replace(AudioConfig(), stereo_width=0.0)
    result = render_audio(score, config, tmp_path, duration=1.5)

    assert result.path is not None
    with wave.open(str(result.path)) as handle:
        assert handle.getnchannels() == 1


# -- reverb --------------------------------------------------------------


@pytest.mark.feature("F-80")
def test_the_middle_of_the_reverb_range_is_what_it_always_was() -> None:
    """FluidSynth turns its own reverb on by default, so psv has never been dry.
    0.5 is those defaults, which is why the default changes nothing."""
    middle = reverb_settings(DEFAULT_REVERB)
    assert middle == {
        "roomsize": 0.5,
        "damping": 0.2,
        "width": 0.8,
        "level": 0.7,
    }


@pytest.mark.feature("F-80")
def test_zero_reverb_asks_for_no_reverb_at_all() -> None:
    assert reverb_settings(0.0)["level"] == 0.0


@pytest.mark.feature("F-80")
@pytest.mark.parametrize("name", sorted(REVERB_ANCHORS))
def test_every_reverb_parameter_only_goes_one_way(name: str) -> None:
    """One number drives four, so all four have to move together. A parameter
    that dipped in the middle would make the knob feel broken."""
    amounts = [index / 20 for index in range(21)]
    values = [reverb_settings(amount)[name] for amount in amounts]
    assert values == sorted(values)
    assert values[0] < values[-1]


@pytest.mark.feature("F-80")
def test_a_reverb_outside_the_range_is_clamped_not_passed_on() -> None:
    """Config validates this, but the settings are also reachable directly and
    FluidSynth would take an out-of-range level without complaining."""
    assert reverb_settings(-5.0) == reverb_settings(0.0)
    assert reverb_settings(5.0) == reverb_settings(1.0)


@pytest.mark.feature("F-80")
def test_a_backend_without_reverb_says_so_rather_than_ignoring_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=0.5),)),))
    config = AudioConfig(backend="builtin", reverb=0.9)
    with caplog.at_level("WARNING"):
        render_audio(score, config, tmp_path)
    assert "reverb" in caplog.text
    assert "fluidsynth" in caplog.text


@pytest.mark.feature("F-80")
def test_the_default_reverb_is_quiet_about_backends_that_lack_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Nobody chose the default, so warning about it would be noise."""
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.0, end=0.5),)),))
    with caplog.at_level("WARNING"):
        render_audio(score, AudioConfig(backend="builtin"), tmp_path)
    assert "reverb" not in caplog.text


@needs_fluidsynth
@pytest.mark.feature("F-80")
def test_more_reverb_means_a_longer_tail() -> None:
    """The thing the number is for. One short note, then silence: what is left
    ringing a second later is the room."""
    score = Score(parts=(Part(notes=(Note(pitch=60, start=0.1, end=0.4),)),))

    def tail(amount: float) -> float:
        samples = synthesise_fluidsynth(
            score, str(_SOUNDFONT), start=0.0, duration=4.0, reverb=amount
        ).reshape(-1, 2)
        window = samples[int(1.0 * SAMPLE_RATE) : int(1.5 * SAMPLE_RATE)]
        return float(np.sqrt((window**2).mean()))

    dry, middle, wet = tail(0.0), tail(DEFAULT_REVERB), tail(1.0)
    assert dry < middle < wet
    assert wet > dry * 10, f"the range is too narrow to be worth a knob: {dry} to {wet}"
