"""The title card and the fades.

A pass over a finished video, so most of it is testable without encoding
anything: what the card says, what the filter graph asks ffmpeg for, and what
the config will and will not accept. One test runs the real thing end to end,
because a filter graph that reads correctly and that ffmpeg rejects is worth
nothing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from psv.config import Config, ConfigError, TitleConfig
from psv.musicxml import read_musicxml_file
from psv.render.title import (
    Card,
    TitleError,
    build_card,
    card_text,
    filter_chain,
    find_font,
    summary,
)
from tests.fixtures.musicxml_builder import attributes, measure, note, score, tempo

CARD = TitleConfig(seconds=3.0, fade_out_s=1.0, hold_s=1.0)


# -- what the card says --------------------------------------------------


@pytest.mark.feature("F-86")
def test_the_card_takes_its_words_from_the_score_when_it_is_given_none() -> None:
    """The usual case needs nothing typed: MusicXML already says both."""
    card = card_text(TitleConfig(seconds=3.0), title="Für Elise", composer="Beethoven")
    assert card.title == "Für Elise"
    assert card.composer == "Beethoven"


@pytest.mark.feature("F-86")
def test_the_config_overrides_the_score() -> None:
    """So a bad transcription can be corrected without editing the file."""
    card = card_text(
        TitleConfig(seconds=3.0, text="Bagatelle No. 25", composer="L. v. Beethoven"),
        title="fur_elise",
        composer="Unknown",
    )
    assert card.title == "Bagatelle No. 25"
    assert card.composer == "L. v. Beethoven"


@pytest.mark.feature("F-86")
def test_a_card_with_nothing_on_it_knows_it_is_empty() -> None:
    assert card_text(TitleConfig(seconds=3.0)).is_empty
    assert not card_text(TitleConfig(seconds=3.0), title="Something").is_empty


@pytest.mark.feature("F-86")
def test_a_composer_is_read_from_musicxml(tmp_path: Path) -> None:
    """MIDI has nowhere to put this and MusicXML does, which is the whole
    reason the card needs nothing typed for a real score."""
    path = tmp_path / "score.musicxml"
    path.write_text(
        score(
            measure(1, attributes(meter=(3, 4)), note("D4", 3.0)),
            title="Waltz",
            composer="Chopin",
        ),
        encoding="utf-8",
    )
    read = read_musicxml_file(path)
    assert read.title == "Waltz"
    assert read.composer == "Chopin"


# -- the filter graph ----------------------------------------------------


@pytest.mark.feature("F-86")
def test_the_graph_asks_for_exactly_what_was_turned_on() -> None:
    chain = filter_chain(CARD, duration=30.0, has_audio=True)
    assert "overlay" in chain, "no card"
    assert "fade=t=out:st=29.000" in chain, "no fade, or in the wrong place"
    assert "tpad" in chain, "nothing held"
    assert "afade" in chain and "apad" in chain, "the sound was left behind"


@pytest.mark.feature("F-86")
def test_a_fade_with_no_card_draws_no_card() -> None:
    chain = filter_chain(TitleConfig(fade_out_s=2.0), duration=30.0, has_audio=True)
    assert "overlay" not in chain
    assert "fade=t=out" in chain


@pytest.mark.feature("F-86")
def test_a_silent_video_is_not_given_an_audio_filter() -> None:
    """The pipeline writes a silent video when every backend fails, and mapping
    a stream that is not there makes ffmpeg refuse the whole graph."""
    chain = filter_chain(CARD, duration=30.0, has_audio=False)
    assert "[a]" not in chain
    assert "[v]" in chain


@pytest.mark.feature("F-86")
@pytest.mark.parametrize("curve", ["ease", "linear", "slow"])
def test_every_curve_reaches_the_graph(curve: str) -> None:
    chain = filter_chain(replace(CARD, curve=curve), duration=30.0, has_audio=False)
    assert "geq" in chain and "alpha" in chain


@pytest.mark.feature("F-86")
def test_the_card_clears_before_it_leaves_the_screen() -> None:
    """The gap between the two is clear screen, so the first notes are plainly
    visible falling before any of them lands."""
    config = TitleConfig(seconds=4.0)
    assert config.clears_at == pytest.approx(2.8)
    assert config.clears_at < config.seconds

    named = TitleConfig(seconds=4.0, clear_at=1.5)
    assert named.clears_at == pytest.approx(1.5)


# -- what the config will not accept -------------------------------------


@pytest.mark.feature("F-86")
def test_a_card_that_never_clears_is_an_error() -> None:
    with pytest.raises(ConfigError, match="never clear"):
        TitleConfig(seconds=2.0, clear_at=3.0).validate()


@pytest.mark.feature("F-86")
def test_an_unknown_curve_lists_the_real_ones() -> None:
    with pytest.raises(ConfigError, match="ease"):
        TitleConfig(seconds=2.0, curve="bounce").validate()


@pytest.mark.feature("F-86")
@pytest.mark.parametrize(
    ("field", "config"),
    [
        ("seconds", TitleConfig(seconds=-1.0)),
        ("fade_out_s", TitleConfig(fade_out_s=-1.0)),
        ("hold_s", TitleConfig(hold_s=-1.0)),
        ("clear_at", TitleConfig(clear_at=-1.0)),
    ],
)
def test_negative_times_are_an_error(field: str, config: TitleConfig) -> None:
    with pytest.raises(ConfigError, match=field):
        config.validate()


@pytest.mark.feature("F-86")
def test_the_title_section_loads_from_a_config_file(tmp_path: Path) -> None:
    path = tmp_path / "psv.toml"
    path.write_text(
        '[title]\nseconds = 3.5\ncomposer = "Satie"\ncurve = "slow"\n',
        encoding="utf-8",
    )
    config = Config.load(path)
    assert config.title.seconds == pytest.approx(3.5)
    assert config.title.composer == "Satie"
    assert config.title.is_on


@pytest.mark.feature("F-86")
def test_the_whole_thing_is_off_by_default() -> None:
    """A practice video wants neither a card nor a fade, and this is the only
    step that re-encodes the finished file."""
    assert not Config().title.is_on


# -- drawing -------------------------------------------------------------


@pytest.mark.feature("F-86")
def test_the_card_is_drawn_and_has_ink_on_it(tmp_path: Path) -> None:
    import numpy as np
    from PIL import Image

    path = build_card(
        TitleConfig(seconds=3.0),
        Card(title="Nocturne", composer="Chopin", footer="channel"),
        480,
        270,
        tmp_path / "card.png",
    )
    pixels = np.array(Image.open(path).convert("RGB"))
    assert pixels.shape == (270, 480, 3)
    assert len(np.unique(pixels.reshape(-1, 3), axis=0)) > 1, (
        "the card is a flat screen with no text on it"
    )


@pytest.mark.feature("F-86")
def test_a_named_font_that_is_not_there_says_so() -> None:
    with pytest.raises(TitleError, match="no font file"):
        find_font("/no/such/font.ttf")


@pytest.mark.feature("F-86")
def test_a_missing_font_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A video is worth more than the typeface it was going to be set in."""
    monkeypatch.setattr("psv.render.title.FONT_DIRS", ())
    assert find_font("") is None


