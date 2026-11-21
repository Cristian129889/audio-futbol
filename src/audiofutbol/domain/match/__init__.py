"""Match simulation domain package (decoupled from UI/TTS/Pygame via MatchIO)."""

from audiofutbol.domain.match.engine import MatchEngine
from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchIO, MatchSettings
from audiofutbol.domain.match.state import MatchState

__all__ = ["HeadlessMatchIO", "MatchEngine", "MatchIO", "MatchSettings", "MatchState"]
