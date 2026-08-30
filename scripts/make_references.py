#!/usr/bin/env python
"""Regenerate the committed reference frames used by the renderer tests.

Run this only when a rendering change is deliberate, then look at the images
before committing them. Regenerating to make a failing test pass is how a
reference suite stops being worth anything.

    python scripts/make_references.py            # rewrite the references
    python scripts/make_references.py --check    # report drift, change nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.fixtures.midi_builder import FIXTURES  # noqa: E402
from tests.test_render_frame import REFERENCE_CASES, SMALL  # noqa: E402

from psv.midi import read_midi  # noqa: E402
from psv.render.frame import render_frame  # noqa: E402

OUT_DIR = REPO_ROOT / "tests" / "assets" / "reference"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report drift without writing"
    )
    args = parser.parse_args(argv)

    from PIL import Image

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    drifted = 0

    for fixture, time in REFERENCE_CASES:
        path = OUT_DIR / f"{fixture}-{time:g}s.png"
        rendered = render_frame(read_midi(FIXTURES[fixture]()), SMALL, time)

        if path.exists():
            expected = np.array(Image.open(path).convert("RGB"))
            same = rendered.shape == expected.shape and np.array_equal(
                rendered, expected
            )
        else:
            same = False

        if same:
            print(f"  {path.name:32} unchanged")
            continue

        drifted += 1
        if args.check:
            print(f"! {path.name:32} DIFFERS")
        else:
            Image.fromarray(rendered).save(path)
            print(f"* {path.name:32} written")

    if args.check and drifted:
        print(f"\n{drifted} reference(s) out of date.", file=sys.stderr)
        print("Look at the change, then: python scripts/make_references.py")
        return 1

    print(f"\n{len(REFERENCE_CASES)} reference frame(s) in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
