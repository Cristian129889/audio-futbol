"""Persistent user settings stored in config.json."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any


def _clamp_unit_float(value: Any, default: float) -> float:
    """Convert value to a finite float in [0.0, 1.0], falling back to default."""
    if isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(0.0, min(1.0, round(num, 2)))


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
    buscar_actualizaciones_al_iniciar: bool = True
    version_omitida: str = ""

    def clamp(self) -> None:
        self.vol_maestro = _clamp_unit_float(self.vol_maestro, 0.65)
        self.vol_musica = _clamp_unit_float(self.vol_musica, 0.35)
        self.vol_efectos = _clamp_unit_float(self.vol_efectos, 0.50)
        self.vol_ambiente = _clamp_unit_float(self.vol_ambiente, 0.25)
        self.musica = bool(self.musica)
        if isinstance(self.turno_seg, bool) or self.turno_seg not in (120, 60, 30):
            self.turno_seg = 60
        self.gol_visitante = bool(self.gol_visitante)
        self.var = bool(self.var)
        self.aviso_tiros = bool(self.aviso_tiros)
        self.braille = bool(self.braille)
        self.buscar_actualizaciones_al_iniciar = bool(
            self.buscar_actualizaciones_al_iniciar
        )
        self.version_omitida = str(self.version_omitida or "").strip()

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
            "buscar_actualizaciones_al_iniciar": self.buscar_actualizaciones_al_iniciar,
            "version_omitida": self.version_omitida,
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
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            tmp.replace(path)
            return True
        except Exception:
            return False
