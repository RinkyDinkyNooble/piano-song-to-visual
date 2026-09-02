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

from psv.config import Config, ConfigError

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
    "as-written": {
        "hands": {"max_span_semitones": 0},
        "difficulty": {"level": "original"},
    },
    "draft": {
        "visual": {"width": 640, "height": 360, "fps": 24},
        "audio": {"backend": "none"},
    },
}

#: One line each, for `--preset` in the help text and for `psv presets`.
DESCRIPTIONS: dict[str, str] = {
    "small-hands": "a 9-semitone reach instead of 12",
    "beginner": "small hands, thinner texture, 0.7x tempo, two bars of count-in",
    "as-written": "no span limit and no reduction: the piece as it really is",
    "draft": "640x360 at 24fps with no audio, for iterating on visuals",
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
