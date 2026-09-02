"""The metronome click, and mixing it into a rendered soundtrack.

A click has one job: to be unmistakable against whatever else is sounding. So it
is short, it is loud, and it sits well above where a piano's fundamental is,
which is why these are plain sines with a fast decay rather than anything
sampled.

The accent is a fifth higher rather than merely louder. Louder alone stops
working the moment the music is loud too, and pitch survives that.

The sample rate is passed in rather than imported. This module sits underneath
the backends so they can mix a click into whatever they produced, and taking the
rate as an argument is what keeps that direction one-way.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from psv.practice import Click

#: Downbeat and off-beat click pitches, in hertz.
ACCENT_HZ = 1500.0
BEAT_HZ = 1000.0

#: How long one click rings. Long enough to hear, short enough not to blur into
#: the next one at any tempo a person can actually play.
CLICK_S = 0.045

#: Exponential decay rate over that time. Steep, so the click reads as a tick
#: rather than a beep.
DECAY = 24.0

ACCENT_LEVEL = 0.55
BEAT_LEVEL = 0.38

#: Peak the mixed result is held under, matching the synth backends.
HEADROOM = 0.89


def click_wave(accent: bool, sample_rate: int) -> np.ndarray:
    """One click, as mono float32 samples."""
    length = max(1, int(CLICK_S * sample_rate))
    time = np.arange(length, dtype=np.float32) / sample_rate
    frequency = ACCENT_HZ if accent else BEAT_HZ
    level = ACCENT_LEVEL if accent else BEAT_LEVEL
    envelope = np.exp(-DECAY * time, dtype=np.float32)
    return (level * envelope * np.sin(2.0 * np.pi * frequency * time)).astype(
        np.float32
    )


def mix_clicks(
    samples: np.ndarray,
    clicks: Sequence[Click],
    *,
    start: float,
    sample_rate: int,
    channels: int = 1,
) -> np.ndarray:
    """Add a click track to already-rendered audio.

    ``samples`` is mono when ``channels`` is 1 and interleaved stereo when it is
    2, which is the shape each backend already produces. ``start`` is the score
    time the buffer begins at; it is negative when a count-in runs ahead of the
    music, and click times are in score time too, so the two line up without
    either side needing to know about the other.

    The result is held under ``HEADROOM``, so adding clicks changes the balance
    between click and music rather than pushing the total into the clipping that
    ``write_wav`` would otherwise do for you.
    """
    if not clicks or samples.size == 0:
        return samples

    mixed = samples.astype(np.float32, copy=True)
    frames = mixed.size // channels

    for click in clicks:
        wave = click_wave(click.accent, sample_rate)
        begin = int((click.time - start) * sample_rate)
        if begin >= frames:
            continue
        # A click straddling the start of the buffer keeps only its tail.
        trimmed = wave[max(0, -begin) :][: frames - max(0, begin)]
        if trimmed.size == 0:
            continue
        begin = max(0, begin)
        if channels == 1:
            mixed[begin : begin + trimmed.size] += trimmed
        else:
            # Interleaved, so each channel is a strided view of the same buffer
            # and one click lands on every channel of each frame.
            for channel in range(channels):
                view = mixed[channel::channels]
                view[begin : begin + trimmed.size] += trimmed

    peak = float(np.max(np.abs(mixed)))
    if peak > HEADROOM:
        mixed *= HEADROOM / peak
    return mixed
