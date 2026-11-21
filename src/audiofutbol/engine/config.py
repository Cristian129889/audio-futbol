"""Persistent user settings stored in config.json."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass
class GameConfig:
    """User-configurable audio, gameplay, and accessibility settings."""

    vol_maestro: float = 0.65
    vol_musica: float = 0.35
    vol_efectos: float = 0.50
    musica: bool = True
    turno_seg: int = 60
    vol_ambiente: float = 0.25
    gol_visitante: bool = True
    var: bool = True
    aviso_tiros: bool = True
    braille: bool = True

    def clamp(self) -> None:
        self.vol_maestro = max(0.0, min(1.0, round(float(self.vol_maestro), 2)))
        self.vol_musica = max(0.0, min(1.0, round(float(self.vol_musica), 2)))
        self.vol_efectos = max(0.0, min(1.0, round(float(self.vol_efectos), 2)))
        self.vol_ambiente = max(0.0, min(1.0, round(float(self.vol_ambiente), 2)))
        if self.turno_seg not in (120, 60, 30):
            self.turno_seg = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "vol_maestro": self.vol_maestro,
            "vol_musica": self.vol_musica,
            "vol_efectos": self.vol_efectos,
            "musica": self.musica,
            "turno_seg": self.turno_seg,
            "vol_ambiente": self.vol_ambiente,
            "gol_visitante": self.gol_visitante,
            "var": self.var,
            "aviso_tiros": self.aviso_tiros,
            "braille": self.braille,
        }

    @classmethod
    def load(cls, path: Path) -> GameConfig:
        cfg = cls()
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                for key in cfg.to_dict():
                    if key in raw:
                        setattr(cfg, key, raw[key])
        except Exception:
            pass
        cfg.clamp()
        return cfg

    def save(self, path: Path) -> bool:
        self.clamp()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False
