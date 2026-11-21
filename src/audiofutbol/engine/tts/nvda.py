"""NVDA Controller Client DLL speech backend (zero console dependency)."""

from __future__ import annotations

import ctypes
from pathlib import Path
import struct
from typing import Iterable


_POINTER_BITS = struct.calcsize("P") * 8
DLL_CANDIDATE_NAMES: tuple[str, ...] = (
    "nvdaControllerClient64.dll" if _POINTER_BITS == 64 else "nvdaControllerClient32.dll",
    "nvdaControllerClient.dll",
    "nvda.dll",
)


class NvdaDllBackend:
    """Direct NVDA speech and Braille output via nvdaControllerClient DLL."""

    def __init__(self, search_dirs: Iterable[Path]) -> None:
        self.dll: ctypes.WinDLL | None = None  # type: ignore[name-defined]
        self.dll_path: Path | None = None
        self.error: str = ""
        self._has_braille: bool = False
        self._load_dll(list(search_dirs))

    @property
    def name(self) -> str:
        return "NVDA (DLL)"

    def _load_dll(self, search_dirs: list[Path]) -> None:
        if not hasattr(ctypes, "WinDLL"):
            self.error = "Sistema no compatible con WinDLL."
            return

        for directory in search_dirs:
            for filename in DLL_CANDIDATE_NAMES:
                candidate = directory / filename
                if not candidate.is_file():
                    continue
                try:
                    loaded = ctypes.WinDLL(str(candidate))
                    loaded.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
                    loaded.nvdaController_speakText.restype = ctypes.c_ulong
                    loaded.nvdaController_cancelSpeech.restype = ctypes.c_ulong
                    loaded.nvdaController_testIfRunning.restype = ctypes.c_ulong
                    if hasattr(loaded, "nvdaController_brailleMessage"):
                        loaded.nvdaController_brailleMessage.argtypes = [ctypes.c_wchar_p]
                        loaded.nvdaController_brailleMessage.restype = ctypes.c_ulong
                        self._has_braille = True
                except (OSError, AttributeError) as exc:
                    self.error = f"No se pudo cargar {filename}: {exc}"
                    continue

                self.dll = loaded
                self.dll_path = candidate
                self.error = ""
                return

        if not self.error:
            self.error = "No se encontró la DLL de NVDA en lib ni en la carpeta del juego."

    def is_dll_loaded(self) -> bool:
        return self.dll is not None

    def is_available(self) -> bool:
        """Return True if the DLL is loaded and NVDA is currently running."""
        if self.dll is None:
            return False
        try:
            return int(self.dll.nvdaController_testIfRunning()) == 0
        except OSError:
            return False

    def speak(self, text: str, interrupt: bool = False) -> bool:
        if not self.is_available() or self.dll is None:
            return False
        try:
            if interrupt:
                self.dll.nvdaController_cancelSpeech()
            return int(self.dll.nvdaController_speakText(str(text))) == 0
        except OSError:
            return False

    def braille(self, text: str) -> bool:
        if not self._has_braille or not self.is_available() or self.dll is None:
            return False
        try:
            return int(self.dll.nvdaController_brailleMessage(str(text))) == 0
        except OSError:
            return False

    def cancel(self) -> None:
        if self.is_available() and self.dll is not None:
            try:
                self.dll.nvdaController_cancelSpeech()
            except OSError:
                pass
