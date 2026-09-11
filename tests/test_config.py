"""Config loading and validation.

The point of this module is that a mistake is reported, not absorbed. A typo in
a colour key should say so, rather than leaving a render quietly wrong.
"""

from __future__ import annotations

import logging
from dataclasses import fields
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


#: The config reference in the README, which is the block starting at `[hands]`.
README = Path(__file__).resolve().parent.parent / "README.md"


def readme_config() -> str:
    """The README's config block, verbatim."""
    text = README.read_text(encoding="utf-8")
    start = text.index("```toml\n[hands]") + len("```toml\n")
    return text[start : text.index("```", start)]


@pytest.mark.feature("F-10")
def test_the_documented_readme_example_is_valid(tmp_path: Path) -> None:
    """The README's config block, loaded as written.

    It used to be a hand-copied subset, which meant the docstring's promise was
    not true: the README drifted into two `[visual]` headers, which TOML rejects
    outright, and an `[[visual.effects]]` entry in the middle of the table that
    would have swallowed the two keys after it. A copy cannot catch that. This
    reads the file.
    """
    path = write(tmp_path, readme_config())
    Config.load(path)


@pytest.mark.feature("F-10")
def test_the_readme_documents_every_config_key() -> None:
    """A key nobody can find is a key nobody sets.

    Compared against the dataclasses rather than against a list, so adding a
    setting and forgetting to write it down fails here rather than never.
    """
    import tomllib

    documented = tomllib.loads(readme_config())
    missing = [
        f"{section.name}.{key.name}"
        for section in fields(Config)
        for key in fields(getattr(Config(), section.name))
        if key.name not in documented.get(section.name, {})
    ]
    assert not missing, f"undocumented config keys: {missing}"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("[difficulty]\nlevel = 'impossible'\n", "difficulty.level"),
        ("[visual.colors]\nleft_hand = 'blue'\n", "hex colour"),
        ("[visual.colors]\nquiet = 1.9\n", "between 0 and 1"),
        ("[visual.grid]\nbeat_lines = 'sometimes'\n", "beat_lines must be"),
        ("[visual.grid]\npitch_lines = 'thirds'\n", "pitch_lines must be"),
        ("[visual.grid]\nopacity = 2.0\n", "opacity"),
        ("[visual]\nbackground = 'navy'\n", "hex colour"),
        ("[visual.colors]\npedal = 'gold'\n", "hex colour"),
        ("[visual.colors]\nunassigned = 'grey'\n", "hex colour"),
        ("[visual]\nfps = 0\n", "visual.fps"),
        ("[visual]\nblack_key_bar_width = 0\n", "black_key_bar_width"),
        ("[pedals]\nlanes = 4\n", "pedals.lanes"),
        ("[pedals]\nthreshold = 0\n", "pedals.threshold"),
        ("[audio]\nreverb = 2\n", "audio.reverb"),
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


# -- the three tiers -----------------------------------------------------


#: Every value that is odd rather than impossible, with the words the warning
#: uses. Listed here so a check that quietly turns back into a hard error has
#: somewhere to fail.
COSMETIC = [
    ("[visual.colors]\nquiet = 0.9\nloud = 0.2\n", "brighter than loud"),
    ("[visual]\nblack_key_bar_width = 1.5\n", "wider than a white-key"),
    ("[visual]\nnote_border = 0.05\n", "mostly outline"),
    ("[visual]\nnote_radius = 0.9\n", "draws as 0.5"),
    ("[visual]\ngradient_top = '#101010'\n", "stays the flat"),
    ("[practice]\ncount_in_bars = 20\n", "waiting, not counting"),
    ("[title]\nseconds = 45.0\n", "a wait rather than an introduction"),
    ("[title]\nseconds = 2.0\nclear_at = 3.0\n", "will not fade"),
]


@pytest.mark.feature("F-91")
@pytest.mark.parametrize(("body", "fragment"), COSMETIC)
def test_a_cosmetic_value_warns_and_loads(
    tmp_path: Path, body: str, fragment: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The warn tier: the render happens, and the config says why it may look odd.

    These were all hard errors once. A forty-minute render refused over
    somebody else's taste is a worse outcome than a video somebody else would
    not have made.
    """
    with caplog.at_level(logging.WARNING, logger="psv.config"):
        config = Config.load(write(tmp_path, body))

    config.validate()
    assert fragment in caplog.text


@pytest.mark.feature("F-91")
def test_a_warning_is_given_once_however_often_it_is_validated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The CLI validates a section again after applying a flag over it.

    Hearing the same sentence three times teaches nothing the first did not.
    """
    with caplog.at_level(logging.WARNING, logger="psv.config"):
        config = Config.load(write(tmp_path, "[visual]\nnote_radius = 0.9\n"))
        config.validate()
        config.validate()

    assert caplog.text.count("draws as 0.5") == 1


@pytest.mark.feature("F-91")
def test_nothing_in_the_warn_tier_blocks_a_render(tmp_path: Path) -> None:
    """Every cosmetic oddity at once, in one file, and it still loads.

    Written out rather than joining the cases above, because TOML rejects a
    repeated table header and a real file would collect them under one.
    """
    merged = """
        [visual]
        black_key_bar_width = 1.5
        note_border = 0.05
        note_radius = 0.9
        gradient_top = '#101010'

        [visual.colors]
        quiet = 0.9
        loud = 0.2

        [practice]
        count_in_bars = 20

        [title]
        seconds = 45.0
        clear_at = 50.0
        """
    Config.load(write(tmp_path, merged)).validate()


@pytest.mark.feature("F-91")
def test_an_impossible_value_is_still_refused(tmp_path: Path) -> None:
    """The error tier did not soften. Each of these would fail or draw garbage."""
    for body in (
        "[visual]\nfps = 0\n",
        "[visual]\nwidth = 1921\n",
        "[visual]\nnote_radius = -0.1\n",
        "[visual]\nbackground = 'navy'\n",
        "[hands]\nmax_span_semitones = 99\n",
        "[audio]\nbackend = 'winamp'\n",
        "[nonsense]\nkey = 1\n",
    ):
        with pytest.raises(ConfigError):
            Config.load(write(tmp_path, body))
