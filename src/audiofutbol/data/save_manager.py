"""Safe, versioned JSON persistence for tournament saves (no pickle fragility)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audiofutbol.domain.competitions.factory import SAVE_SLOTS, deserialize_competition
from audiofutbol.engine.paths import AppPaths

SAVE_SCHEMA_VERSION = 1


class SaveManager:
    """Persists and restores competitions as UTF-8 JSON inside `saves/`."""

    def __init__(self, paths: AppPaths) -> None:
        self.saves_dir: Path = paths.saves_dir

    def slot_path(self, key: str) -> Path:
        return self.saves_dir / f"partida_{key}.json"

    def exists(self, key: str) -> bool:
        return self.slot_path(key).is_file()

    def list_saved_slots(self) -> list[tuple[str, str]]:
        return [(key, label) for key, label in SAVE_SLOTS if self.exists(key)]

    def save(self, key: str, competition: Any) -> bool:
        try:
            self.saves_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": SAVE_SCHEMA_VERSION,
                "competition": competition.to_dict(),
            }
            target = self.slot_path(key)
            tmp = target.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            tmp.replace(target)
            return True
        except Exception:
            return False

    def load(self, key: str) -> Any | None:
        target = self.slot_path(key)
        if not target.is_file():
            return None
        try:
            with target.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            raw = payload.get("competition", payload)
            return deserialize_competition(raw)
        except Exception:
            return None

    def delete(self, key: str) -> None:
        try:
            target = self.slot_path(key)
            if target.is_file():
                target.unlink()
        except Exception:
            pass
