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

import itertools
import multiprocessing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from psv.config import Config, ConfigError, EffectConfig, VisualConfig
from psv.midi import read_midi
from psv.model import Note, Part, Score
from psv.presets import (
    EFFECT_DESCRIPTIONS,
    EFFECT_SETS,
    apply_effect_set,
)
from psv.render.effects import KINDS, PAINTERS, Canvas, background_for, pulse_lift
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


def with_effects(
    *effects: EffectConfig, base: VisualConfig | None = None
) -> VisualConfig:
    config = replace(base or EFFECT_SIZE, effects=effects)
    config.validate()
    return config


def one(kind: str, intensity: float = 1.0) -> EffectConfig:
    return EffectConfig(kind=kind, intensity=intensity)


def score() -> Score:
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


# -- bloom stays above the strike line -----------------------------------


@pytest.mark.feature("F-83")
def test_bloom_leaves_the_keyboard_alone() -> None:
    """The white keys are the brightest thing on screen by a wide margin.

    A bloom over the whole frame is therefore mostly a bloom of the keyboard,
    which washes the picture and glows the one part of it that is not music.
    """
    from psv.render.frame import Layout

    plain = drawn(EFFECT_SIZE)
    bloomed = drawn(with_effects(one("bloom", 1.0)))
    line = Layout.from_config(EFFECT_SIZE, NO_LANES).keyboard_top

    assert np.array_equal(plain[line:], bloomed[line:]), "the keyboard was bloomed"
    assert not np.array_equal(plain[:line], bloomed[:line]), "nothing was bloomed"


def bloom_canvas(config: VisualConfig) -> Canvas:
    """A Canvas for calling an effect directly, without drawing a frame first."""
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    layout = Layout.from_config(config, NO_LANES)
    return Canvas(
        score=Score(),
        config=config,
        layout=layout,
        geometry=KeyboardGeometry(
            layout.keyboard_width, config.height - layout.keyboard_top
        ),
        time=0.0,
    )


@pytest.mark.feature("F-83")
def test_bloom_has_no_step_at_the_floor() -> None:
    """A soft knee, so a bar does not pop as it crosses the threshold.

    Called on a made-up frame rather than a rendered one: a bar's brightness on
    screen is its colour times its velocity times the theme, so driving the
    luma through a render cannot put a patch exactly either side of the floor.

    With the hard threshold this replaced, one grey level either side of the
    floor was the difference between no glow and the whole glow.
    """
    from psv.render.effects import BLOOM_FLOOR, bloom

    canvas = bloom_canvas(EFFECT_SIZE)
    height, width = EFFECT_SIZE.height, EFFECT_SIZE.width

    def added(luma: int) -> float:
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[20:60, 100:200] = luma
        before = frame.copy()
        bloom(frame, canvas, 1.0)
        return float(np.abs(frame.astype(float) - before.astype(float)).sum())

    floor = int(BLOOM_FLOOR)
    assert added(floor - 1) == 0.0, "below the floor is still nothing"
    just_over = added(floor + 2)
    well_over = added(floor + 60)
    assert just_over > 0.0, "the knee never starts"
    assert just_over < 0.15 * well_over, (
        f"crossing the floor is still a step: {just_over} vs {well_over}"
    )


# -- the halo follows the bar's corners -----------------------------------


@pytest.mark.feature("F-84")
def test_a_square_bar_still_gets_a_square_halo() -> None:
    """The inset is driven by `note_radius`, so at 0 nothing changes."""
    square = replace(EFFECT_SIZE, note_radius=0.0)
    assert np.array_equal(
        drawn(with_effects(one("halo", 1.0), base=square)),
        drawn(with_effects(one("halo", 1.0), base=square)),
    )
    # And the corner of the ring is lit, which is what "square" means here.
    lit = drawn(with_effects(one("halo", 1.0), base=square))
    plain = drawn(square)
    assert not np.array_equal(lit, plain)


