"""Shared test configuration.

Registers the ``feature`` marker that ties tests to entries in features.toml,
and provides paths to the real songs and the synthetic fixtures.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import mido
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "tests" / "assets"
COMMITTED_SONGS = ASSETS / "public-domain"
FETCHED_SONGS = ASSETS / "fetched"
FEATURES_FILE = REPO_ROOT / "tests" / "features.toml"
SONGS_FILE = ASSETS / "songs.toml"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "feature(id): the features.toml entry this test proves, e.g. "
        'feature("F-20"). Enforced by tests/test_feature_coverage.py.',
    )
    config.addinivalue_line(
        "markers",
        "needs_song(id): requires a song that may not be present; skipped when "
        "it has not been fetched.",
    )


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


@pytest.fixture(scope="session")
def features() -> list[dict[str, Any]]:
    """Every entry in features.toml."""
    entries: list[dict[str, Any]] = _load(FEATURES_FILE)["feature"]
    return entries


@pytest.fixture(scope="session")
def songs() -> dict[str, dict[str, Any]]:
    """Every entry in songs.toml, keyed by id."""
    return {s["id"]: s for s in _load(SONGS_FILE)["song"]}


def song_path(song: dict[str, Any]) -> Path:
    root = COMMITTED_SONGS if song["committed"] else FETCHED_SONGS
    return root / str(song["filename"])


@pytest.fixture
def load_song(
    songs: dict[str, dict[str, Any]],
) -> Callable[[str], mido.MidiFile]:
    """Return a callable that loads a song by id, skipping if it is absent.

    Committed songs are always there. Fetched ones are gitignored, so a fresh
    clone skips those tests until `python scripts/fetch_test_songs.py` runs.
    """

    def _load_song(song_id: str) -> mido.MidiFile:
        if song_id not in songs:
            raise KeyError(f"unknown song id {song_id!r}; see tests/assets/songs.toml")
        song = songs[song_id]
        path = song_path(song)
        if not path.exists():
            pytest.skip(
                f"song {song_id!r} not present; run scripts/fetch_test_songs.py"
            )
        return mido.MidiFile(path)

    return _load_song
