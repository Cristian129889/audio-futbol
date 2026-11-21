"""Scene protocol, GameContext service locator, and stack-based SceneManager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from audiofutbol.data.competition_loader import StadiumDirectory
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.domain.match.ports import MatchSettings
from audiofutbol.domain.models import Team
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.service import SpeechService
from audiofutbol.engine.window import GameWindow
from audiofutbol.ui.widgets import AudioUI


@dataclass
class GameContext:
    """Shared dependencies injected into scenes (composition root container)."""

    paths: AppPaths
    config: GameConfig
    speech: SpeechService
    audio: AudioEngine
    window: GameWindow
    ui: AudioUI
    saves: SaveManager
    stadiums: StadiumDirectory
    national_teams: list[Team]
    loaded_from_csv: bool

    def match_settings(self) -> MatchSettings:
        return MatchSettings(
            turn_seconds=self.config.turno_seg,
            var_enabled=self.config.var,
            shot_notice_enabled=self.config.aviso_tiros,
            crowd_volume=self.config.vol_ambiente,
        )

    def save_config(self) -> None:
        self.config.save(self.paths.config_file)


class Scene(Protocol):
    """A self-contained screen/state in the game flow."""

    def run(self, ctx: GameContext) -> Scene | None:
        """Execute this scene. Return the next Scene to push, or None to exit."""


class SceneManager:
    """Drives the active scene until None is returned."""

    def __init__(self, ctx: GameContext, initial_scene: Scene) -> None:
        self.ctx = ctx
        self._current: Scene | None = initial_scene

    def run(self) -> None:
        while self._current is not None:
            self._current = self._current.run(self.ctx)