@pytest.mark.feature("F-86")
def test_the_summary_says_what_was_done() -> None:
    line = summary(CARD, Card(title="Gymnopédie", composer="Satie", footer=""))
    assert "Gymnopédie" in line
    assert "Satie" in line
    assert "held black" in line
    assert summary(TitleConfig(), Card("", "", "")) == ""


# -- the real thing ------------------------------------------------------


@pytest.mark.feature("F-86")
def test_a_run_with_a_card_makes_a_longer_video_with_the_score_on_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The one test that encodes.

    A filter graph that reads correctly and that ffmpeg rejects is worth
    nothing, and the argument for MusicXML is that nothing has to be typed:
    both the title and the composer come off the file.
    """
    from psv.cli import main
    from tests.probe import video_meta

    source = tmp_path / "waltz.musicxml"
    source.write_text(
        score(
            measure(1, attributes(meter=(3, 4)), tempo(120), note("D4", 3.0)),
            measure(2, note("E4", 3.0)),
            title="Waltz",
            composer="Chopin",
        ),
        encoding="utf-8",
    )
    tiny = ["--width", "160", "--height", "120", "--fps", "10"]

    plain = tmp_path / "plain.mp4"
    assert main(["run", str(source), "-o", str(plain), *tiny]) == 0

    titled = tmp_path / "titled.mp4"
    assert (
        main(
            [
                "run",
                str(source),
                "-o",
                str(titled),
                *tiny,
                "--title-card",
                "1",
                "--fade-out",
                "0.5",
                "--hold-black",
                "1",
            ]
        )
        == 0
    )

    printed = capsys.readouterr().out
    assert "Waltz" in printed and "Chopin" in printed, printed
    assert "held black" in printed

    # The held black is time the plain render does not have.
    assert video_meta(titled)["duration"] > video_meta(plain)["duration"] + 0.5


@pytest.mark.feature("F-86")
def test_the_title_options_are_refused_by_render(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`psv render` writes the picture only, so a card there would be drawn and
    then thrown away by whatever muxes the sound on afterwards."""
    from psv.cli import main

    source = tmp_path / "waltz.musicxml"
    source.write_text(
        score(
            measure(1, attributes(meter=(3, 4)), tempo(120), note("D4", 3.0)),
            title="Waltz",
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "render",
            str(source),
            "-o",
            str(tmp_path / "out.mp4"),
            "--width",
            "160",
            "--height",
            "120",
            "--fps",
            "10",
            "--title-card",
            "1",
        ]
    )
    assert code == 2
    assert "psv run" in capsys.readouterr().err
