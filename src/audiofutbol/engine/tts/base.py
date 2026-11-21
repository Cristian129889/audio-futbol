"""Speech backend protocol and silent test double."""

from __future__ import annotations

from typing import Protocol


class SpeechBackend(Protocol):
    """Strategy interface implemented by all TTS output backends."""

    @property
    def name(self) -> str:
        """Human-readable backend name."""

    def is_available(self) -> bool:
        """Return True if this backend can currently emit speech."""

    def speak(self, text: str, interrupt: bool = False) -> bool:
        """Speak text. Return True on success."""

    def braille(self, text: str) -> bool:
        """Optionally show text on a Braille display. Return True if sent."""

    def cancel(self) -> None:
        """Immediately stop ongoing speech."""


class NullSpeechBackend:
    """Silent backend that records spoken utterances (useful for headless tests)."""

    def __init__(self) -> None:
        self.spoken: list[tuple[str, bool]] = []
        self.brailled: list[str] = []
        self.cancel_count: int = 0

    @property
    def name(self) -> str:
        return "Silencioso (pruebas)"

    def is_available(self) -> bool:
        return True

    def speak(self, text: str, interrupt: bool = False) -> bool:
        self.spoken.append((str(text), interrupt))
        return True

    def braille(self, text: str) -> bool:
        self.brailled.append(str(text))
        return True

    def cancel(self) -> None:
        self.cancel_count += 1
