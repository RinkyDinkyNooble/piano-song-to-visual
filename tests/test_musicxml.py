"""Reading MusicXML.

Two suites in one file. The generated fixtures run everywhere and say exactly
what they test; the fetched Unofficial MusicXML Test Suite is MIT and therefore
gitignored, so those tests skip unless someone has run
`python scripts/fetch_test_scores.py`.

The thing worth testing hardest is what MusicXML gives that MIDI cannot: the
file states which staff a note is on, and a staff is a hand. Everything else
here exists because it is easy to get wrong, and `<backup>` is the easiest.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from psv.load import ScoreReadError, read_score, score_format
from psv.model import Hand, Pedal
from psv.musicxml import MusicXmlReadError, read_musicxml_file
from tests.fixtures.musicxml_builder import FIXTURES, compressed, write

SCORES = Path(__file__).resolve().parent / "assets" / "scores"


#: Writes one generated fixture into the test's own directory and hands back
#: the path, the way `midi_path` does for the MIDI fixtures.
ScorePath = Callable[[str], Path]


@pytest.fixture
def score_path(tmp_path: Path) -> ScorePath:
    def _write(name: str) -> Path:
        return Path(write(name, str(tmp_path)))

    return _write


def suite(name: str) -> Path:
    """A file from the fetched suite, skipping when it is not there."""
    path = SCORES / name
    if not path.exists():
        pytest.skip(f"{name} not fetched; run scripts/fetch_test_scores.py")
    return path


# -- the reason this reader exists ---------------------------------------


@pytest.mark.feature("F-61")
def test_staves_are_hands(score_path: ScorePath) -> None:
    """The whole point. MIDI made us guess from track names, and guessing wrong
    cost a real file a quarter of its notes."""
    score = read_musicxml_file(score_path("piano-two-staves"))
    by_hand = {note.hand: note for note in score.notes}

    assert set(by_hand) == {Hand.LEFT, Hand.RIGHT}
    assert by_hand[Hand.RIGHT].pitch > by_hand[Hand.LEFT].pitch


@pytest.mark.feature("F-61")
def test_a_real_piano_staff_file_splits_the_hands() -> None:
    """The suite's own two-staff case: F4 above, B2 below."""
    score = read_musicxml_file(suite("43a-PianoStaff.musicxml"))
    hands = {note.hand: note.pitch for note in score.notes}
    assert hands == {Hand.RIGHT: 65, Hand.LEFT: 47}


@pytest.mark.feature("F-61")
def test_several_parts_are_instruments_not_hands(score_path: ScorePath) -> None:
    """Two staves in one part are hands. Two parts are an ensemble, and the
    arrange stage should decide, not this reader."""
    score = read_musicxml_file(score_path("ensemble"))
    assert {note.hand for note in score.notes} == {Hand.UNASSIGNED}


# -- timing --------------------------------------------------------------


@pytest.mark.feature("F-62")
def test_a_tie_makes_one_note_not_two(score_path: ScorePath) -> None:
    """Two tied whole notes are one four-second note at 120 bpm. Getting this
    wrong doubles the note count and re-attacks the string."""
    score = read_musicxml_file(score_path("tied-note"))
    assert len(score.notes) == 1
    assert score.notes[0].start == pytest.approx(0.0)
    assert score.notes[0].end == pytest.approx(4.0)


@pytest.mark.feature("F-62")
def test_a_tie_in_the_real_suite_makes_one_note() -> None:
    score = read_musicxml_file(suite("33b-Spanners-Tie.musicxml"))
    assert len(score.notes) == 1
    assert score.notes[0].end == pytest.approx(4.0)


@pytest.mark.feature("F-62")
def test_a_tie_that_never_ends_does_not_hang() -> None:
    """A malformed tie must not swallow the rest of the piece."""
    score = read_musicxml_file(suite("33i-Ties-NotEnded.musicxml"))
    assert score.notes
    assert score.duration < 60.0


@pytest.mark.feature("F-62")
def test_chord_members_share_an_attack(score_path: ScorePath) -> None:
    """`<chord/>` means no time passed since the previous note."""
    score = read_musicxml_file(score_path("chord"))
    first_three = [n for n in score.notes if n.start == pytest.approx(0.0)]
    assert len(first_three) == 3
    assert {n.pitch for n in first_three} == {60, 64, 67}
    assert [n for n in score.notes if n.start > 0.5], "the fourth note follows"


@pytest.mark.feature("F-62")
def test_backup_rewinds_the_cursor_for_a_second_voice(score_path: ScorePath) -> None:
    """The single most error-prone element in the format: a second voice is
    written by rewinding, not by stating a time."""
    score = read_musicxml_file(score_path("two-voices"))
    starts = sorted({round(n.start, 3) for n in score.notes})
    assert starts == [0.0, 0.5], "the lower voice starts with the upper one"

    low = min(score.notes, key=lambda n: n.pitch)
    assert low.start == pytest.approx(0.0)
    assert low.end == pytest.approx(1.0)


