"""Named bundles of settings that go together.

Getting a sensible result should not require assembling a TOML file first. Each
preset is a small overlay applied on top of whatever config was loaded, so a
preset plus a config file plus a flag all compose, in that order of increasing
specificity.

Deliberately few, and each one earns its place by answering a question someone
actually asks: my hands are small, I am starting out, I want to see what the
piece really is, I want to iterate quickly.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from psv.config import Config, ConfigError, EffectConfig

#: Overlays, keyed by preset name. Each maps a config section to the fields it
#: overrides. Only fields named here move; everything else stays as loaded.
PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "small-hands": {
        "hands": {"max_span_semitones": 9},
    },
    "beginner": {
        "hands": {"max_span_semitones": 9},
        "difficulty": {"level": "beginner"},
        "practice": {"tempo": 0.7, "count_in_bars": 2},
        "visual": {"note_border": 0.0022},
    },
    # Arrangement still reduces a multi-instrument score to two hands, because
    # two hands is the whole premise. This turns off the two things that are
    # optional: the span limit and difficulty thinning.
    "as-written": {
        "hands": {"max_span_semitones": 0},
        "difficulty": {"level": "original"},
    },
    "draft": {
        "visual": {"width": 640, "height": 360, "fps": 24},
        "audio": {"backend": "none"},
    },
}

#: Colour schemes, kept apart from the presets above because they answer a
#: different question. A preset changes what the video *is*: how wide a reach,
#: how thin the texture, how fast. A theme only changes how it looks, so the
#: two compose and neither has to know about the other.
#:
#: Every one of these leaves hue carrying which hand and brightness carrying how
#: loud. That is the readability rule, and a theme may not spend it.
THEMES: dict[str, dict[str, Any]] = {
    "midnight": {
        "gradient_top": "#0b1026",
        "gradient_bottom": "#1b2350",
        "note_border_shade": 0.35,
        "bar_gradient": 0.5,
        "colors": {"left_hand": "#4f9dff", "right_hand": "#ffcf5c"},
    },
    "ember": {
        "gradient_top": "#160b0b",
        "gradient_bottom": "#4a1524",
        "note_border_shade": 0.25,
        "bar_gradient": -0.4,
        "colors": {"left_hand": "#ff9b3d", "right_hand": "#3fd0c9"},
    },
    "neon": {
        "gradient_top": "#07040f",
        "gradient_bottom": "#2a0a45",
        "note_border_shade": 0.7,
        "bar_gradient": 0.6,
        "colors": {"left_hand": "#ff3ea5", "right_hand": "#31e7ff"},
    },
    "aurora": {
        "gradient_top": "#04121a",
        "gradient_bottom": "#0d3a34",
        "note_border_shade": 0.2,
        "bar_gradient": 0.35,
        "colors": {"left_hand": "#7c6cff", "right_hand": "#4fe08a"},
    },
}


def _effects(*pairs: tuple[str, float]) -> tuple[EffectConfig, ...]:
    return tuple(EffectConfig(kind=kind, intensity=level) for kind, level in pairs)


#: Bundles of optional effects, a third axis alongside presets and themes. The
#: order inside each is the order they draw in, so a halo under particles is a
#: different picture from particles under a halo.
#:
#: `bloom` is in none of them. It costs about three times a whole frame and it
#: blows out the white keys, so it is available by name and never by default.
EFFECT_SETS: dict[str, tuple[EffectConfig, ...]] = {
    "none": (),
    "subtle": _effects(("key_glow", 0.4), ("strike_flash", 0.5)),
    "showcase": _effects(("key_glow", 0.6), ("strike_flash", 0.8), ("particles", 0.6)),
    "maximum": _effects(
        ("halo", 0.5),
        ("pulse", 0.5),
        ("key_glow", 0.7),
        ("trail", 0.6),
        ("strike_flash", 0.9),
        ("particles", 0.8),
    ),
}

#: One line each, for `--preset` in the help text and for `psv presets`.
DESCRIPTIONS: dict[str, str] = {
    "small-hands": "a 9-semitone reach instead of 12",
    "beginner": "small hands, thinner texture, 0.7x tempo, two bars of count-in",
    "as-written": "no span limit and nothing thinned for difficulty",
    "draft": "640x360 at 24fps with no audio, for iterating on visuals",
}

#: One line each, for `--effects` in the help text and for `psv presets`.
EFFECT_DESCRIPTIONS: dict[str, str] = {
    "none": "no effects, whatever the config file said",
    "subtle": "a glow on the pressed key and a flash as a note lands",
    "showcase": "subtle plus sparks off the strike line",
    "maximum": "everything except bloom, which you have to ask for by name",
}

#: One line each, for `--theme` in the help text and for `psv presets`.
THEME_DESCRIPTIONS: dict[str, str] = {
    "midnight": "deep blue, lit edges, blue against amber",
    "ember": "warm dark red, amber against teal",
    "neon": "violet and hard white edges, the loudest one",
    "aurora": "deep teal, violet against green",
}


def apply_preset(config: Config, name: str) -> Config:
    """Return ``config`` with ``name``'s overlay applied.

    Precedence runs from least to most specific: the config file, then the
    preset, then the individual flags. A preset is itself a flag, so asking for
    one on this run beats what the file said, and `--span 14 --preset
    small-hands` still gives 14.
    """
    overlay = PRESETS.get(name)
    if overlay is None:
        raise ConfigError(
            f"unknown preset {name!r}. Available: {', '.join(sorted(PRESETS))}"
        )

    updated = config
    for section, fields in overlay.items():
        updated = replace(
            updated, **{section: replace(getattr(updated, section), **fields)}
        )
    updated.validate()
    return updated


def apply_effect_set(config: Config, name: str) -> Config:
    """Return ``config`` with ``name``'s bundle of effects applied.

    Replaces whatever effects were configured rather than adding to them, so
    `--effects none` is a way to turn a config file's effects off for one run.
    """
    if name not in EFFECT_SETS:
        raise ConfigError(
            f"unknown effect set {name!r}. Available: {', '.join(sorted(EFFECT_SETS))}"
        )
    updated = replace(config, visual=replace(config.visual, effects=EFFECT_SETS[name]))
    updated.validate()
    return updated


def apply_theme(config: Config, name: str) -> Config:
    """Return ``config`` with ``name``'s colour scheme applied.

    A theme sits between the preset and the flags: more specific than a bundle
    that also changes how the piece is played, less specific than something you
    typed for this run.
    """
    theme = THEMES.get(name)
    if theme is None:
        raise ConfigError(
            f"unknown theme {name!r}. Available: {', '.join(sorted(THEMES))}"
        )

    overrides = dict(theme)
    colours = overrides.pop("colors", None)
    visual = replace(config.visual, **overrides)
    if colours is not None:
        visual = replace(visual, colors=replace(visual.colors, **colours))
    updated = replace(config, visual=visual)
    updated.validate()
    return updated
