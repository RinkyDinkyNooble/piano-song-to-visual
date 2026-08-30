#!/usr/bin/env python
"""Fetch the CC BY-SA test songs that are not committed to this repo.

Reads tests/assets/songs.toml, downloads every entry with ``committed = false``
into tests/assets/fetched/, and verifies each file against its recorded SHA-256.
Files already present with the right hash are left alone.

    python scripts/fetch_test_songs.py            # fetch what is missing
    python scripts/fetch_test_songs.py --check    # verify only, download nothing
    python scripts/fetch_test_songs.py --force    # re-download everything

Public-domain songs are committed and are only verified, never downloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "tests" / "assets" / "songs.toml"
COMMITTED_DIR = REPO_ROOT / "tests" / "assets" / "public-domain"
FETCHED_DIR = REPO_ROOT / "tests" / "assets" / "fetched"

TIMEOUT_S = 60
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def load_manifest() -> dict[str, Any]:
    with MANIFEST.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def target_path(song: dict[str, Any]) -> Path:
    root = COMMITTED_DIR if song["committed"] else FETCHED_DIR
    return root / str(song["filename"])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str) -> bytes:
    """Fetch a URL over https, refusing anything oversized or non-https."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https url: {url}")
    # https-only is enforced above, so the audited schemes warning does not apply.
    request = urllib.request.Request(url, headers={"User-Agent": "psv-test-fetch"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        data: bytes = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"response larger than {MAX_DOWNLOAD_BYTES} bytes: {url}")
    return data


def extract(payload: bytes, song: dict[str, Any]) -> bytes:
    """Pull the wanted member out, if the download was a zip archive."""
    member = song.get("archive_member")
    if member is None:
        return payload
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = {Path(n).name: n for n in archive.namelist()}
        if member not in names:
            raise KeyError(f"{member!r} not in archive; found {sorted(names)}")
        return archive.read(names[member])


def fetch_one(song: dict[str, Any], base_url: str, *, force: bool) -> str:
    """Return a one-word status: ok, fetched, missing, or a mismatch message."""
    path = target_path(song)
    expected = str(song["sha256"])

    if path.exists() and not force:
        actual = sha256(path.read_bytes())
        if actual == expected:
            return "ok"
        return f"HASH MISMATCH (expected {expected[:12]}, got {actual[:12]})"

    if song["committed"]:
        return "MISSING (committed file, should be in git)"

    url = f"{base_url}/{song['path']}"
    try:
        data = extract(download(url), song)
    except (urllib.error.URLError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        return f"FAILED ({exc})"

    actual = sha256(data)
    if actual != expected:
        return f"HASH MISMATCH after download (got {actual[:12]})"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return "fetched"


def check_one(song: dict[str, Any]) -> str:
    """Verify a song already on disk, without downloading.

    A missing committed song is a real failure: it belongs in git. A missing
    fetched song is only "absent", since it is gitignored by design and the
    tests that need it skip.
    """
    path = target_path(song)
    if not path.exists():
        return "MISSING" if song["committed"] else "absent"
    actual = sha256(path.read_bytes())
    return "ok" if actual == str(song["sha256"]) else "HASH MISMATCH"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify only, download nothing"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-download even if present"
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="with --check, also fail when a fetchable song has not been fetched",
    )
    args = parser.parse_args(argv)

    manifest = load_manifest()
    base_url = manifest["base_url"]
    songs: list[dict[str, Any]] = manifest["song"]

    failures = 0
    absent = 0
    for song in songs:
        if args.check:
            status = check_one(song)
        else:
            status = fetch_one(song, base_url, force=args.force)

        if status == "absent":
            absent += 1
            if args.require_all:
                failures += 1
        elif status not in {"ok", "fetched"}:
            failures += 1
        marker = " " if status in {"ok", "fetched"} else "!"
        print(f"{marker} {song['id']:12} {status:12} {song['licence']}")

    if failures:
        print(f"\n{failures} song(s) unavailable.", file=sys.stderr)
        print("Run: python scripts/fetch_test_songs.py", file=sys.stderr)
        return 1

    if absent:
        print(
            f"\n{len(songs) - absent} of {len(songs)} songs present. "
            f"{absent} not fetched yet, so tests needing them will skip."
        )
        return 0

    print(f"\nAll {len(songs)} songs present and verified.")
    print(
        "Attribution for the CC BY-SA entries is recorded in tests/assets/songs.toml."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