@pytest.mark.feature("F-62")
def test_a_real_backup_file_places_both_voices() -> None:
    score = read_musicxml_file(suite("03b-Rhythm-Backup.musicxml"))
    assert len(score.notes) == 4
    assert sorted({round(n.start, 3) for n in score.notes}) == [0.0, 0.5, 1.0]


@pytest.mark.feature("F-62")
def test_forward_leaves_a_gap(score_path: ScorePath) -> None:
    score = read_musicxml_file(score_path("forward-gap"))
    assert len(score.notes) == 1
    assert score.notes[0].start == pytest.approx(1.0)


@pytest.mark.feature("F-62")
def test_divisions_may_change_mid_score(score_path: ScorePath) -> None:
    """A duration is counted in whatever divisions were last declared, so the
    same number means different lengths in different bars."""
    score = read_musicxml_file(score_path("division-change"))
    assert score.duration == pytest.approx(4.0)
    assert [round(n.start, 3) for n in score.notes] == [0.0, 2.0]


@pytest.mark.feature("F-62")
def test_a_score_with_no_divisions_still_reads() -> None:
    score = read_musicxml_file(suite("03e-Rhythm-No-Divisions.musicxml"))
    assert score.notes


@pytest.mark.feature("F-63")
def test_tempo_changes_stretch_the_music(score_path: ScorePath) -> None:
    """Four beats at 120 then four at 60: two seconds then four."""
    score = read_musicxml_file(score_path("tempo-change"))
    assert score.duration == pytest.approx(6.0)
    assert score.tempo_map.bpm_at(0.0) == pytest.approx(120.0, rel=1e-3)
    assert score.tempo_map.bpm_at(3.0) == pytest.approx(60.0, rel=1e-3)


@pytest.mark.feature("F-63")
def test_the_meter_reaches_the_bar_index(score_path: ScorePath) -> None:
    score = read_musicxml_file(score_path("meter-change"))
    assert [(t.numerator, t.denominator) for t in score.time_signatures] == [
        (4, 4),
        (3, 4),
    ]
    assert score.meter.bar_start(2) == pytest.approx(2.0)


@pytest.mark.feature("F-62")
def test_rests_take_time_and_make_no_note(score_path: ScorePath) -> None:
    score = read_musicxml_file(score_path("rests-only"))
    assert len(score.notes) == 1
    assert score.notes[0].start == pytest.approx(3.0)


@pytest.mark.feature("F-62")
def test_grace_notes_exist_and_take_almost_no_time(score_path: ScorePath) -> None:
    """They have no duration in the file. They must still be played, and must
    not consume any of the following note's time."""
    score = read_musicxml_file(score_path("grace-notes"))
    grace, main = sorted(score.notes, key=lambda n: n.pitch)

    assert grace.duration > 0, "a zero-length note is not playable"
    assert grace.duration < 0.5
    assert main.start == pytest.approx(0.0), "the real note keeps its place"
    assert main.end == pytest.approx(2.0)


# -- what MIDI could only guess at ---------------------------------------


@pytest.mark.feature("F-64")
def test_dynamics_become_velocity(score_path: ScorePath) -> None:
    """Written as `pp` and `ff`, not inferred from velocity bytes. The Rondo
    had no dynamics at all in MIDI and rendered completely flat."""
    score = read_musicxml_file(score_path("dynamics-and-pedal"))
    quiet, loud = sorted(score.notes, key=lambda n: n.start)
    assert quiet.velocity < loud.velocity
    assert quiet.velocity < 40 < loud.velocity


@pytest.mark.feature("F-64")
def test_pedal_marks_become_pedal_events(score_path: ScorePath) -> None:
    score = read_musicxml_file(score_path("dynamics-and-pedal"))
    assert len(score.pedals) == 1
    assert score.pedals[0].pedal is Pedal.SUSTAIN
    assert score.pedals[0].start == pytest.approx(0.0)
    assert score.pedals[0].end > 0.5


@pytest.mark.feature("F-64")
def test_a_real_directions_file_yields_pedal_and_a_range_of_dynamics() -> None:
    score = read_musicxml_file(suite("31a-Directions.musicxml"))
    assert score.pedals
    assert len({note.velocity for note in score.notes}) > 3


# -- the file itself -----------------------------------------------------


@pytest.mark.feature("F-65")
def test_a_compressed_mxl_reads_the_same_as_the_plain_file(tmp_path: Path) -> None:
    """.mxl is a zip with a manifest naming the real score."""
    plain = read_musicxml_file(write("piano-two-staves", str(tmp_path)))
    packed = read_musicxml_file(compressed("piano-two-staves", str(tmp_path)))

    assert [n.pitch for n in packed.notes] == [n.pitch for n in plain.notes]
    assert [n.hand for n in packed.notes] == [n.hand for n in plain.notes]


@pytest.mark.feature("F-65")
def test_the_format_is_told_from_content_not_extension(tmp_path: Path) -> None:
    """Notation software exports `.xml` as readily as `.musicxml`, and a `.xml`
    may be anything at all."""
    from tests.fixtures.midi_builder import FIXTURES as MIDI_FIXTURES

    misnamed = tmp_path / "actually-musicxml.mid"
    misnamed.write_text(FIXTURES["chord"](), encoding="utf-8")
    assert score_format(misnamed) == "musicxml"

    midi = tmp_path / "actually-midi.musicxml"
    MIDI_FIXTURES["single-note"]().save(midi)
    assert score_format(midi) == "midi"
    assert read_score(midi).notes


