"""Enforce that finished features are actually tested.

This is the check behind the promise that every feature is covered at least
once overall, rather than only having its internals unit tested.

Two rules:

1. Every feature marked ``status = "done"`` has at least one test carrying
   ``@pytest.mark.feature("F-xx")``. Flipping a feature to done without a test
   fails the suite.
2. Every feature id used in a marker exists in features.toml, so a typo cannot
   quietly satisfy rule 1.

It also keeps the registry itself honest: ids unique and well-formed, and every
``uses`` reference pointing at a real song or fixture.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.midi_builder import FIXTURES

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_FILE = REPO_ROOT / "tests" / "features.toml"
SONGS_FILE = REPO_ROOT / "tests" / "assets" / "songs.toml"
TESTS_DIR = REPO_ROOT / "tests"

ID_PATTERN = re.compile(r"^F-\d{2}$")
MARKER_PATTERN = re.compile(r'@pytest\.mark\.feature\(\s*["\'](?P<id>[^"\']+)["\']')
VALID_VERIFY = {"e2e", "property", "unit", "visual"}
VALID_STATUS = {"planned", "done"}


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def _registry() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = _load(FEATURES_FILE)["feature"]
    return entries


def _marked_ids() -> dict[str, list[str]]:
    """Map feature id -> the test files that claim to cover it.

    This file is skipped: it documents the marker syntax, and scanning raw text
    would read those examples as real claims.
    """
    self_name = Path(__file__).name
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.name == self_name:
            continue
        text = path.read_text(encoding="utf-8")
        for match in MARKER_PATTERN.finditer(text):
            rel = str(path.relative_to(REPO_ROOT))
            found.setdefault(match.group("id"), []).append(rel)
    return found


def test_feature_ids_are_unique_and_well_formed() -> None:
    ids = [feature["id"] for feature in _registry()]
    assert len(ids) == len(set(ids)), "duplicate feature ids in features.toml"
    bad = [i for i in ids if not ID_PATTERN.match(i)]
    assert not bad, f"feature ids must look like F-07: {bad}"


def test_registry_entries_are_complete() -> None:
    for feature in _registry():
        fid = feature["id"]
        assert feature["name"].strip(), f"{fid} has an empty name"
        assert feature["status"] in VALID_STATUS, (
            f"{fid} has status {feature['status']}"
        )
        assert feature["verify"] in VALID_VERIFY, (
            f"{fid} has verify {feature['verify']}"
        )
        assert feature["milestone"].startswith("M"), f"{fid} has no milestone"


def test_every_used_asset_exists() -> None:
    """A feature cannot point at a song or fixture that was never defined."""
    song_ids = {song["id"] for song in _load(SONGS_FILE)["song"]}
    unknown: list[str] = []
    for feature in _registry():
        for ref in feature.get("uses", []):
            kind, _, name = ref.partition(":")
            known = song_ids if kind == "song" else FIXTURES
            if kind not in {"song", "fixture"} or name not in known:
                unknown.append(f"{feature['id']} -> {ref}")
    assert not unknown, f"features reference unknown assets: {unknown}"


def test_markers_reference_real_features() -> None:
    """Rule 2. Catches a typo'd id before it can fake coverage."""
    known = {feature["id"] for feature in _registry()}
    used = _marked_ids()
    unknown = {fid: files for fid, files in used.items() if fid not in known}
    assert not unknown, (
        f"tests mark feature ids that are not in features.toml: {unknown}"
    )


def uncovered_done_features(
    registry: list[dict[str, Any]], covered: set[str]
) -> list[str]:
    """Features claiming to be done that no test backs up."""
    return [
        f"{feature['id']} ({feature['name']})"
        for feature in registry
        if feature["status"] == "done" and feature["id"] not in covered
    ]


def test_the_gate_actually_fires() -> None:
    """Prove rule 1 catches an untested done feature.

    Without this, an always-passing checker looks identical to a working one for
    as long as every feature is still planned.
    """
    registry = [
        {"id": "F-98", "name": "tested thing", "status": "done"},
        {"id": "F-99", "name": "untested thing", "status": "done"},
        {"id": "F-97", "name": "planned thing", "status": "planned"},
    ]
    assert uncovered_done_features(registry, {"F-98"}) == ["F-99 (untested thing)"]
    assert uncovered_done_features(registry, {"F-98", "F-99"}) == []


def test_done_features_have_a_test() -> None:
    """Rule 1. The gate that makes 'done' mean something."""
    missing = uncovered_done_features(_registry(), set(_marked_ids()))
    assert not missing, (
        "features are marked done but no test claims them.\n"
        "Add @pytest.mark.feature(...) to the test that proves each, or set "
        "status back to planned:\n  " + "\n  ".join(missing)
    )


def test_coverage_summary(capsys: pytest.CaptureFixture[str]) -> None:
    """Not a gate. Prints progress so `pytest -s` shows where things stand."""
    registry = _registry()
    covered = set(_marked_ids())
    done = [f for f in registry if f["status"] == "done"]
    with capsys.disabled():
        print(
            f"\nfeatures: {len(registry)} total, {len(done)} done, "
            f"{len(covered)} with tests"
        )
    assert registry, "features.toml is empty"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_fixture_builds(name: str) -> None:
    """The fixtures themselves must stay loadable, since features.toml points
    at them by name."""
    midi = FIXTURES[name]()
    assert midi.tracks, f"fixture {name} produced no tracks"
    assert midi.length >= 0.0
