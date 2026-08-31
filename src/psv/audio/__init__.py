"""Soundtracks for a rendered video.

Four backends behind one call, each falling back to the next when what it needs
is missing. See `backends` for why the chain matters more than any one of them.
"""

from psv.audio.backends import (
    SAMPLE_RATE,
    AudioError,
    AudioResult,
    fluidsynth_available,
    mux_into_video,
    pitch_to_hz,
    render_audio,
    synthesise,
    write_wav,
)

__all__ = [
    "SAMPLE_RATE",
    "AudioError",
    "AudioResult",
    "fluidsynth_available",
    "mux_into_video",
    "pitch_to_hz",
    "render_audio",
    "synthesise",
    "write_wav",
]
