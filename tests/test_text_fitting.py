"""Text that fits the space there is for it.

Two halves. The arithmetic is tested against a made-up `measure`, where a
character is a fixed number of units wide, so the cases are exact and no font
is involved. The card is tested by measuring the ink it actually drew.

**Why no reference image here**, which is what this project usually reaches
for. The card is set in whatever serif face the machine has: Georgia on
Windows, DejaVu on a Linux runner, the Pillow built-in where there is neither.
A committed reference would be a picture of one of those, and the test would
fail everywhere else for a reason that has nothing to do with the fitting. What
the fault actually was — ink past the edge of the frame — is a number, and the
detector below reads it directly.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from psv import fit_text
from psv.config import TitleConfig
from psv.render.text import FLOOR_SHARE, MIN_SIZE
from psv.render.title import TEXT_WIDTH_SHARE, TITLE_SIZE, Card, build_card

#: A character is this many units wide per point of type size. Made up, exact,
#: and monotonic in the size, which is all the search needs.
UNIT = 0.6


def measure(text: str, size: int) -> float:
    return len(text) * size * UNIT


# -- the arithmetic ------------------------------------------------------


@pytest.mark.feature("F-93")
def test_text_that_already_fits_is_left_alone() -> None:
    """The common case costs nothing and changes nothing."""
    fitted = fit_text("Nocturne", width=1000, size=40, measure=measure)
    assert fitted.lines == ("Nocturne",)
    assert fitted.size == 40
    assert fitted.fits


@pytest.mark.feature("F-93")
def test_a_long_line_is_shrunk_until_it_fits() -> None:
    """Shrink before wrapping: a smaller title still reads as a title."""
    text = "Sonata No. 14 in C-sharp minor"
    fitted = fit_text(text, width=400, size=40, measure=measure)

    assert fitted.lines == (text,), "one line, just smaller"
    assert fitted.size < 40
    assert measure(text, fitted.size) <= 400


@pytest.mark.feature("F-93")
def test_the_size_chosen_is_the_largest_that_fits() -> None:
    """Shrinking further than it has to is its own kind of wrong.

    `floor` is opened right up, because the point here is the search landing on
    the boundary rather than where the default floor stops it.
    """
    text = "Prelude"
    fitted = fit_text(text, width=100, size=60, measure=measure, floor=MIN_SIZE)

    assert measure(text, fitted.size) <= 100
    assert measure(text, fitted.size + 1) > 100


@pytest.mark.feature("F-93")
def test_wrapping_happens_only_below_the_legible_floor() -> None:
    """Two lines is a different-looking card, so it is the second answer."""
    text = "Fantasia and Fugue in G minor"
    floor = round(40 * FLOOR_SHARE)

    # Wide enough for one line at the floor size: stays one line.
    just_enough = measure(text, floor)
    assert len(fit_text(text, width=just_enough, size=40, measure=measure).lines) == 1

    # A hair narrower than that, and it wraps rather than shrinking further.
    fitted = fit_text(text, width=just_enough - 1, size=40, measure=measure)
    assert len(fitted.lines) == 2
    assert fitted.fits


@pytest.mark.feature("F-93")
def test_wrapping_only_breaks_at_spaces() -> None:
    """A word split down the middle needs hyphenation to not look wrong."""
    text = "Wanderer Fantasy"
    fitted = fit_text(text, width=60, size=40, measure=measure)
    assert " ".join(fitted.lines) == text
    for line in fitted.lines:
        assert not line.startswith(" ") and not line.endswith(" ")


@pytest.mark.feature("F-93")
def test_one_very_long_word_is_returned_rather_than_broken() -> None:
    """Nothing to wrap at, so it comes back too wide and says so.

    Reported rather than raised: the caller is part way through drawing a
    frame, and a slightly-too-wide title beats a render that stopped.
    """
    fitted = fit_text("Klavierstueck", width=10, size=40, measure=measure)
    assert fitted.lines == ("Klavierstueck",)
    assert not fitted.fits
    assert fitted.size >= MIN_SIZE


@pytest.mark.feature("F-93")
def test_nothing_to_set_is_no_lines() -> None:
    assert fit_text("", width=100, size=40, measure=measure).lines == ()
    assert fit_text("   ", width=100, size=40, measure=measure).lines == ()


@pytest.mark.feature("F-93")
def test_the_size_never_goes_below_the_minimum() -> None:
    """Type smaller than this is not small, it is absent."""
    fitted = fit_text("a b c d e f", width=1, size=12, measure=measure)
    assert fitted.size >= MIN_SIZE


@pytest.mark.feature("F-93")
@pytest.mark.parametrize(
    "text",
    [
        "Nocturne",
        "Sonata No. 14 in C-sharp minor Op. 27 No. 2",
        "Prelude and Fugue in E-flat major BWV 552 the St Anne",
        "A",
        "two words",
    ],
)
def test_the_result_is_always_the_same_words(text: str) -> None:
    """Fitting rearranges the text and never edits it."""
    fitted = fit_text(text, width=200, size=40, measure=measure)
    assert " ".join(fitted.lines).split() == text.split()


# -- the card, measured --------------------------------------------------


def ink_columns(path: Path, screen: tuple[int, int, int]) -> tuple[int, int]:
    """Leftmost and rightmost column with anything drawn on it.

    The card is a flat screen with type on it, so "anything that is not the
    screen colour" is the text, the rules, and nothing else.
    """
    pixels = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    lit = np.abs(pixels - np.array(screen, dtype=np.int16)).sum(axis=2) > 8
    columns = np.flatnonzero(lit.any(axis=0))
    assert columns.size, "nothing was drawn on the card at all"
    return int(columns[0]), int(columns[-1])


@pytest.mark.feature("F-93")
def test_a_title_long_enough_to_have_overflowed_stays_on_the_card(
    tmp_path: Path,
) -> None:
    """The detector for the actual fault, run on the actual drawing.

    This title at the card's unshrunk size is far wider than the frame. Before
    fitting existed it was drawn at that size anyway and ran off both edges,
    which is measurable as ink in column 0 and in the last column.
    """
    width, height = 960, 540
    config = TitleConfig(seconds=3.0)
    path = build_card(
        config,
        Card(
            title="Prelude and Fugue in E-flat major BWV 552 the St Anne",
            composer="Johann Sebastian Bach",
            footer="a channel with a long enough name to run over as well",
        ),
        width,
        height,
        tmp_path / "card.png",
    )

    first, last = ink_columns(path, (10, 10, 10))
    margin = width * (1 - TEXT_WIDTH_SHARE) / 2
    assert first >= 0
    assert last <= width - 1
    # And inside the margin, not merely inside the frame: text touching the
    # edge reads as cropped even when every pixel of it is present.
    assert first >= margin - 2, f"ink starts at column {first}, margin is {margin}"
    assert last <= width - margin + 2, f"ink ends at column {last}"


@pytest.mark.feature("F-93")
def test_a_short_title_is_still_drawn_at_full_size(tmp_path: Path) -> None:
    """Fitting must not shrink what already fitted.

    Measured as the height of the title's ink against the type size the card
    asked for. Capital letters occupy most of the em and no face puts them at
    half of it, so this separates "drawn at the size asked for" from "shrunk"
    without depending on which serif the machine has.
    """
    width, height = 960, 540
    path = build_card(
        TitleConfig(seconds=3.0),
        Card(title="Air", composer="", footer=""),
        width,
        height,
        tmp_path / "short.png",
    )
    pixels = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    lit = np.abs(pixels - np.array((10, 10, 10), dtype=np.int16)).sum(axis=2) > 8
    rows = int(lit.any(axis=1).sum())

    asked = round(height * TITLE_SIZE)
    assert rows >= asked * 0.5, f"'Air' drew {rows} rows for a {asked}pt title"


@pytest.mark.feature("F-93")
def test_a_wrapped_title_does_not_land_on_the_composer(tmp_path: Path) -> None:
    """Wrapping grows the block downward, and the line below has to move.

    Each block sits where its fraction of the frame height says, and those
    fractions were chosen for a one-line title. A title that needed two lines
    took room the composer was going to use, so without pushing the composer
    down the two sets of type touch.

    Measured as a gap of blank rows between the two, found by looking for rows
    with no ink in the band between them.
    """
    width, height = 1280, 720
    path = build_card(
        TitleConfig(seconds=4.0),
        Card(
            title="Prelude and Fugue in E-flat major BWV 552 the St Anne",
            composer="Johann Sebastian Bach",
            footer="",
        ),
        width,
        height,
        tmp_path / "wrapped.png",
    )
    pixels = np.array(Image.open(path).convert("RGB")).astype(np.int16)
    lit = np.abs(pixels - np.array((10, 10, 10), dtype=np.int16)).sum(axis=2) > 8
    rows = np.flatnonzero(lit.any(axis=1))
    assert rows.size, "nothing was drawn"

    # Three bands of type (two title lines and the composer) means at least two
    # runs of blank rows between the first row of ink and the last.
    inked = set(rows.tolist())
    gaps = 0
    previous_was_ink = True
    for row in range(int(rows[0]), int(rows[-1]) + 1):
        is_ink = row in inked
        if previous_was_ink and not is_ink:
            gaps += 1
        previous_was_ink = is_ink
    assert gaps >= 2, f"the title and composer run together: {gaps} blank runs"
