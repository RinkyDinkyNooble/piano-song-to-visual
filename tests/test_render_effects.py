"""Optional visual effects.

Three promises are worth more than the effects themselves, and they are what
most of this file tests.

Off means literally unchanged, not visually similar. An intensity of 0 is a
no-op, which is what makes the slider trustworthy. And every effect is a pure
function of the score and a time, so a frame drawn in a worker process is the
same frame, byte for byte, as one drawn here. That last one is not decoration:
the renderer cuts the timeline into spans and hands them to separate processes,
and an effect that carried state would seam at every boundary.
"""

from __future__ import annotations

import multiprocessing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from psv.config import Config, ConfigError, EffectConfig, VisualConfig
from psv.midi import read_midi
from psv.presets import (
    EFFECT_DESCRIPTIONS,
    EFFECT_SETS,
    apply_effect_set,
)
from psv.render.effects import KINDS, PAINTERS, background_for, pulse_lift
from psv.render.frame import render_frame
from tests.fixtures.midi_builder import FIXTURES
from tests.test_render_frame import NO_LANES, SMALL

REFERENCE_DIR = Path(__file__).resolve().parent / "assets" / "reference"

#: A fixture with both hands busy, and a moment just after notes have landed so
#: the effects that fade from an onset have something to fade from.
FIXTURE = "two-hands"
WHEN = 1.02

#: Big enough for a glow to have room, small enough to commit.
EFFECT_SIZE = VisualConfig(width=320, height=180, fps=10, lookahead_s=3.0)

EFFECT_REFERENCE_CASES = [(kind,) for kind in sorted(KINDS)]


def with_effects(*effects: EffectConfig, base: VisualConfig | None = None):
    config = replace(base or EFFECT_SIZE, effects=effects)
    config.validate()
    return config


def one(kind: str, intensity: float = 1.0) -> EffectConfig:
    return EffectConfig(kind=kind, intensity=intensity)


def score():
    return read_midi(FIXTURES[FIXTURE]())


def drawn(config: VisualConfig, time: float = WHEN) -> np.ndarray:
    return render_frame(score(), config, time, pedal_lanes=NO_LANES)


# -- off means off -------------------------------------------------------


@pytest.mark.feature("F-75")
def test_the_shipped_default_has_no_effects() -> None:
    assert Config().visual.effects == ()


@pytest.mark.feature("F-75")
def test_an_empty_effects_list_is_literally_unchanged() -> None:
    """Not visually similar. A practice render must be the same pixels it was
    before any of this existed."""
    assert np.array_equal(drawn(EFFECT_SIZE), drawn(with_effects()))


@pytest.mark.feature("F-76")
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_effect_at_zero_intensity_is_a_no_op(kind: str) -> None:
    """The property that makes the slider trustworthy. "Right idea, too strong"
    has to be a number, never a rewrite."""
    assert np.array_equal(drawn(EFFECT_SIZE), drawn(with_effects(one(kind, 0.0))))


@pytest.mark.feature("F-75")
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_effect_draws_something_at_full_intensity(kind: str) -> None:
    plain = drawn(EFFECT_SIZE)
    lit = drawn(with_effects(one(kind, 1.0)))
    changed = int(np.count_nonzero(np.any(plain != lit, axis=2)))
    assert changed > 0, f"{kind} changed nothing"


# -- composition ---------------------------------------------------------


@pytest.mark.feature("F-75")
def test_two_effects_that_only_add_light_commute() -> None:
    """Not the behaviour the plan predicted, and worth pinning because it is
    the reason the order usually does not matter.

    Every effect but bloom only adds light, and addition commutes. A halo under
    particles really is the same picture as particles under a halo, right up to
    the point where something saturates.
    """
    first = drawn(with_effects(one("halo"), one("particles")))
    second = drawn(with_effects(one("particles"), one("halo")))
    assert np.array_equal(first, second)


