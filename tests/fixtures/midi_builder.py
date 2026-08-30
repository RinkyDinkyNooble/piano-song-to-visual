"""Build small, exact MIDI files for testing.

The real songs under ``tests/assets/`` are engraved scores exported by LilyPond.
They have uniform velocity and no sustain pedal, so they cannot test dynamics,
pedalling, or deliberately pathological hand spans. Everything here can.

These are the source of truth: tests call the builders directly and get a
``mido.MidiFile`` in memory. ``scripts/make_fixtures.py`` writes the same files
to disk when you want to open one in a DAW, but nothing depends on those blobs.

Positions and durations are in **beats**, never seconds, so a fixture stays
correct when its tempo changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import mido

TICKS_PER_BEAT = 480
DEFAULT_BPM = 120

SUSTAIN, SOSTENUTO, SOFT = 64, 66, 67

#: Lowest and highest keys on an 88-key piano (A0 and C8).
LOWEST_KEY, HIGHEST_KEY = 21, 108


def bpm_to_tempo(bpm: float) -> int:
    return round(60_000_000 / bpm)


@dataclass
class _Event:
    tick: int
    order: int
    message: mido.Message | mido.MetaMessage


@dataclass
class MidiBuilder:
    """Accumulates events on one or more tracks, then emits a MidiFile."""

    ticks_per_beat: int = TICKS_PER_BEAT
    _events: dict[int, list[_Event]] = field(default_factory=dict)
    _counter: int = 0

    def _add(
        self, track: int, beat: float, message: mido.Message | mido.MetaMessage
    ) -> None:
        self._counter += 1
        tick = round(beat * self.ticks_per_beat)
        self._events.setdefault(track, []).append(_Event(tick, self._counter, message))

    def note(
        self,
        pitch: int,
        start: float,
        duration: float = 1.0,
        velocity: int = 64,
        *,
        track: int = 0,
        channel: int = 0,
        off_as_zero_velocity: bool = False,
    ) -> MidiBuilder:
        """Add one note. Set ``off_as_zero_velocity`` to end it with note_on/0."""
        self._add(
            track,
            start,
            mido.Message("note_on", note=pitch, velocity=velocity, channel=channel),
        )
        end = (
            mido.Message("note_on", note=pitch, velocity=0, channel=channel)
            if off_as_zero_velocity
            else mido.Message("note_off", note=pitch, velocity=0, channel=channel)
        )
        self._add(track, start + duration, end)
        return self

    def chord(
        self,
        pitches: list[int],
        start: float,
        duration: float = 1.0,
        velocity: int = 64,
        *,
        track: int = 0,
        channel: int = 0,
    ) -> MidiBuilder:
        for pitch in pitches:
            self.note(pitch, start, duration, velocity, track=track, channel=channel)
        return self

    def pedal(
        self,
        start: float,
        duration: float,
        *,
        control: int = SUSTAIN,
        depth: int = 127,
        track: int = 0,
        channel: int = 0,
    ) -> MidiBuilder:
        self._add(
            track,
            start,
            mido.Message(
                "control_change", control=control, value=depth, channel=channel
            ),
        )
        self._add(
            track,
            start + duration,
            mido.Message("control_change", control=control, value=0, channel=channel),
        )
        return self

    def tempo(self, beat: float, bpm: float, *, track: int = 0) -> MidiBuilder:
        self._add(track, beat, mido.MetaMessage("set_tempo", tempo=bpm_to_tempo(bpm)))
        return self

    def time_signature(
        self, beat: float, numerator: int, denominator: int, *, track: int = 0
    ) -> MidiBuilder:
        self._add(
            track,
            beat,
            mido.MetaMessage(
                "time_signature", numerator=numerator, denominator=denominator
            ),
        )
        return self

    def program(self, program: int, *, track: int = 0, channel: int = 0) -> MidiBuilder:
        self._add(
            track, 0, mido.Message("program_change", program=program, channel=channel)
        )
        return self

    def track_name(self, name: str, *, track: int = 0) -> MidiBuilder:
        self._add(track, 0, mido.MetaMessage("track_name", name=name))
        return self

    def build(self) -> mido.MidiFile:
        midi = mido.MidiFile(type=1, ticks_per_beat=self.ticks_per_beat)
        if not self._events:
            midi.tracks.append(mido.MidiTrack())
            return midi
        for index in sorted(self._events):
            events = sorted(self._events[index], key=lambda e: (e.tick, e.order))
            track = mido.MidiTrack()
            previous = 0
            for event in events:
                message = event.message.copy(time=event.tick - previous)
                track.append(message)
                previous = event.tick
            midi.tracks.append(track)
        return midi


# --------------------------------------------------------------------------
# Fixtures. Each returns a MidiFile and states exactly what it exists to catch.
# --------------------------------------------------------------------------


def single_note() -> mido.MidiFile:
    """One middle C. The smallest thing the parser must not get wrong."""
    return MidiBuilder().note(60, 0.0, 1.0, 64).build()


def empty() -> mido.MidiFile:
    """No notes. Every stage must survive this without dividing by zero."""
    return MidiBuilder().build()


def full_keyboard() -> mido.MidiFile:
    """All 88 keys in order. Catches geometry errors at either extreme and any
    white/black key mix-up, since every key is hit exactly once."""
    builder = MidiBuilder()
    for index, pitch in enumerate(range(LOWEST_KEY, HIGHEST_KEY + 1)):
        builder.note(pitch, index * 0.25, 0.25, 64)
    return builder.build()


def velocity_ramp() -> mido.MidiFile:
    """The same pitch at velocity 1 through 127. The dynamics colour map has to
    be monotonic across this and must not clip at either end."""
    builder = MidiBuilder()
    for index, velocity in enumerate(range(1, 128)):
        builder.note(60, index * 0.25, 0.2, velocity)
    return builder.build()


def dynamic_levels() -> mido.MidiFile:
    """One chord per dynamic level, pp through ff, so the five bands are
    visually distinguishable from each other rather than merely ordered."""
    builder = MidiBuilder()
    for index, velocity in enumerate((16, 40, 64, 96, 127)):
        builder.chord([60, 64, 67], index * 2.0, 1.5, velocity)
    return builder.build()


def sustain_pedal() -> mido.MidiFile:
    """Notes under a held sustain pedal, then the same notes with none. Drives
    the pedal lane, and drives the constraint engine's rule that truncating a
    note is nearly free while the pedal is down."""
    builder = MidiBuilder()
    builder.pedal(0.0, 4.0, control=SUSTAIN)
    for index, pitch in enumerate((60, 64, 67, 72)):
        builder.note(pitch, index * 1.0, 0.5, 80)
    for index, pitch in enumerate((60, 64, 67, 72)):
        builder.note(pitch, 5.0 + index * 1.0, 0.5, 80)
    return builder.build()


def half_pedal() -> mido.MidiFile:
    """Sustain at partial depths. Anything treating CC64 as a boolean will read
    these identically, which is the bug this catches."""
    builder = MidiBuilder()
    for index, depth in enumerate((20, 50, 80, 110, 127)):
        builder.pedal(index * 2.0, 1.5, control=SUSTAIN, depth=depth)
        builder.note(60 + index, index * 2.0, 1.5, 70)
    return builder.build()


def three_pedals() -> mido.MidiFile:
    """All three pedals, overlapping. Exercises the multi-lane renderer and the
    configurable lane count."""
    builder = MidiBuilder()
    builder.pedal(0.0, 6.0, control=SUSTAIN)
    builder.pedal(1.0, 2.0, control=SOSTENUTO)
    builder.pedal(3.0, 2.5, control=SOFT)
    for index in range(6):
        builder.note(60 + index, index * 1.0, 0.8, 70)
    return builder.build()


def wide_span_chord() -> mido.MidiFile:
    """A single 37-semitone chord in one hand. No human plays this, so the
    constraint engine must repair it, and `verify_span` must go red before it
    does."""
    return MidiBuilder().chord([36, 48, 60, 73], 0.0, 4.0, 64).build()


def span_edge_cases() -> mido.MidiFile:
    """Four chords: exactly at a 12-semitone limit, one semitone over, and two
    pinned against the keyboard edges where octave-shifting has nowhere to go.
    The engine must leave the first alone and repair the rest without running
    off the end of the keyboard."""
    builder = MidiBuilder()
    builder.chord([60, 72], 0.0, 1.0, 64)
    builder.chord([60, 73], 2.0, 1.0, 64)
    builder.chord([LOWEST_KEY, LOWEST_KEY + 20], 4.0, 1.0, 64)
    builder.chord([HIGHEST_KEY - 20, HIGHEST_KEY], 6.0, 1.0, 64)
    return builder.build()


def tiny_overlap() -> mido.MidiFile:
    """Two far-apart notes overlapping by about 10 ms. This is sloppy MIDI, not
    a stretch anyone has to play, so the engine must not treat it as a
    violation. The pair after it overlaps properly and must be caught."""
    builder = MidiBuilder()
    builder.note(48, 0.0, 1.005, 64)
    builder.note(84, 1.0, 1.0, 64)
    builder.note(48, 4.0, 2.0, 64)
    builder.note(84, 4.5, 1.0, 64)
    return builder.build()


def tempo_changes() -> mido.MidiFile:
    """Four tempo changes over sixteen steady beats. The vertical beat grid must
    stay aligned to the beats, not to wall-clock seconds."""
    builder = MidiBuilder()
    for beat, bpm in ((0.0, 60), (4.0, 120), (8.0, 90), (12.0, 180)):
        builder.tempo(beat, bpm)
    for beat in range(16):
        builder.note(60 + (beat % 5), float(beat), 0.9, 72)
    return builder.build()


def time_signatures() -> mido.MidiFile:
    """4/4 to 3/4 to 7/8. Bar lines have to move with the meter."""
    builder = MidiBuilder()
    builder.time_signature(0.0, 4, 4)
    builder.time_signature(8.0, 3, 4)
    builder.time_signature(14.0, 7, 8)
    for beat in range(20):
        builder.note(60, float(beat), 0.9, 72)
    return builder.build()


def two_hands() -> mido.MidiFile:
    """Hands already separated onto their own tracks. The arrange stage must
    recognise this and decline to re-derive what is already correct."""
    builder = MidiBuilder()
    builder.track_name("right", track=0).track_name("left", track=1)
    for beat in range(8):
        builder.note(72 + (beat % 4), float(beat), 0.9, 80, track=0)
        builder.chord([48, 55], float(beat), 0.9, 60, track=1)
    return builder.build()


def voice_crossing() -> mido.MidiFile:
    """Two voices that swap register halfway through. Any hand assignment that
    is really just a pitch threshold gets this wrong."""
    builder = MidiBuilder()
    for beat in range(8):
        builder.note(60 + beat * 2, float(beat), 0.9, 80, track=0)
        builder.note(74 - beat * 2, float(beat), 0.9, 80, track=1)
    return builder.build()


def orchestral() -> mido.MidiFile:
    """Four instruments on four programs and four channels. The arrange stage's
    actual input shape, in miniature."""
    builder = MidiBuilder()
    parts = ((40, 0, 84), (41, 1, 72), (42, 2, 60), (43, 3, 43))
    for track, (program, channel, base) in enumerate(parts):
        builder.program(program, track=track, channel=channel)
        for beat in range(8):
            builder.note(
                base + (beat % 4),
                float(beat),
                0.9,
                70,
                track=track,
                channel=channel,
            )
    return builder.build()


def drum_channel() -> mido.MidiFile:
    """Piano on channel 0, percussion on channel 9. Channel 9 is not pitched and
    must never reach the keyboard."""
    builder = MidiBuilder()
    for beat in range(4):
        builder.note(60, float(beat), 0.9, 80, track=0, channel=0)
        builder.note(36, float(beat), 0.1, 100, track=1, channel=9)
    return builder.build()


def retriggered_pitch() -> mido.MidiFile:
    """The same pitch struck again before its first note-off. Naive note pairing
    either drops one or leaves a note hanging forever."""
    builder = MidiBuilder()
    builder.note(60, 0.0, 2.0, 64)
    builder.note(60, 1.0, 2.0, 90)
    return builder.build()


def zero_velocity_note_off() -> mido.MidiFile:
    """Every note ends with note_on velocity 0 instead of note_off. Legal MIDI,
    and common. A parser that only looks for note_off sees nothing end."""
    builder = MidiBuilder()
    for beat in range(4):
        builder.note(60 + beat, float(beat), 0.9, 70, off_as_zero_velocity=True)
    return builder.build()


#: Every fixture by name. ``scripts/make_fixtures.py`` and the coverage test
#: both walk this, so a new fixture is picked up by adding it here.
FIXTURES = {
    "single-note": single_note,
    "empty": empty,
    "full-keyboard": full_keyboard,
    "velocity-ramp": velocity_ramp,
    "dynamic-levels": dynamic_levels,
    "sustain-pedal": sustain_pedal,
    "half-pedal": half_pedal,
    "three-pedals": three_pedals,
    "wide-span-chord": wide_span_chord,
    "span-edge-cases": span_edge_cases,
    "tiny-overlap": tiny_overlap,
    "tempo-changes": tempo_changes,
    "time-signatures": time_signatures,
    "two-hands": two_hands,
    "voice-crossing": voice_crossing,
    "orchestral": orchestral,
    "drum-channel": drum_channel,
    "retriggered-pitch": retriggered_pitch,
    "zero-velocity-note-off": zero_velocity_note_off,
}
