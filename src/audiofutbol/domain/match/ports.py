"""Hexagonal Port defining how the MatchEngine interacts with presentation/audio."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Callable, Protocol


@dataclass(frozen=True)
class MatchSettings:
    """Immutable gameplay rules snapshot for a single match."""

    turn_seconds: int = 60
    var_enabled: bool = True
    shot_notice_enabled: bool = True
    crowd_volume: float = 0.35


class MatchIO(Protocol):
    """Port implemented by UI adapters (Pygame/TTS) or headless test runners."""

    def speak(self, text: str, interrupt: bool = False) -> None:
        """Announce narration or shortcut information."""

    def play_sfx(
        self, event: str, pan: float | None = None, volume: float = 1.0
    ) -> None:
        """Trigger a sound effect."""

    def start_crowd(self, attendance_factor: float) -> None:
        """Start looping stadium ambience."""

    def stop_crowd(self) -> None:
        """Stop looping stadium ambience."""

    def play_theme(self, name: str, loop: bool = False, volume: float = 1.0) -> None:
        """Play a match theme (versus, penalties, etc.)."""

    def play_menu_music(self) -> None:
        """Play background music (used in halftime summaries/menus)."""

    def stop_music(self, fade_ms: int = 1200) -> None:
        """Fade out music."""

    def play_jingle(self, name: str, ms: int, volume: float = 1.0) -> None:
        """Play a post-match victory/defeat jingle."""

    def pause(self, ms: int) -> None:
        """Pause briefly for dramatic pacing (VAR, kickoff)."""

    def choose(
        self,
        title: str,
        options: list[str],
        shortcuts: dict[str, Callable[[], str | None]] | None = None,
        quick_keys: dict[str, int] | None = None,
        initial_index: int = 0,
    ) -> int | None:
        """Present a choice menu; return chosen index or None on Escape."""

    def confirm(self, question: str) -> bool:
        """Present a Yes/No confirmation prompt."""

    def read_lines(self, title: str, lines: list[str]) -> None:
        """Open an interactive line-by-line reader."""

    def read_summary_stream(self, title: str, lines: list[str]) -> None:
        """Stream halftime summary lines aloud (skippable with Esc/Space)."""

    def update_hud(self, status_line: str) -> None:
        """Update the optional visual HUD scoreboard/clock."""


class HeadlessMatchIO:
    """Silent, instant MatchIO adapter for AI-simulated matches and unit tests."""

    def __init__(
        self,
        rng: random.Random | None = None,
        decision_callback: Callable[[str, list[str]], int | None] | None = None,
    ) -> None:
        self._rng = rng or random.Random()
        self._decision_callback = decision_callback
        self.messages: list[str] = []
        self.sfx_events: list[str] = []

    def speak(self, text: str, interrupt: bool = False) -> None:
        self.messages.append(text)

    def play_sfx(
        self, event: str, pan: float | None = None, volume: float = 1.0
    ) -> None:
        self.sfx_events.append(event)

    def start_crowd(self, attendance_factor: float) -> None:
        pass

    def stop_crowd(self) -> None:
        pass

    def play_theme(self, name: str, loop: bool = False, volume: float = 1.0) -> None:
        pass

    def play_menu_music(self) -> None:
        pass

    def stop_music(self, fade_ms: int = 1200) -> None:
        pass

    def play_jingle(self, name: str, ms: int, volume: float = 1.0) -> None:
        pass

    def pause(self, ms: int) -> None:
        pass

    def choose(
        self,
        title: str,
        options: list[str],
        shortcuts: dict[str, Callable[[], str | None]] | None = None,
        quick_keys: dict[str, int] | None = None,
        initial_index: int = 0,
    ) -> int | None:
        if self._decision_callback is not None:
            return self._decision_callback(title, options)
        return self._rng.randrange(len(options)) if options else None

    def confirm(self, question: str) -> bool:
        return True

    def read_lines(self, title: str, lines: list[str]) -> None:
        pass

    def read_summary_stream(self, title: str, lines: list[str]) -> None:
        pass

    def update_hud(self, status_line: str) -> None:
        pass
