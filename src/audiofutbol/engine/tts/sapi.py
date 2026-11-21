"""Non-blocking Windows SAPI5 TTS fallback backend (zero console dependency)."""

from __future__ import annotations

import sys


SVS_FLAGS_ASYNC = 1
SVS_FPURGE_BEFORE_SPEAK = 2


class Sapi5Backend:
    """Native Windows SAPI5 voice via COM (async so it never blocks Pygame)."""

    def __init__(self) -> None:
        self._voice = None
        self.error: str = ""
        self._init_voice()

    @property
    def name(self) -> str:
        return "Windows SAPI5"

    def _init_voice(self) -> None:
        if sys.platform != "win32":
            self.error = "SAPI5 solo está disponible en Windows."
            return
        try:
            import comtypes.client  # type: ignore[import-untyped]

            self._voice = comtypes.client.CreateObject("SAPI.SpVoice")
        except Exception as exc:
            self._voice = None
            self.error = f"No se pudo inicializar Windows SAPI5: {exc}"

    def is_available(self) -> bool:
        return self._voice is not None

    def speak(self, text: str, interrupt: bool = False) -> bool:
        if self._voice is None:
            return False
        flags = SVS_FLAGS_ASYNC | (SVS_FPURGE_BEFORE_SPEAK if interrupt else 0)
        try:
            self._voice.Speak(str(text), flags)
            return True
        except Exception:
            return False

    def braille(self, text: str) -> bool:
        return False

    def cancel(self) -> None:
        if self._voice is None:
            return
        try:
            self._voice.Speak("", SVS_FLAGS_ASYNC | SVS_FPURGE_BEFORE_SPEAK)
        except Exception:
            pass
