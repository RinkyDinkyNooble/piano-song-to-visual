"""Colour primitives.

These were written twice, once in `psv.config` to validate a colour and once in
`psv.render.color` to parse one. The point of the module is that there is now a
single answer to "what is a colour", so the tests here are mostly about the two
callers agreeing.
"""

from __future__ import annotations

import pytest

from psv.config import ConfigError, VisualConfig
from psv.rgb import is_grayscale, is_hex, parse_hex


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#000000", (0, 0, 0)),
        ("#ffffff", (255, 255, 255)),
        ("#4a90d9", (74, 144, 217)),
        ("#FFC266", (255, 194, 102)),
        ("#abc", (170, 187, 204)),
    ],
)
def test_parse_hex_reads_both_lengths_and_either_case(
    text: str, expected: tuple[int, int, int]
) -> None:
    assert parse_hex(text) == expected


@pytest.mark.parametrize(
    "text", ["#000000", "#abc", "#4a90d9", "#FFF", "#012345", "#AbCdEf"]
)
def test_everything_is_hex_accepts_can_also_be_parsed(text: str) -> None:
    """The property that matters: validation and parsing agree.

    Config validates a colour and the renderer parses it later, so a string the
    first accepts and the second cannot read would be a crash mid-render.
    """
    assert is_hex(text)
    assert parse_hex(text)  # must not raise


@pytest.mark.parametrize(
    "text", ["", "abc", "#", "#12", "#12345", "#1234567", "#gggggg", "4a90d9", "#ab cd"]
)
def test_is_hex_rejects_what_would_not_parse(text: str) -> None:
    assert not is_hex(text)


def test_is_grayscale_is_about_the_triple_not_the_string() -> None:
    assert is_grayscale((16, 16, 16))
    assert is_grayscale(parse_hex("#141414"))
    assert not is_grayscale(parse_hex("#141415"))


def test_the_background_rule_uses_the_same_parser_the_renderer_does() -> None:
    """A three-digit grey is grey, and has to be accepted as one."""
    VisualConfig(background="#333").validate()
    with pytest.raises(ConfigError, match="grayscale"):
        VisualConfig(background="#334").validate()
