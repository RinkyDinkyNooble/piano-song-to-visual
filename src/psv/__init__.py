"""psv — piano song to visual.

Converts a score (MIDI or MusicXML) into a Synthesia-style falling-notes
practice video, arranged under hard hand-span constraints so it stays
playable.
"""

from psv.render.text import Fitted, fit_text

__version__ = "1.0.1"

__all__ = ["Fitted", "__version__", "fit_text"]
