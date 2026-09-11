"""What works with only the core dependency installed.

The README promises that `pip install piano-song-to-visual`, with no extras,
gives you `inspect`, `export`, `arrange` and `constrain`. That promise was not
true: `psv.cli` imported `psv.render.video` and `psv.audio.backends` at module
scope, both of which import numpy, so every command died on a missing numpy
before argparse had looked at an argument. `psv --version` did too. Then
`psv/__init__.py` started re-exporting `fit_text` from under `psv.render`, and
plain `import psv` joined them.

The tests here run in a subprocess with numpy and Pillow made unimportable,
which is the only honest way to check it: this test suite installs the extras,
so nothing in-process can tell you what a bare install does.

They fail on the code before the fix. That is the point of them.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Everything the optional extras bring. Blocked by name, so importing any of
#: them raises exactly what pip's absence would raise.
HEAVY = ("numpy", "PIL", "imageio_ffmpeg", "fluidsynth")

#: Put in front of every snippet. A meta-path finder is used rather than
#: deleting from `sys.modules`, because it also catches an import that happens
#: later, inside a function, which is where the fixed code does its importing.
BLOCK = textwrap.dedent(f"""
    import sys

    HEAVY = {HEAVY!r}

    class Blocker:
        def find_module(self, name, path=None):
            return self.find_spec(name, path)

        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in HEAVY:
                raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
            return None

    sys.meta_path.insert(0, Blocker())
    for name in list(sys.modules):
        if name.split(".")[0] in HEAVY:
            del sys.modules[name]
    """)


def run_without_extras(code: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a Python that cannot import numpy, Pillow or ffmpeg."""
    return subprocess.run(
        [sys.executable, "-c", BLOCK + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )


def test_the_blocker_actually_blocks() -> None:
    """The test's own instrument, checked before anything rests on it."""
    done = run_without_extras("import numpy")
    assert done.returncode != 0
    assert "No module named" in done.stderr


def test_importing_psv_does_not_need_numpy() -> None:
    """`import psv` is the first thing anything does, including `psv --version`."""
    done = run_without_extras("""
        import psv
        print(psv.__version__)
        """)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip()


def test_the_public_text_fitter_does_not_need_numpy() -> None:
    """`fit_text` is pure arithmetic over a callable.

    It lived under `psv.render` at first, which meant reaching it ran
    `psv/render/__init__.py` and imported the whole renderer.
    """
    done = run_without_extras("""
        from psv import fit_text
        print(fit_text("a b", width=100, size=10, measure=lambda t, s: len(t) * s))
        """)
    assert done.returncode == 0, done.stderr
    assert "Fitted" in done.stdout


def test_version_works_without_extras() -> None:
    """The exact command that was reported broken."""
    done = run_without_extras("""
        from psv.cli import main
        raise SystemExit(main(["--version"]))
        """)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("psv ")


def test_help_works_without_extras() -> None:
    done = run_without_extras("""
        from psv.cli import main
        raise SystemExit(main(["--help"]))
        """)
    assert done.returncode == 0, done.stderr
    assert "usage: psv" in done.stdout


@pytest.mark.parametrize("command", ["inspect", "export", "arrange", "constrain"])
def test_the_midi_stages_run_without_extras(command: str, tmp_path: Path) -> None:
    """The four the README says a bare install gives you, each run for real."""
    score = REPO_ROOT / "tests/assets/public-domain/bach-bwv565-toccata-and-fugue.mid"
    output = tmp_path / "out.mid"
    argv = [command, str(score)]
    if command != "inspect":
        argv += ["-o", str(output)]

    done = run_without_extras(f"""
        from psv.cli import main
        raise SystemExit(main({argv!r}))
        """)
    assert done.returncode == 0, done.stderr
    if command != "inspect":
        assert output.is_file()


@pytest.mark.parametrize("command", ["render", "run"])
def test_a_command_that_needs_the_extra_says_which_one(
    command: str, tmp_path: Path
) -> None:
    """Not a traceback. The message names the thing to install.

    A `ModuleNotFoundError: No module named 'numpy'` is true and useless: it
    names the module that happened to be imported first, not the extra that
    would have brought it.
    """
    score = REPO_ROOT / "tests/assets/public-domain/bach-bwv565-toccata-and-fugue.mid"
    argv = [command, str(score), "-o", str(tmp_path / "out.mp4")]

    done = run_without_extras(f"""
        from psv.cli import main
        raise SystemExit(main({argv!r}))
        """)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "render` extra" in done.stderr
    assert "pip install 'piano-song-to-visual[render]'" in done.stderr
    assert "Traceback" not in done.stderr