@pytest.mark.feature("F-65")
def test_a_file_that_is_neither_is_refused(tmp_path: Path) -> None:
    junk = tmp_path / "nonsense.xml"
    junk.write_bytes(b"\x00\x01\x02 not a score at all")
    with pytest.raises(ScoreReadError, match="neither MIDI nor MusicXML"):
        read_score(junk)


@pytest.mark.feature("F-65")
def test_xml_that_is_not_a_score_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "other.xml"
    other.write_text("<html><body>hello</body></html>", encoding="utf-8")
    with pytest.raises(MusicXmlReadError, match="not a MusicXML score"):
        read_musicxml_file(other)


@pytest.mark.feature("F-65")
def test_timewise_scores_say_what_to_do(tmp_path: Path) -> None:
    """The other MusicXML layout. Rare, and better refused clearly than parsed
    into silence."""
    path = tmp_path / "timewise.musicxml"
    path.write_text('<score-timewise version="4.0"/>', encoding="utf-8")
    with pytest.raises(MusicXmlReadError, match="score-partwise"):
        read_musicxml_file(path)


@pytest.mark.feature("F-65")
def test_an_empty_score_is_not_an_error(score_path: ScorePath) -> None:
    score = read_musicxml_file(score_path("empty"))
    assert score.notes == ()
    assert score.duration == pytest.approx(0.0)


@pytest.mark.feature("F-65")
def test_the_title_comes_from_the_file(score_path: ScorePath) -> None:
    assert read_musicxml_file(score_path("chord")).title == "chord"


# -- nothing silently disappears -----------------------------------------


@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_every_fixture_reads_and_survives_the_pipeline(
    fixture: str, score_path: ScorePath
) -> None:
    """Same conservation invariant the MIDI front end is held to: a stage may
    remove notes, but not quietly."""
    from psv.arrange import arrange
    from psv.config import Config
    from psv.constraints import constrain

    config = Config.load(None)
    score = read_musicxml_file(score_path(fixture))
    before = len(score.notes)

    arranged = arrange(score, max_span=config.hands.layout_span)
    assert len(arranged.score.notes) + len(arranged.dropped) == before

    result = constrain(arranged.score, config)
    reported = sum(1 for r in result.repairs if r.strategy == "drop") + len(
        result.removed_for_difficulty
    )
    assert len(arranged.score.notes) - len(result.score.notes) == reported


@pytest.mark.parametrize(
    "name", sorted(p.name for p in SCORES.glob("*.musicxml")) or ["<none fetched>"]
)
def test_every_fetched_suite_file_reads_without_crashing(name: str) -> None:
    """The suite is 23 files of deliberate awkwardness. None of them may raise
    anything other than this project's own error type."""
    path = suite(name)
    score = read_musicxml_file(path)
    assert score.duration >= 0.0
    for note in score.notes:
        assert 0 <= note.pitch <= 127
        assert note.end >= note.start


@pytest.mark.feature("F-61")
def test_inspect_agrees_with_arrange_when_hands_cross(score_path: ScorePath) -> None:
    """Für Elise's left hand crosses up over the right, so the two registers
    overlap by far more than an octave. Its hands are nonetheless known, because
    MusicXML states them, and `arrange` correctly leaves the file alone. The
    report used to say "needs the arrange stage" about it anyway.

    Third time this exact disagreement has cost something, so it is asserted
    rather than trusted.
    """
    from psv.arrange import looks_arranged
    from psv.inspect import inspect_score
    from psv.model import Note, Part, Score

    crossed = Score(
        parts=(
            Part(
                name="right",
                hand=Hand.RIGHT,
                notes=tuple(
                    Note(
                        pitch=64 + i % 3,
                        start=i * 0.5,
                        end=i * 0.5 + 0.4,
                        hand=Hand.RIGHT,
                    )
                    for i in range(12)
                ),
            ),
            Part(
                name="left",
                hand=Hand.LEFT,
                # Reaches well above the right hand, as a crossing bass line does.
                notes=tuple(
                    Note(
                        pitch=40 + i * 3,
                        start=i * 0.5,
                        end=i * 0.5 + 0.4,
                        hand=Hand.LEFT,
                    )
                    for i in range(12)
                ),
            ),
        )
    )
    report = inspect_score(crossed)
    assert report.hands_assigned
    assert report.looks_pre_separated, "the report must not contradict the stage"
    assert looks_arranged(crossed)


@pytest.mark.feature("F-61")
def test_register_still_decides_when_hands_are_unknown(score_path: ScorePath) -> None:
    """The fallback has to keep working for MIDI, which usually says nothing."""
    from psv.inspect import inspect_score

    score = read_musicxml_file(score_path("ensemble"))
    assert not inspect_score(score).hands_assigned