@pytest.mark.feature("F-75")
def test_the_order_matters_once_an_effect_reads_the_frame() -> None:
    """Bloom is the one that does. It picks up the brightest pixels, so whether
    a glow was drawn before or after it is a different picture. That is what
    makes the effects a list rather than a set."""
    before = drawn(with_effects(one("key_glow"), one("bloom")))
    after = drawn(with_effects(one("bloom"), one("key_glow")))
    assert not np.array_equal(before, after)


@pytest.mark.feature("F-75")
def test_effects_compose_rather_than_replace_each_other() -> None:
    plain = drawn(EFFECT_SIZE)
    flash = drawn(with_effects(one("strike_flash")))
    both = drawn(with_effects(one("strike_flash"), one("key_glow")))

    assert not np.array_equal(both, flash), "the second effect drew nothing"
    lit = np.any(both != plain, axis=2)
    assert lit.sum() > int(np.any(flash != plain, axis=2).sum())


# -- determinism, and the seam it protects -------------------------------


@pytest.mark.feature("F-77")
def test_the_same_frame_twice_is_byte_identical() -> None:
    """Particles included. Their randomness comes from hashing the note and the
    spark index, so there is nothing to differ between two runs."""
    config = with_effects(*(one(kind) for kind in sorted(KINDS)))
    assert np.array_equal(drawn(config), drawn(config))


def _render_in_this_process(payload: tuple[VisualConfig, float]) -> bytes:
    """Draw a frame and hand back its bytes. Runs in a spawned worker."""
    config, time = payload
    return render_frame(
        read_midi(FIXTURES[FIXTURE]()), config, time, pedal_lanes=NO_LANES
    ).tobytes()


@pytest.mark.feature("F-77")
def test_a_worker_process_draws_the_same_pixels() -> None:
    """The reason effects may not carry state between frames.

    The renderer cuts the timeline into spans and gives each to its own
    process. An effect seeded from anything process-local would come out
    different across a span boundary, and the seam would be visible every few
    seconds.
    """
    config = with_effects(*(one(kind) for kind in sorted(KINDS)))
    here = drawn(config).tobytes()

    context = multiprocessing.get_context("spawn")
    with context.Pool(1) as pool:
        there = pool.apply(_render_in_this_process, ((config, WHEN),))

    assert here == there


# -- pulse ---------------------------------------------------------------


@pytest.mark.feature("F-75")
def test_the_pulse_lifts_the_background_and_nothing_else() -> None:
    """The only effect that does not draw. It changes the colour the background
    is about to be filled with, which is why it costs nothing."""
    config = with_effects(one("pulse", 1.0))
    assert background_for(config, score(), WHEN) != config.background


@pytest.mark.feature("F-75")
def test_the_pulse_fades_back_to_the_configured_background() -> None:
    quiet = 500.0  # long past the end of the fixture
    config = with_effects(one("pulse", 1.0))
    assert background_for(config, score(), quiet) == config.background
    assert pulse_lift(score(), quiet, 1.0) == 0.0


@pytest.mark.feature("F-75")
def test_a_louder_note_pulses_harder() -> None:
    """Driven by what was played, not by the tempo map. Pulsing on the beat is
    a metronome you can see: it fires whether or not anything was played."""
    from psv.model import Note, Part, Score

    def at(velocity: int) -> float:
        one_note = Score(
            parts=(
                Part(notes=(Note(pitch=60, start=1.0, end=2.0, velocity=velocity),)),
            )
        )
        return pulse_lift(one_note, 1.05, 1.0)

    assert at(110) > at(40) > 0.0


# -- config --------------------------------------------------------------


@pytest.mark.feature("F-75")
def test_effects_load_from_an_array_of_tables(tmp_path: Path) -> None:
    path = tmp_path / "psv.toml"
    path.write_text(
        "[[visual.effects]]\n"
        'kind = "key_glow"\n'
        "intensity = 0.4\n"
        "\n"
        "[[visual.effects]]\n"
        'kind = "strike_flash"\n',
        encoding="utf-8",
    )
    effects = Config.load(path).visual.effects
    assert [(e.kind, e.intensity) for e in effects] == [
        ("key_glow", 0.4),
        ("strike_flash", 0.6),
    ]


