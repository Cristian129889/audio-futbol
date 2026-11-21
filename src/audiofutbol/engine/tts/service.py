"""Chain-of-responsibility speech service with history and HUD callback."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable, Iterable

from audiofutbol.engine.tts.base import SpeechBackend
from audiofutbol.engine.tts.nvda import NvdaDllBackend
from audiofutbol.engine.tts.sapi import Sapi5Backend


class SpeechService:
    """Routes speech through prioritized backends (NVDA DLL -> SAPI5) without console I/O."""

    def __init__(
        self,
        backends: list[SpeechBackend],
        braille_enabled: bool = True,
        history_size: int = 100,
    ) -> None:
        self._backends = list(backends)
        self.braille_enabled = braille_enabled
        self._history: deque[str] = deque(maxlen=history_size)
        self._listeners: list[Callable[[str, bool], None]] = []

    def add_listener(self, listener: Callable[[str, bool], None]) -> None:
        """Subscribe a listener (e.g., HUD caption renderer) to spoken messages."""
        self._listeners.append(listener)

    @property
    def last_message(self) -> str:
        return self._history[-1] if self._history else ""

    @property
    def history(self) -> list[str]:
        return list(self._history)

    @property
    def backends(self) -> list[SpeechBackend]:
        return list(self._backends)

    def active_backend_name(self) -> str:
        for backend in self._backends:
            if backend.is_available():
                return backend.name
        return "Sin motor de voz activo"

    def nvda_status_summary(self) -> str:
        for backend in self._backends:
            if isinstance(backend, NvdaDllBackend):
                if backend.is_available():
                    return f"NVDA activo mediante {backend.dll_path.name if backend.dll_path else 'DLL'}."
                if backend.is_dll_loaded():
                    return (
                        f"DLL de NVDA cargada ({backend.dll_path.name if backend.dll_path else ''}), "
                        f"pero NVDA no está abierto. Usando {self.active_backend_name()}."
                    )
                return f"DLL de NVDA no cargada: {backend.error} Usando {self.active_backend_name()}."
        return f"Motor de voz activo: {self.active_backend_name()}."

    def speak(self, text: str, interrupt: bool = False) -> bool:
        """Speak text via the first available backend and notify visual listeners."""
        message = str(text)
        if not message:
            return False
        self._history.append(message)
        for listener in self._listeners:
            try:
                listener(message, interrupt)
            except Exception:
                pass

        for backend in self._backends:
            try:
                if backend.is_available() and backend.speak(message, interrupt=interrupt):
                    if self.braille_enabled:
                        backend.braille(message)
                    return True
            except Exception:
                continue
        return False

    def repeat_last(self) -> bool:
        """Repeat the most recent spoken message, interrupting current speech."""
        if not self._history:
            return False
        return self.speak(self._history[-1], interrupt=True)

    def silence(self) -> None:
        """Cancel speech on every available backend."""
        for backend in self._backends:
            try:
                if backend.is_available():
                    backend.cancel()
            except Exception:
                pass


def create_default_speech_service(
    dll_search_dirs: Iterable[Path],
    braille_enabled: bool = True,
) -> SpeechService:
    """Build the default Windows-ready speech chain: NVDA DLL first, SAPI5 fallback."""
    nvda = NvdaDllBackend(dll_search_dirs)
    sapi = Sapi5Backend()
    return SpeechService([nvda, sapi], braille_enabled=braille_enabled)
