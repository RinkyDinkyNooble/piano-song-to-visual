"""The failures the command line reports, defined where they cost nothing.

These used to live beside the code that raises them, which put the CLI in an
awkward spot: to name `AudioError` in an `except` clause it had to import
`psv.audio.backends`, and that module imports numpy at the top. So `psv
--version` on an install without the optional extras died on a missing numpy
before argparse had looked at a single argument, and `psv inspect` — which
needs neither audio nor a renderer — died the same way.

An exception class has no dependencies of its own. Defining them here lets the
CLI say what went wrong without dragging in the machinery that goes wrong, and
the modules that raise them re-export them, so `from psv.audio.backends import
AudioError` still works for anyone who was already writing that.
"""

from __future__ import annotations


class AudioError(RuntimeError):
    """Audio could not be produced by any available backend."""


class VideoWriteError(RuntimeError):
    """Encoding failed, or the encoder was unavailable."""


class MissingExtra(RuntimeError):
    """A command needs an optional dependency that is not installed.

    Raised in place of the `ModuleNotFoundError` a bare import would give, so
    the message names the thing to install rather than the module that happened
    to be imported first.
    """

    def __init__(self, extra: str, what: str) -> None:
        super().__init__(
            f"{what} needs the `{extra}` extra, which is not installed:\n"
            f"    pip install 'piano-song-to-visual[{extra}]'"
        )
        self.extra = extra
