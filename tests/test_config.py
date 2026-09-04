"""Config loading and validation.

The point of this module is that a mistake is reported, not absorbed. A typo in
a colour key should say so, rather than leaving a render quietly wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from psv.config import MAX_ALLOWED_SPAN, Config, ConfigError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "psv.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.feature("F-10")
def test_defaults_load_and_validate_without_a_file() -> None:
    config = Config.load(None)
    assert config.hands.max_span_semitones == 12
    assert config.pedals.lanes == 1
    assert config.audio.backend == "builtin"
    assert config.visual.colors.left_hand != config.visual.colors.right_hand


@pytest.mark.feature("F-10")
def test_a_partial_file_overrides_only_what_it_names(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        [hands]
        max_span_semitones = 15

        [visual.grid]
        beat_lines = "bar"
        """,
    )
    config = Config.load(path)
    assert config.hands.max_span_semitones == 15
    assert config.visual.grid.beat_lines == "bar"
    # Untouched keys keep their defaults.
    assert config.visual.grid.pitch_lines == "octave"
    assert config.visual.fps == 60


@pytest.mark.feature("F-10")
def test_an_unknown_key_is_an_error_and_names_the_valid_ones(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "[hands]\nmax_span = 12\n")
    with pytest.raises(ConfigError) as exc:
        Config.load(path)
    message = str(exc.value)
    assert "max_span" in message
    assert "max_span_semitones" in message


@pytest.mark.feature("F-10")
def test_an_unknown_top_level_table_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "[colours]\nleft = '#fff'\n")
    with pytest.raises(ConfigError, match="colours"):
        Config.load(path)


@pytest.mark.feature("F-10")
@pytest.mark.parametrize("span", [-1, MAX_ALLOWED_SPAN + 1, 88])
def test_a_span_outside_human_reach_is_rejected(tmp_path: Path, span: int) -> None:
    """The hand-span limit is an invariant. It cannot be set to something the
    constraint engine would have to ignore."""
    path = write(tmp_path, f"[hands]\nmax_span_semitones = {span}\n")
    with pytest.raises(ConfigError, match="max_span_semitones"):
        Config.load(path)


@pytest.mark.feature("F-10")
def test_the_documented_readme_example_is_valid(tmp_path: Path) -> None:
    """Guards against the README drifting away from what the loader accepts."""
    path = write(
        tmp_path,
        """
        [hands]
        max_span_semitones = 12

        [difficulty]
        level = "medium"

        [visual]
        black_key_bar_width = 0.6
        black_key_darkening = 0.2

        [visual.colors]
        left_hand = "#4a90d9"
        right_hand = "#5fb87a"
        quiet = 0.35
        loud = 1.0

        [visual.grid]
        pitch_lines = "octave"
        beat_lines = "beat"

        [pedals]
        lanes = 1

        [audio]
        backend = "none"
        """,
    )
    Config.load(path)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("[difficulty]\nlevel = 'impossible'\n", "difficulty.level"),
        ("[visual.colors]\nleft_hand = 'blue'\n", "hex colour"),
        ("[visual.colors]\nquiet = 0.9\nloud = 0.2\n", "quiet <= loud"),
        ("[visual.grid]\nbeat_lines = 'sometimes'\n", "beat_lines must be"),
        ("[visual.grid]\npitch_lines = 'thirds'\n", "pitch_lines must be"),
        ("[visual.grid]\nopacity = 2.0\n", "opacity"),
        ("[visual]\nbackground = '#204060'\n", "grayscale"),
        ("[visual.colors]\npedal = 'gold'\n", "hex colour"),
        ("[visual.colors]\nunassigned = 'grey'\n", "hex colour"),
        ("[visual]\nfps = 0\n", "visual.fps"),
        ("[visual]\nblack_key_bar_width = 1.5\n", "black_key_bar_width"),
        ("[pedals]\nlanes = 4\n", "pedals.lanes"),
        ("[pedals]\nthreshold = 0\n", "pedals.threshold"),
        ("[audio]\nbackend = 'winamp'\n", "audio.backend"),
        ("[audio]\nbackend = 'fluidsynth'\n", "requires audio.soundfont"),
        ("[audio]\nbackend = 'mux'\n", "requires audio.audio_file"),
    ],
)
def test_out_of_range_values_are_rejected_with_a_useful_message(
    tmp_path: Path, body: str, fragment: str
) -> None:
    with pytest.raises(ConfigError, match=fragment):
        Config.load(write(tmp_path, body))


def test_a_wrongly_typed_value_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "[visual]\nfps = 'sixty'\n")
    with pytest.raises(ConfigError, match="must be int"):
        Config.load(path)


def test_an_integer_is_accepted_where_a_float_is_wanted(tmp_path: Path) -> None:
    config = Config.load(write(tmp_path, "[visual]\nlookahead_s = 4\n"))
    assert config.visual.lookahead_s == pytest.approx(4.0)


def test_a_table_given_as_a_scalar_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "hands = 12\n")
    with pytest.raises(ConfigError, match="must be a table"):
        Config.load(path)


def test_malformed_toml_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "[hands\nmax_span_semitones = 12\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        Config.load(path)


def test_a_missing_config_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not read"):
        Config.load(tmp_path / "absent.toml")


@pytest.mark.feature("F-55")
def test_a_span_of_zero_means_no_limit(tmp_path: Path) -> None:
    """Not a weaker guarantee, a different request. The promise is that output
    never exceeds the *configured* span, and this configures no span."""
    config = Config.load(write(tmp_path, "[hands]\nmax_span_semitones = 0\n"))
    assert not config.hands.is_limited
    assert config.hands.max_span_semitones == 0


@pytest.mark.feature("F-55")
def test_hand_assignment_still_has_a_span_to_lay_out_against(tmp_path: Path) -> None:
    """Splitting the notes between two hands is a separate question from
    limiting the reach, and it still needs an answer when nothing is limited."""
    config = Config.load(write(tmp_path, "[hands]\nmax_span_semitones = 0\n"))
    assert config.hands.layout_span > 0

    limited = Config.load(write(tmp_path, "[hands]\nmax_span_semitones = 14\n"))
    assert limited.hands.layout_span == 14
    assert limited.hands.is_limited


@pytest.mark.feature("F-70")
def test_an_unknown_encode_level_says_which_ones_exist(tmp_path: Path) -> None:
    path = write(tmp_path, '[visual]\nencode = "tiny"\n')
    with pytest.raises(ConfigError, match=r"visual\.encode must be one of"):
        Config.load(path)


@pytest.mark.feature("F-69")
def test_a_negative_worker_count_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "[visual]\nworkers = -1\n")
    with pytest.raises(ConfigError, match=r"visual\.workers"):
        Config.load(path)


@pytest.mark.feature("F-69")
def test_the_render_settings_have_working_defaults() -> None:
    visual = Config.load(None).visual
    assert visual.workers == 0, "0 means one process per core"
    assert visual.encode == "balanced"
