"""Reading a piece as if it were written without pedals.

`pedals.enabled = false` is not a display setting, and the tests here are
mostly about proving that. Dropping the events as the file is read is what
makes every stage agree: the constraint engine stops shortening notes on the
grounds that the damper is off the string, the renderer has no lane to draw,
and the synthesiser is sent no CC64.

The one that matters is
`test_turning_pedals_off_changes_which_repair_the_engine_picks`. Anything that
only checked the events were gone would pass just as well if the switch were
wired to the renderer alone.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import mido
import pytest

from psv.config import Config, ConfigError, HandsConfig, PedalsConfig
from psv.constraints import constrain
from psv.load import read_score
from psv.midi import read_midi
from tests.fixtures.midi_builder import MidiBuilder


def write(path: Path, midi: mido.MidiFile) -> Path:
    midi.save(path)
    return path


def held_bass_midi(*, with_pedal: bool) -> mido.MidiFile:
    """A bass note held into a far-away note, with both hands otherwise busy.

    The MIDI twin of `held_bass_scenario` in test_constraints_repair.py. Two
    tracks, so the parser reads the hands rather than guessing them, and built
    so reassign cannot apply: that is what leaves the choice between truncating
    under the pedal and shifting an octave.
    """
    builder = MidiBuilder()
    builder.track_name("left", track=0)
    builder.note(36, 0.0, 4.0, track=0)
    builder.note(72, 1.0, 2.0, track=0)
    builder.track_name("right", track=1)
    builder.note(90, 0.0, 4.0, track=1)
    if with_pedal:
        builder.pedal(0.0, 4.0, track=0)
    return builder.build()


def config_for(**pedals: object) -> Config:
    return replace(
        Config(),
        hands=HandsConfig(max_span_semitones=12, overlap_tolerance_s=0.03),
        pedals=replace(Config().pedals, **pedals),  # type: ignore[arg-type]
    )


# -- the events go, at the one point every stage reads through ------------


@pytest.mark.feature("F-89")
def test_reading_with_pedals_off_drops_them_and_keeps_the_notes(
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))

    kept = read_score(path)
    dropped = read_score(path, pedals=False)

    assert kept.pedals, "the fixture is meant to have pedalling in it"
    assert dropped.pedals == ()
    # Only the pedalling goes. The music is the same piece.
    assert [n.pitch for n in dropped.notes] == [n.pitch for n in kept.notes]
    assert [n.end for n in dropped.notes] == [n.end for n in kept.notes]


@pytest.mark.feature("F-89")
def test_without_pedals_leaves_a_score_that_had_none_alone() -> None:
    score = read_midi(held_bass_midi(with_pedal=False))
    assert score.without_pedals() == score


# -- the part that makes it more than a display setting -------------------


@pytest.mark.feature("F-89")
def test_turning_pedals_off_changes_which_repair_the_engine_picks(
    tmp_path: Path,
) -> None:
    """The same file, read two ways, arranged two different ways.

    With the pedal down the engine lifts the bass early and calls it inaudible,
    because it is. Read as a piece written without pedals it has to do
    something you would hear instead, and moves the octave.

    A switch that only muted the synth would leave the first arrangement in
    place and play its shortened bass out loud, so this is the assertion that
    would fail on the cheap version of this feature.
    """
    path = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))

    with_pedal = constrain(read_score(path), config_for())
    without = constrain(read_score(path, pedals=False), config_for(enabled=False))

    assert [r.strategy for r in with_pedal.repairs] == ["truncate-under-pedal"]

    plain = [r.strategy for r in without.repairs]
    assert "octave-shift" in plain
    # No truncation of either kind: there is no pedal to make one cheap, and
    # the note it would shorten is the one the octave shift moved instead.
    assert not [s for s in plain if s.startswith("truncate")]


@pytest.mark.feature("F-89")
def test_no_music_goes_missing_without_a_record(tmp_path: Path) -> None:
    """Turning the pedal off costs repairs, and every one of them is reported."""
    path = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))
    score = read_score(path, pedals=False)

    result = constrain(score, config_for(enabled=False))

    lost = len(score.notes) - len(result.score.notes)
    recorded = sum(r.dropped for r in result.repairs) + len(
        result.removed_for_difficulty
    )
    assert lost == recorded, "a note went missing without a record"


# -- config and command line ----------------------------------------------


@pytest.mark.feature("F-89")
def test_the_setting_loads_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "psv.toml"
    path.write_text("[pedals]\nenabled = false\n", encoding="utf-8")
    assert Config.load(path).pedals.enabled is False


@pytest.mark.feature("F-89")
def test_pedals_are_on_unless_a_file_says_otherwise() -> None:
    assert Config().pedals.enabled is True


@pytest.mark.feature("F-89")
def test_a_wrongly_typed_setting_is_still_refused(tmp_path: Path) -> None:
    path = tmp_path / "psv.toml"
    path.write_text("[pedals]\nenabled = 'no'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


@pytest.mark.feature("F-89")
def test_enabled_and_lanes_ask_different_questions() -> None:
    """`lanes = 0` hides the lane. `enabled = false` removes the pedalling.

    Written down because conflating them is the mistake this setting exists to
    avoid, and because `lanes = 0` must stay purely cosmetic.
    """
    hidden = PedalsConfig(lanes=0)
    assert hidden.enabled is True


@pytest.mark.feature("F-89")
def test_the_flag_reaches_the_score(tmp_path: Path) -> None:
    """`--no-pedal export` writes a MIDI with no pedalling in it.

    Through the real CLI rather than by calling the override helper, because
    the flag has to survive argparse's subcommand namespace as well as be
    applied: `-c file.toml` on either side of the subcommand is a distinction
    that has caught this project out before.
    """
    from psv.cli import main

    source = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))
    kept = tmp_path / "kept.mid"
    dropped = tmp_path / "dropped.mid"

    assert main(["export", str(source), "-o", str(kept)]) == 0
    assert main(["export", str(source), "-o", str(dropped), "--no-pedal"]) == 0

    assert read_score(kept).pedals
    assert read_score(dropped).pedals == ()


@pytest.mark.feature("F-89")
def test_inspect_reports_the_file_even_when_the_config_turns_pedals_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`inspect` answers what is in the file; every other command answers what
    psv will do with it. Reporting no pedalling for a pedalled file would be a
    lie about the file, so the setting does not reach here."""
    from psv.cli import main

    source = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))
    config = tmp_path / "psv.toml"
    config.write_text("[pedals]\nenabled = false\n", encoding="utf-8")

    assert main(["-c", str(config), "inspect", str(source)]) == 0
    assert "pedal" in capsys.readouterr().out.lower()


@pytest.mark.feature("F-89")
def test_inspect_refuses_the_flag_rather_than_ignoring_it(tmp_path: Path) -> None:
    """A flag that would do nothing is a usage error, not a silent no-op."""
    from psv.cli import main

    source = write(tmp_path / "pedalled.mid", held_bass_midi(with_pedal=True))
    with pytest.raises(SystemExit) as exc:
        main(["inspect", str(source), "--no-pedal"])
    assert exc.value.code == 2
