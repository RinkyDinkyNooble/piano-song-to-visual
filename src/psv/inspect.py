"""Report what is actually inside a Score.

This is the first thing to run on an unfamiliar file. Whether a MIDI needs the
arrange stage at all, whether it carries pedal or dynamics data, and how far the
constraint engine will have to move things are all questions this answers before
any of those stages run.
"""

from __future__ import annotations

from bisect import bisect_left, insort
from dataclasses import dataclass

from psv.model import (
    DEFAULT_OVERLAP_TOLERANCE_S,
    HIGHEST_KEY,
    LOWEST_KEY,
    Note,
    Pedal,
    Score,
    pitch_name,
)

#: A file whose velocities are all the same carries no dynamics. Engraved scores
#: exported from notation software look like this.
_UNIFORM_VELOCITY_THRESHOLD = 1


@dataclass(frozen=True, slots=True)
class PartSummary:
    name: str
    source_track: int
    note_count: int
    pitch_low: int
    pitch_high: int


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """Everything `psv inspect` prints, as data so tests can assert on it."""

    title: str
    duration_s: float
    part_count: int
    note_count: int
    parts: tuple[PartSummary, ...]
    pitch_low: int | None
    pitch_high: int | None
    off_keyboard_notes: int
    distinct_velocities: int
    peak_polyphony: int
    mean_polyphony: float
    widest_span: int
    widest_span_time: float
    pedal_counts: dict[Pedal, int]
    has_partial_pedalling: bool
    tempo_changes: int
    bpm_low: float
    bpm_high: float
    time_signatures: tuple[str, ...]

    @property
    def has_dynamics(self) -> bool:
        return self.distinct_velocities > _UNIFORM_VELOCITY_THRESHOLD

    @property
    def has_pedal(self) -> bool:
        return bool(self.pedal_counts)

    @property
    def looks_pre_separated(self) -> bool:
        """Whether the file already looks like two-hand piano writing.

        Two parts with distinct registers is what a piano score exported from
        notation software looks like. It is a hint for the arrange stage, not a
        decision: that stage makes the call.
        """
        if self.part_count != 2:
            return False
        low, high = sorted(self.parts, key=lambda p: p.pitch_low)
        return low.pitch_high <= high.pitch_low + 12


def inspect_score(score: Score) -> ScoreReport:
    notes = score.notes
    peak, mean = _polyphony(notes)
    span, span_time = _widest_span(notes)

    pedal_counts: dict[Pedal, int] = {}
    for event in score.pedals:
        pedal_counts[event.pedal] = pedal_counts.get(event.pedal, 0) + 1

    pitches = [note.pitch for note in notes]
    bpms = [change.bpm for change in score.tempo_map.changes]

    return ScoreReport(
        title=score.title or (score.source.stem if score.source else "<untitled>"),
        duration_s=score.duration,
        part_count=len(score.parts),
        note_count=len(notes),
        parts=tuple(
            PartSummary(
                name=part.name,
                source_track=part.source_track,
                note_count=len(part),
                pitch_low=min(n.pitch for n in part.notes),
                pitch_high=max(n.pitch for n in part.notes),
            )
            for part in score.parts
            if part.notes
        ),
        pitch_low=min(pitches) if pitches else None,
        pitch_high=max(pitches) if pitches else None,
        off_keyboard_notes=sum(1 for note in notes if not note.on_keyboard),
        distinct_velocities=len({note.velocity for note in notes}),
        peak_polyphony=peak,
        mean_polyphony=mean,
        widest_span=span,
        widest_span_time=span_time,
        pedal_counts=pedal_counts,
        has_partial_pedalling=any(not e.is_full for e in score.pedals),
        tempo_changes=len(score.tempo_map.changes),
        bpm_low=min(bpms),
        bpm_high=max(bpms),
        time_signatures=tuple(
            f"{sig.numerator}/{sig.denominator}" for sig in score.time_signatures
        ),
    )


def _polyphony(notes: tuple[Note, ...]) -> tuple[int, float]:
    """Peak and time-weighted mean number of notes sounding together.

    Sweeps the note-on and note-off boundaries rather than sampling, so a very
    short dense passage is not missed and a long sparse one is not overweighted.
    """
    if not notes:
        return 0, 0.0

    events: list[tuple[float, int]] = []
    for note in notes:
        events.append((note.start, 1))
        events.append((note.end, -1))
    events.sort()

    peak = 0
    active = 0
    weighted = 0.0
    previous = events[0][0]
    for time, delta in events:
        if time > previous:
            weighted += active * (time - previous)
            previous = time
        active += delta
        peak = max(peak, active)

    total = events[-1][0] - events[0][0]
    return peak, (weighted / total if total > 0 else float(peak))


