"""How much a note matters, for deciding what to sacrifice.

Used in three places: choosing which note to drop when nothing gentler will fix
a span violation, thinning texture for a difficulty level, and reducing several
instruments to two hands.

**Why a note cannot be scored on its own.** The first version was velocity plus
duration, with a bonus for being the top or bottom of a chord. Two things were
wrong with it, and both were measured rather than suspected.

Velocity is usually not there at all. Five of the six real files this was tested
against have exactly one velocity level, because engraved sheet music exported
to MIDI carries no performance data, and engraved music is the whole repertoire
psv is aimed at. The term the old docstring called dominant was a constant, so
duration decided everything by itself.

Duration alone gets fast music backwards. A melodic run is a sequence of short
notes and so is an ornament, and preferring the long note keeps the
accompaniment while throwing away the tune. On the one test piece with a fast
right hand, 231 of 505 melody notes were being lost, 230 of them to a plain
duration threshold that could not tell a run from a decoration.

What replaced it reads two things off the whole score that a single note cannot
know: whether a note continues a line, and whether another copy of the same key
is already sounding.

**What was tried and dropped.** The roadmap asked for chord tones and passing
tones. That was built, as a duration-weighted pitch-class histogram per beat
with the top four classes taken as the harmony in force, and it made the
arrangement worse rather than better. Turned up far enough to bite, it cost 4%
of the bass line and 1% of the melody, because a passing note in an outer voice
is not less important than a chord tone in an inner one and a histogram cannot
tell which is which. A penalty for octave doublings changed nothing at any
weight. Neither is here. Both were removed rather than left in place doing
nothing, and this paragraph exists so the next person does not build them again
expecting more.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from psv.model import Note

#: Outer voices carry the tune and the bass. Losing one is far more damaging
#: than losing an inner voice, so they get a large thumb on the scale.
OUTER_VOICE_BONUS = 40.0

#: A note that continues a stepwise line, and is the top of what is sounding,
#: is the tune. Large enough that a short note in a run outranks a long note
#: under it, because that is the case a duration score gets backwards.
LINE_BONUS = 30.0

#: Taken off the shorter of two notes on the *same* key sounding together.
#:
#: One finger, one key: a unison is not a thicker chord, it is one sound
#: written twice, usually because two staves or two tracks doubled each other.
#: Whichever copy stops first is the one to lose, and losing it costs nothing,
#: which makes this the only genuinely free drop in the module.
#:
#: Without it velocity decided between the two copies, which is a coin toss on
#: a distinction that does not exist, and it kept choosing the copy that ended
#: sooner. The note the listener actually heard was cut short and the beat
#: after it fell silent. Taking this rule out again puts five times as many
#: beats back to silence.
UNISON_PENALTY = 60.0

#: The most duration can contribute, and where it saturates. Past a couple of
#: beats, longer does not mean more important.
DURATION_WEIGHT = 20.0
DURATION_FULL_S = 2.0

#: The most velocity can contribute, once normalised to the range this
#: particular piece uses.
#:
#: Small, and smaller than it looks. Normalising is the point: the old term
#: added a raw MIDI velocity, so on a flat file every note got the same large
#: constant and on a loud one it swamped everything else. Here a flat file
#: scores zero for every note and the remaining factors decide.
#:
#: It moved no measurement on any test file, at this weight or at five times
#: it, and turned up further it made the result slightly worse. It is kept
#: because it is the one thing a performed MIDI carries that an engraved score
#: does not, and a tie broken by how hard a note was struck beats a tie broken
#: by pitch. It is not doing heavy lifting and should not be given more.
VELOCITY_WEIGHT = 12.0

#: Two notes this far apart in pitch or closer count as a step in a line. Wide
#: enough for a third, so an arpeggiated tune still reads as one, and narrow
#: enough that a leap between registers does not.
LINE_INTERVAL = 4

#: How far apart in time two notes may be and still belong to the same line,
#: as a multiple of the first one's length, with a floor and a ceiling. A run
#: is notes following each other closely; the same figure half a bar later is
#: not the same gesture.
LINE_GAP_FACTOR = 2.5
LINE_GAP_MIN_S = 0.12
LINE_GAP_MAX_S = 0.75


def _key(note: Note) -> tuple[int, int]:
    """A note's identity in the precomputed table.

    Pitch and start time, which is what survives the edits made downstream:
    truncation moves a note's end, and dropping notes renumbers the list. An
    octave shift changes the pitch and so loses the note's line membership,
    which is right enough, since a shifted note is no longer in the line it was
    in.
    """
    return note.pitch, round(note.start * 1000.0)


@dataclass(frozen=True, slots=True)
class Salience:
    """How much each note matters, worked out once for a whole score.

    Built with :meth:`analyse`, then asked about one note at a time. The
    analysis is why this is an object rather than a function: which notes
    continue a line is a property of the piece, and working it out per note
    would be quadratic on a score with several thousand of them.

    Asking about a note that was not in the analysis is not an error. It falls
    back to what the note says about itself, which is what the repair stage
    needs when it scores a note it has just rewritten.

    A default-constructed one knows nothing about any piece and still ranks
    notes sensibly, so a caller with no score to read needs no special case.
    """

    #: Notes that continue a stepwise succession, by :func:`_key`.
    lines: frozenset[tuple[int, int]] = frozenset()
    #: The quietest note in the piece, and the range above it. Zero when the
    #: file has one velocity, which most engraved exports do.
    quietest: int = 0
    velocity_range: int = 0

    # -- building --------------------------------------------------------

    @classmethod
    def analyse(cls, notes: Sequence[Note]) -> Salience:
        """Read a score once and keep what scoring a note will need."""
        if not notes:
            return cls()
        velocities = [note.velocity for note in notes]
        quietest = min(velocities)
        return cls(
            lines=_find_lines(notes),
            quietest=quietest,
            velocity_range=max(velocities) - quietest,
        )

    # -- asking ----------------------------------------------------------

    def alone(self, note: Note, now: float | None = None) -> float:
        """What the note is worth without knowing what sounds beside it."""
        return self._velocity_term(note) + self._duration_term(note, now)

    def of(self, note: Note, among: Sequence[Note], now: float | None = None) -> float:
        """What the note is worth within the simultaneity it belongs to.

        The top and the bottom of a chord are the melody and the bass, and a
        listener misses whatever lies between them least. A note that is both
        an outer voice and part of a line is the tune itself, and stays
        protected when it is short, which is the case duration gets wrong.

        ``now`` is when the choice is being made. Given it, length counts from
        there rather than from the note's start, which is the difference
        between how long a note was written and how much of it is still to
        come. Dropping a note that was about to stop costs the listener
        nothing; dropping one with two seconds left costs the bar.

        There is deliberately no term for where the beat falls. Every note in a
        simultaneity was struck at the same moment and so scores identically on
        metre, which leaves it unable to separate the notes this is asked to
        choose between. It was built and measured before that was obvious: it
        moved downbeat retention by three hundredths of a percent.
        """
        score = self.alone(note, now)
        if not among:
            return score

        if any(
            other is not note and other.pitch == note.pitch and other.end > note.end
            for other in among
        ):
            score -= UNISON_PENALTY

        pitches = [other.pitch for other in among]
        if note.pitch in (max(pitches), min(pitches)):
            score += OUTER_VOICE_BONUS
            if note.pitch == max(pitches) and self.carries_line(note):
                score += LINE_BONUS
        return score

    def carries_line(self, note: Note) -> bool:
        """Whether this note continues a stepwise succession.

        The test that separates a melodic run from an ornament, which nothing
        about a single note can do: both are short.
        """
        return _key(note) in self.lines

    # -- the individual terms --------------------------------------------

    def _velocity_term(self, note: Note) -> float:
        if self.velocity_range <= 0:
            return 0.0
        above = (note.velocity - self.quietest) / self.velocity_range
        return VELOCITY_WEIGHT * min(1.0, max(0.0, above))

    def _duration_term(self, note: Note, now: float | None = None) -> float:
        left = note.duration if now is None else max(0.0, note.end - now)
        return DURATION_WEIGHT * min(left, DURATION_FULL_S) / DURATION_FULL_S


# -- the analysis --------------------------------------------------------


def _find_lines(notes: Sequence[Note]) -> frozenset[tuple[int, int]]:
    """Notes that have a stepwise neighbour in time, within their own hand.

    Two notes belong to the same line when one follows the other closely enough
    to be heard as going somewhere, and the step between them is small enough
    to be a step rather than a leap between registers. Notes struck together
    are harmony however close they sit, so they do not count.

    Scanned over a sorted start list with a bounded window rather than compared
    pairwise: a string quartet is six thousand notes, and every stage builds
    one of these.
    """
    ordered = sorted(range(len(notes)), key=lambda i: notes[i].start)
    starts = [notes[i].start for i in ordered]
    found: set[tuple[int, int]] = set()

    for position, index in enumerate(ordered):
        note = notes[index]
        reach = min(
            LINE_GAP_MAX_S, max(LINE_GAP_MIN_S, note.duration * LINE_GAP_FACTOR)
        )
        first = bisect_left(starts, note.start - reach, 0, position)
        last = bisect_right(starts, note.start + reach, position, len(starts))
        for other_position in range(first, last):
            if other_position == position:
                continue
            other = notes[ordered[other_position]]
            if other.hand is not note.hand:
                continue
            step = abs(other.pitch - note.pitch)
            if step == 0 or step > LINE_INTERVAL:
                continue
            if abs(other.start - note.start) < 1e-6:
                continue
            found.add(_key(note))
            break

    return frozenset(found)
