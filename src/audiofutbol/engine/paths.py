"""Filesystem path resolution for source and frozen (PyInstaller) builds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


def resolve_base_dir() -> Path:
    """Resolve the application root directory.

    In a PyInstaller frozen executable, assets and saves live next to the .exe.
    In source/editable installs, walk up from this file until finding the repo root
    (identified by `datos/` or `competiciones/` or `pyproject.toml`), falling back to CWD.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "competiciones").is_dir() or (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AppPaths:
    """Canonical paths for game resources, configuration, saves, and logs."""

    base_dir: Path

    @classmethod
    def detect(cls, base_dir: Path | None = None) -> AppPaths:
        return cls(base_dir=(base_dir.resolve() if base_dir else resolve_base_dir()))

    @property
    def sounds_dir(self) -> Path:
        return self.base_dir / "sonidos"

    @property
    def music_dir(self) -> Path:
        return self.base_dir / "musica"

    @property
    def themes_dir(self) -> Path:
        return self.base_dir / "temas"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "datos"

    @property
    def clubs_dir(self) -> Path:
        return self.data_dir / "clubes"

    @property
    def competitions_dir(self) -> Path:
        return self.base_dir / "competiciones"

    @property
    def lib_dir(self) -> Path:
        return self.base_dir / "lib"

    @property
    def saves_dir(self) -> Path:
        return self.base_dir / "saves"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def config_file(self) -> Path:
        return self.base_dir / "config.json"

    @property
    def sound_map_file(self) -> Path:
        return self.base_dir / "sonidos.txt"

    @property
    def sound_list_file(self) -> Path:
        return self.base_dir / "lista_sonidos.txt"

    @property
    def error_log_file(self) -> Path:
        return self.base_dir / "error.log"

    def dll_search_dirs(self) -> list[Path]:
        """Directories searched for the NVDA Controller Client DLL."""
        dirs = [self.lib_dir, self.base_dir, self.base_dir / "dll"]
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass = Path(getattr(sys, "_MEIPASS"))
            dirs.extend([meipass / "lib", meipass])
        return dirs
