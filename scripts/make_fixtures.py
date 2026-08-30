#!/usr/bin/env python
"""Write every synthetic fixture to tests/assets/generated/ for inspection.

Nothing in the test suite reads these files. They exist so you can open a
fixture in a DAW or MIDI editor and see what it actually contains. The builders
in tests/fixtures/midi_builder.py are the source of truth, which is why the
output directory is gitignored.

    python scripts/make_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.fixtures.midi_builder import FIXTURES  # noqa: E402

OUT_DIR = REPO_ROOT / "tests" / "assets" / "generated"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in sorted(FIXTURES.items()):
        midi = build()
        path = OUT_DIR / f"{name}.mid"
        midi.save(path)
        notes = sum(
            1
            for track in midi.tracks
            for msg in track
            if msg.type == "note_on" and msg.velocity > 0
        )
        print(f"{name:24} {len(midi.tracks)} track(s)  {notes:4} notes  -> {path.name}")
    print(f"\n{len(FIXTURES)} fixtures written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