def _widest_span(
    notes: tuple[Note, ...], tolerance: float = DEFAULT_OVERLAP_TOLERANCE_S
) -> tuple[int, float]:
    """The widest set of notes sounding together, ignoring hand assignment.

    Before the arrange stage runs there are no hands, so this measures the whole
    texture. It is an upper bound on what the constraint engine will face, not
    the figure it will act on.

    A sweep over note boundaries, keeping the sounding pitches sorted, so the
    cost is n log n rather than comparing every note against every other. The
    tolerance is applied by ending each note early: an overlap shorter than that
    is sloppy MIDI, not a stretch anyone holds.
    """
    if not notes:
        return 0, 0.0

    # (time, is_start, pitch): ends sort before starts at the same instant, so a
    # note that stops exactly where another begins is not counted as held with it.
    events: list[tuple[float, int, int]] = []
    for note in notes:
        end = max(note.start, note.end - tolerance)
        events.append((note.start, 1, note.pitch))
        events.append((end, 0, note.pitch))
    events.sort()

    active: list[int] = []
    widest = 0
    at_time = 0.0
    for time, is_start, pitch in events:
        if is_start:
            insort(active, pitch)
            if len(active) > 1:
                span = active[-1] - active[0]
                if span > widest:
                    widest, at_time = span, time
        else:
            index = bisect_left(active, pitch)
            if index < len(active) and active[index] == pitch:
                del active[index]
    return widest, at_time


def format_report(report: ScoreReport, *, verbose: bool = False) -> str:
    """Render a report as the text `psv inspect` prints."""
    lines: list[str] = [
        f"{report.title}",
        f"  duration       {report.duration_s:.1f}s",
        f"  notes          {report.note_count} in {report.part_count} part(s)",
    ]

    if report.pitch_low is not None and report.pitch_high is not None:
        extra = ""
        if report.off_keyboard_notes:
            extra = f"  ({report.off_keyboard_notes} outside the 88 keys)"
        lines.append(
            f"  range          {pitch_name(report.pitch_low)} to "
            f"{pitch_name(report.pitch_high)}{extra}"
        )

    lines += [
        f"  polyphony      peak {report.peak_polyphony}, "
        f"mean {report.mean_polyphony:.1f}",
        f"  widest span    {report.widest_span} semitones "
        f"at {report.widest_span_time:.1f}s",
    ]

    if report.tempo_changes == 1:
        lines.append(f"  tempo          {report.bpm_low:.0f} BPM, constant")
    else:
        lines.append(
            f"  tempo          {report.bpm_low:.0f} to {report.bpm_high:.0f} BPM, "
            f"{report.tempo_changes} changes"
        )
    lines.append(f"  meter          {', '.join(report.time_signatures)}")

    if report.has_dynamics:
        lines.append(f"  dynamics       {report.distinct_velocities} velocity levels")
    else:
        lines.append("  dynamics       none (every note the same velocity)")

    if report.has_pedal:
        detail = ", ".join(
            f"{pedal.name.lower()} x{count}"
            for pedal, count in sorted(report.pedal_counts.items())
        )
        partial = " (includes partial depths)" if report.has_partial_pedalling else ""
        lines.append(f"  pedal          {detail}{partial}")
    else:
        lines.append("  pedal          none")

    lines.append(
        "  hands          "
        + (
            "look already separated"
            if report.looks_pre_separated
            else "not separated; needs the arrange stage"
        )
    )

    if verbose and report.parts:
        lines.append("")
        for part in report.parts:
            lines.append(
                f"  track {part.source_track:<2} {part.name[:24]:<24} "
                f"{part.note_count:>5} notes  "
                f"{pitch_name(part.pitch_low)}-{pitch_name(part.pitch_high)}"
            )

    if report.off_keyboard_notes:
        lines.append("")
        lines.append(
            f"  note: {report.off_keyboard_notes} note(s) fall outside "
            f"{pitch_name(LOWEST_KEY)}-{pitch_name(HIGHEST_KEY)} and cannot be "
            "played on an 88-key piano."
        )

    return "\n".join(lines)