@pytest.mark.feature("F-75")
@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('[[visual.effects]]\nkind = "sparkle"\n', "unknown visual effect"),
        ('[[visual.effects]]\nkind = "halo"\nintensity = 2\n', "between 0 and 1"),
        ('[[visual.effects]]\nkind = "halo"\nglow = 3\n', "unknown key"),
        ("[visual]\neffects = 3\n", "list of tables"),
        ("[visual]\neffects = [3]\n", "must be a table"),
    ],
)
def test_a_broken_effects_list_says_what_is_wrong(
    tmp_path: Path, body: str, fragment: str
) -> None:
    path = tmp_path / "psv.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=fragment):
        Config.load(path)


# -- the named sets ------------------------------------------------------


@pytest.mark.feature("F-78")
@pytest.mark.parametrize("name", sorted(EFFECT_SETS))
def test_every_effect_set_applies_and_validates(name: str) -> None:
    applied = apply_effect_set(Config(), name)
    applied.validate()
    assert tuple(applied.visual.effects) == EFFECT_SETS[name]


@pytest.mark.feature("F-78")
def test_every_effect_set_is_described() -> None:
    assert set(EFFECT_SETS) == set(EFFECT_DESCRIPTIONS)


@pytest.mark.feature("F-78")
def test_asking_for_none_turns_off_what_a_config_file_asked_for() -> None:
    """A flag beats the file, and that has to include turning something off."""
    loaded = replace(Config(), visual=with_effects(one("halo")))
    assert apply_effect_set(loaded, "none").visual.effects == ()


@pytest.mark.feature("F-78")
def test_no_set_includes_bloom() -> None:
    """It costs about three times a whole frame. Something that expensive is
    asked for by name, never handed over in a bundle."""
    for name, effects in EFFECT_SETS.items():
        assert all(effect.kind != "bloom" for effect in effects), name


@pytest.mark.feature("F-78")
def test_an_unknown_effect_set_names_the_real_ones() -> None:
    with pytest.raises(ConfigError, match="unknown effect set"):
        apply_effect_set(Config(), "fireworks")


# -- reference images ----------------------------------------------------


@pytest.mark.feature("F-75")
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_effect_frames_match_their_committed_reference(kind: str) -> None:
    """Pins each effect's drawing. Regenerate with
    `python scripts/make_references.py` once a change is deliberate."""
    from PIL import Image

    path = REFERENCE_DIR / f"effect-{kind}.png"
    assert path.exists(), f"missing reference {path.name}; run make_references.py"

    rendered = drawn(with_effects(one(kind, 1.0)))
    expected = np.array(Image.open(path).convert("RGB"))

    assert rendered.shape == expected.shape
    differing = int(np.count_nonzero(np.any(rendered != expected, axis=2)))
    assert differing == 0, f"{differing} pixels differ from {path.name}"


@pytest.mark.feature("F-75")
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_reference_actually_shows_its_effect(kind: str) -> None:
    """A reference identical to the plain frame pins nothing. This is what stops
    a broken effect being committed as its own expected output."""
    from PIL import Image

    path = REFERENCE_DIR / f"effect-{kind}.png"
    reference = np.array(Image.open(path).convert("RGB"))
    assert not np.array_equal(reference, drawn(EFFECT_SIZE))


def test_every_effect_that_draws_is_registered() -> None:
    """`KINDS` is what the config validates against, so an effect missing from
    it would be unreachable and one missing from `PAINTERS` would silently do
    nothing."""
    assert set(PAINTERS) | {"pulse"} == set(KINDS)


def test_the_small_reference_config_is_valid() -> None:
    EFFECT_SIZE.validate()
    assert EFFECT_SIZE.width == SMALL.width