@pytest.mark.feature("F-84")
def test_the_halo_does_not_fill_in_a_rounded_corner() -> None:
    """A rectangle of light around a rounded bar makes it read as square again.

    The bar's own corner is dark, so a lit corner behind it redraws the
    rectangle the rounding just removed, in glow instead of in the bar's
    colour. Measured just outside the bar: at the corner, and level with the
    middle of the same edge.
    """
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    pitch = 60
    white = replace(EFFECT_SIZE.colors, left_hand="#ffffff", right_hand="#ffffff")
    # No grid: the bar is found by scanning a column for anything that is not
    # the background, and a beat line is not the background either.
    base = replace(
        EFFECT_SIZE,
        width=640,
        height=480,
        note_radius=0.5,
        colors=white,
        grid=replace(EFFECT_SIZE.grid, pitch_lines="none", beat_lines="none"),
    )
    notes = Score(parts=(Part(notes=(Note(pitch=pitch, start=1.0, end=1.9),)),))
    layout = Layout.from_config(base, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, base.height - layout.keyboard_top
    )
    left, right = geometry.bar_span(pitch)
    edge = round(left)
    inset = round((right - left) * base.note_radius)

    plain = render_frame(notes, base, 1.3, pedal_lanes=NO_LANES)
    haloed = render_frame(
        notes, with_effects(one("halo", 1.0), base=base), 1.3, pedal_lanes=NO_LANES
    )
    added = np.abs(haloed.astype(int) - plain.astype(int)).sum(axis=2)

    # The bar's own rows, found down its middle and stopping at the keyboard.
    column = plain[: layout.keyboard_top, (edge + round(right)) // 2]
    rows = np.where(np.any(column != column[0], axis=-1))[0]
    top = int(rows.min())
    middle = (top + int(rows.max())) // 2

    at_corner = added[top - inset : top, edge - inset : edge].sum()
    at_edge = added[middle - inset : middle, edge - inset : edge].sum()

    assert at_edge > 0, "the halo drew nothing beside the bar at all"
    assert at_corner < 0.2 * at_edge, (
        f"the corner is lit like the straight edge: {at_corner} vs {at_edge}"
    )


@pytest.mark.feature("F-83")
def test_the_bloom_upscale_interpolates_rather_than_repeating_pixels() -> None:
    """The blocks Ren saw twice, tested where they came from.

    The committed bloom reference is 320x180, where the falling area is small
    enough that the shrink is 1 and the upscale never runs, so it could not
    have caught this. One bright cell stretched twenty-fold is unambiguous:
    repeating pixels gives a hard square of one value, interpolating gives a
    ramp.
    """
    from psv.render.effects import _upscale

    small = np.zeros((5, 5, 3), dtype=np.uint8)
    small[2, 2] = 200
    grown = _upscale(small, 100, 100)

    assert grown.shape == (100, 100, 3)
    row = grown[50, :, 0].astype(int)
    peak = int(np.argmax(row))
    assert row[peak] > 0, "the bright cell did not survive the stretch"

    # A repeat would put exactly two values on this row: 200 and 0.
    assert len(set(row.tolist())) > 5, f"only {len(set(row.tolist()))} values: a block"

    # And it falls away from the peak rather than stopping at an edge.
    rising = row[: peak + 1]
    assert all(a <= b for a, b in itertools.pairwise(rising)), (
        "the ramp up to the peak is not monotonic"
    )


# -- the key glow follows the key, not its bounding box -------------------


@pytest.mark.feature("F-85")
def test_a_white_key_glow_does_not_spill_onto_its_black_neighbours() -> None:
    """A white key is not a rectangle.

    For the length of the black keys it is only the tab between them. Lit at
    full width for that whole length it draws over the half of each neighbour
    sitting in front of it, and the black key looks like it is glowing too.
    """
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    white, black = 62, 61  # D4, and the C#4 to its left
    base = replace(
        EFFECT_SIZE,
        width=1280,
        height=720,
        grid=replace(EFFECT_SIZE.grid, pitch_lines="none", beat_lines="none"),
    )
    notes = Score(parts=(Part(notes=(Note(pitch=white, start=0.5, end=3.0),)),))
    layout = Layout.from_config(base, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, base.height - layout.keyboard_top
    )

    plain = render_frame(notes, base, 1.5, pedal_lanes=NO_LANES)
    lit = render_frame(
        notes, with_effects(one("key_glow", 1.0), base=base), 1.5, pedal_lanes=NO_LANES
    )

    # Down the middle of the black key, over the length that black keys have.
    column = round(geometry.key_centre(black))
    rows = np.s_[layout.keyboard_top : layout.keyboard_top + int(geometry.black_height)]
    assert np.array_equal(plain[rows, column], lit[rows, column]), (
        "the glow reached the black key beside it"
    )

    # And it did light the white key it belongs to.
    own = round(geometry.key_centre(white))
    assert not np.array_equal(plain[rows, own], lit[rows, own]), "nothing was lit"


@pytest.mark.feature("F-85")
def test_a_black_key_is_a_rectangle_and_keeps_its_full_width() -> None:
    """The narrowing is a white-key fact. A black key has no neighbours in
    front of it, so its span is the same at every depth."""
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    base = replace(EFFECT_SIZE, width=1280, height=720)
    layout = Layout.from_config(base, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, base.height - layout.keyboard_top
    )
    for pitch in (61, 63, 66):
        assert geometry.visible_span(pitch, 0.0) == geometry.key_span(pitch)

    # Below the black keys, a white key is its full width again.
    assert geometry.visible_span(62, geometry.black_height) == geometry.key_span(62)
    assert geometry.visible_span(62, 0.0) != geometry.key_span(62)


@pytest.mark.feature("F-85")
@pytest.mark.parametrize("kind", ["trail", "strike_flash", "halo", "key_glow"])
def test_nothing_drawn_on_a_struck_key_reaches_its_black_neighbours(
    kind: str,
) -> None:
    """The keyboard is drawn whites first so blacks sit on top of them.

    Effects run after the keyboard, which puts them on top of everything, so
    each one that reaches down onto the keys has to do that occluding itself.
    Sampled just after the strike, where the trail and the flash are alive.
    """
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    white = 62  # D4, a black key either side
    base = replace(
        EFFECT_SIZE,
        width=1280,
        height=720,
        grid=replace(EFFECT_SIZE.grid, pitch_lines="none", beat_lines="none"),
    )
    notes = Score(parts=(Part(notes=(Note(pitch=white, start=1.0, end=3.0),)),))
    layout = Layout.from_config(base, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, base.height - layout.keyboard_top
    )
    rows = np.s_[
        layout.keyboard_top + 1 : layout.keyboard_top + 1 + int(geometry.black_height)
    ]

    lit_own = 0
    for when in (1.02, 1.15, 1.30):
        plain = render_frame(notes, base, when, pedal_lanes=NO_LANES)
        drawn_now = render_frame(
            notes, with_effects(one(kind, 1.0), base=base), when, pedal_lanes=NO_LANES
        )
        for black in (61, 63):
            left, right = geometry.key_span(black)
            columns = np.s_[round(left) + 1 : round(right) - 1]
            spill = np.abs(
                drawn_now[rows, columns].astype(int) - plain[rows, columns].astype(int)
            ).sum()
            assert spill == 0, f"{kind} spilled onto the black key at {when}s"

        left, right = geometry.key_span(white)
        own = np.s_[round(left) + 2 : round(right) - 2]
        lit_own += int(
            np.abs(
                drawn_now[rows, own].astype(int) - plain[rows, own].astype(int)
            ).sum()
        )

    assert lit_own > 0, f"{kind} lit nothing on the key it belongs to"


@pytest.mark.feature("F-85")
def test_a_struck_black_key_is_still_lit_by_its_own_effects() -> None:
    """A key never occludes itself. The light belongs on it."""
    from psv.render.frame import Layout
    from psv.render.geometry import KeyboardGeometry

    black = 61
    base = replace(
        EFFECT_SIZE,
        width=1280,
        height=720,
        grid=replace(EFFECT_SIZE.grid, pitch_lines="none", beat_lines="none"),
    )
    notes = Score(parts=(Part(notes=(Note(pitch=black, start=1.0, end=3.0),)),))
    layout = Layout.from_config(base, NO_LANES)
    geometry = KeyboardGeometry(
        layout.keyboard_width, base.height - layout.keyboard_top
    )
    left, right = geometry.key_span(black)
    patch = np.s_[
        layout.keyboard_top + 1 : layout.keyboard_top + 1 + int(geometry.black_height),
        round(left) + 2 : round(right) - 2,
    ]

    plain = render_frame(notes, base, 1.05, pedal_lanes=NO_LANES)
    drawn_now = render_frame(
        notes, with_effects(one("trail", 1.0), base=base), 1.05, pedal_lanes=NO_LANES
    )
    assert np.abs(drawn_now[patch].astype(int) - plain[patch].astype(int)).sum() > 0, (
        "the black key occluded its own trail"
    )


# -- the occlusion must never grow what it is given ----------------------


@pytest.mark.feature("F-85")
@given(
    y0=st.floats(min_value=-50, max_value=1200),
    height=st.floats(min_value=0.0, max_value=400),
    x0=st.floats(min_value=0, max_value=1200),
    width=st.floats(min_value=0.0, max_value=120),
)
@settings(max_examples=300, deadline=None)
def test_occlusion_never_reaches_outside_the_rectangle_it_was_given(
    y0: float, height: float, x0: float, width: float
) -> None:
    """The invariant that matters, and the one that broke.

    Splitting a rectangle around the black keys clamped the first piece to the
    keyboard line without also clamping it to the rectangle's own bottom. Every
    halo's lower edge sits above the keys while its bar is still falling, so
    each one was stretched from a five-pixel strip into a line running all the
    way down to the keyboard: a bright vertical rule beside every falling note.
    """
    from psv.render.effects import _behind_keys

    canvas = bloom_canvas(replace(EFFECT_SIZE, width=1280, height=720))
    x1, y1 = x0 + width, y0 + height

    for left, top, right, bottom in _behind_keys(canvas, 62, x0, y0, x1, y1):
        assert top >= y0, f"piece starts above the rectangle: {top} < {y0}"
        assert bottom <= y1, f"piece ends below the rectangle: {bottom} > {y1}"
        assert left >= x0, f"piece starts left of the rectangle: {left} < {x0}"
        assert right <= x1, f"piece ends right of the rectangle: {right} > {x1}"


@pytest.mark.feature("F-85")
def test_a_rectangle_clear_of_the_keyboard_is_not_cut_at_all() -> None:
    """Nothing above the keys can be occluded by a key, so nothing changes."""
    from psv.render.effects import _behind_keys

    base = replace(EFFECT_SIZE, width=1280, height=720)
    canvas = bloom_canvas(base)
    rect = (600.0, 100.0, 640.0, 160.0)
    pieces = list(_behind_keys(canvas, 62, *rect))
    assert pieces == [(600.0, 100.0, 640.0, 160.0)]


@pytest.mark.feature("F-85")
def test_no_falling_bar_draws_a_rule_down_to_the_keyboard() -> None:
    """The artefact itself, at the size it was seen.

    A column beside a bar was brighter than its neighbours over most of the
    frame's height. Nothing an effect draws around a falling bar should be
    taller than the bar.
    """
    base = replace(
        EFFECT_SIZE,
        width=1280,
        height=720,
        grid=replace(EFFECT_SIZE.grid, pitch_lines="none", beat_lines="none"),
    )
    # A run of short notes on one pitch, which is what made the rules join up.
    notes = tuple(
        Note(pitch=62, start=1.0 + i * 0.12, end=1.06 + i * 0.12) for i in range(12)
    )
    score = Score(parts=(Part(notes=notes),))
    from psv.render.frame import Layout

    layout = Layout.from_config(base, NO_LANES)

    plain = render_frame(score, base, 1.5, pedal_lanes=NO_LANES)
    haloed = render_frame(
        score, with_effects(one("halo", 1.0), base=base), 1.5, pedal_lanes=NO_LANES
    )
    added = np.abs(
        haloed[: layout.keyboard_top].astype(int)
        - plain[: layout.keyboard_top].astype(int)
    ).sum(axis=2)

    tallest = int((added > 0).sum(axis=0).max())
    assert tallest < layout.keyboard_top * 0.6, (
        f"a column is lit over {tallest} of {layout.keyboard_top} rows"
    )
