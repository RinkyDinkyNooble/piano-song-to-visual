#!/usr/bin/env python
"""Fetch the MusicXML test scores that are not committed.

They come from the Unofficial MusicXML Test Suite, which is MIT. That is a
permissive licence but a conditional one: it asks that the copyright notice
travel with the files. This repository is 0BSD and imposes no conditions on
anyone downstream, so carrying MIT files inside it would quietly attach an
obligation the repository's own licence says is not there. Same reasoning as the
CC BY-SA songs, and the same answer.

    python scripts/fetch_test_scores.py            # download what is missing
    python scripts/fetch_test_scores.py --check    # verify, download nothing

Every file is checked against the SHA-256 in tests/assets/scores.toml. A file
whose hash does not match is refused rather than used: the tests built on it
would otherwise be measuring something nobody chose.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "tests" / "assets" / "scores.toml"
DESTINATION = REPO_ROOT / "tests" / "assets" / "scores"

TIMEOUT_S = 45


def load() -> dict[str, object]:
    with MANIFEST.open("rb") as handle:
        data: dict[str, object] = tomllib.load(handle)
    return data


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(url: str, into: Path) -> bytes:
    # The URL is built from the base in the committed manifest, never from
    # anything a caller passes in, so the scheme cannot be steered.
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "psv-tests"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        body: bytes = response.read()
    into.parent.mkdir(parents=True, exist_ok=True)
    into.write_bytes(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is present and verified, downloading nothing",
    )
    args = parser.parse_args(argv)

    manifest = load()
    scores = manifest["score"]
    base = manifest["base_url"]
    assert isinstance(scores, list) and isinstance(base, str)

    missing = failed = 0
    for entry in scores:
        name = entry["file"]
        path = DESTINATION / name
        expected = entry["sha256"]

        if path.exists():
            actual = digest(path)
            if actual == expected:
                print(f"  {entry['id']:<18} ok")
                continue
            print(f"  {entry['id']:<18} HASH MISMATCH", file=sys.stderr)
            if args.check:
                failed += 1
                continue
            path.unlink()

        if args.check:
            print(f"  {entry['id']:<18} missing")
            missing += 1
            continue

        try:
            body = fetch(f"{base}/{name}", path)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {entry['id']:<18} FAILED: {exc}", file=sys.stderr)
            failed += 1
            continue

        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            path.unlink(missing_ok=True)
            print(
                f"  {entry['id']:<18} REFUSED: hash {actual[:12]} != {expected[:12]}",
                file=sys.stderr,
            )
            failed += 1
            continue
        print(f"  {entry['id']:<18} fetched ({len(body)} bytes)")

    print()
    print(f"{manifest['source']}, {manifest['licence']}")
    print(f"  {manifest['homepage']}")
    if args.check and missing:
        print(f"\n{missing} score(s) not present; tests needing them will skip.")
    if failed:
        print(f"\n{failed} score(s) could not be verified.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
