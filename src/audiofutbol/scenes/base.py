"""Scene protocol, GameContext service locator, and stack-based SceneManager."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from audiofutbol.data.competition_loader import StadiumDirectory
from audiofutbol.data.economy import EconomyService
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.domain.match.ports import MatchSettings
from audiofutbol.domain.models import Team
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.service import SpeechService
from audiofutbol.engine.updater import UpdateService
from audiofutbol.engine.window import GameWindow
from audiofutbol.net.client import OnlineClient
from audiofutbol.net.match_sync import announcement, sound_for
from audiofutbol.net.protocol import MSG
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
    extra_national_teams: list[Team] = field(default_factory=list)
    updater: UpdateService | None = None
    economy: EconomyService | None = None
    # One connection for the whole app: leaving the lobby does not disconnect.
    online: OnlineClient = field(default_factory=OnlineClient)
    online_message_handler: Callable[[dict[str, Any]], None] | None = None
    online_match_status: str = ""

    def match_settings(self) -> MatchSettings:
        return MatchSettings(
            turn_seconds=self.config.turno_seg,
            var_enabled=self.config.var,
            shot_notice_enabled=self.config.aviso_tiros,
            crowd_volume=self.config.vol_ambiente,
        )

    def save_config(self) -> None:
        self.config.save(self.paths.config_file)

    # -- online match sharing ----------------------------------------------
    def pump_online(self) -> None:
        """Drain network messages on the main thread and announce new events."""

        while True:
            message = self.online.poll()
            if message is None:
                return
            handler = self.online_message_handler
            if handler is not None:
                handler(message)
            else:
                self.handle_online_match_message(message)

    def handle_online_match_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == MSG.MATCH_EVENT:
            event = dict(message.get("event") or {})
            event_id = str(event.get("id") or "")
            if event_id and event_id in self.online.announced_match_events:
                return
            if event_id:
                self.online.announced_match_events.add(event_id)
            text = announcement(event, self.online.client_id)
            if text:
                if self.online.match_authoritative and not str(event.get("text") or ""):
                    self.audio.play_sfx(sound_for(str(event.get("kind") or "play")))
                else:
                    self.ui.speak(text, interrupt=False)
                    self.audio.play_sfx(sound_for(str(event.get("kind") or "play")))
        elif kind == MSG.MATCH_START:
            text = str(message.get("text") or "Partido autoritativo iniciado.")
            self.ui.speak(text, interrupt=True)
            self.audio.play_sfx("silbato")
        elif kind == MSG.MATCH_STOP:
            text = str(message.get("text") or "Partido autoritativo detenido.")
            self.ui.speak(text, interrupt=True)
            self.online.match_active = False
            self.online.match_prompt = None
        elif kind == MSG.MATCH_NARRATION:
            text = str(message.get("text") or "")
            if text:
                self.ui.speak(text, interrupt=bool(message.get("interrupt")))
        elif kind == MSG.MATCH_SFX:
            self.audio.play_sfx(
                str(message.get("event") or "balon"),
                pan=message.get("pan"),
                volume=float(message.get("volume", 1.0) or 1.0),
            )
        elif kind == MSG.MATCH_AUDIO:
            self._handle_match_audio(message)
        elif kind == MSG.MATCH_HUD:
            self.online.match_hud = str(message.get("status") or "")
            self.window.update_status(self.online.match_hud)
        elif kind == MSG.MATCH_END:
            text = str(message.get("text") or "El partido en línea terminó.")
            self.ui.speak(text, interrupt=True)
            self.audio.play_sfx("silbato_final")
            self.online.match_active = False
            self.online.match_prompt = None
        elif kind == MSG.MATCH_PROMPT:
            if str(message.get("controller_id") or "") != self.online.client_id:
                title = str(message.get("title") or "")
                controller = str(message.get("controller_name") or "El anfitrión")
                if title:
                    self.ui.speak(f"{controller}: {title}", interrupt=False)
        elif kind == MSG.MATCH_RESET:
            text = str(message.get("text") or "Marcador compartido reiniciado.")
            self.ui.speak(f"En línea: {text}", interrupt=True)
            self.audio.play_sfx("silbato")
            self.online_match_status = ""
        elif kind == MSG.MATCH_STATE:
            state = self.online.match_state
            status = str(state.get("status") or "")
            if self.online_match_status and status != self.online_match_status and status in (
                "final", "finished", "full_time", "fulltime", "ft",
            ):
                goals = int((state.get("totals") or {}).get("goals") or 0)
                self.ui.speak(
                    f"En línea: final del partido compartido. {goals} gol"
                    f"{'es' if goals != 1 else ''} en la sala.",
                    interrupt=True,
                )
                self.audio.play_sfx("silbato_final")
            self.online_match_status = status
        elif kind == MSG.NOTICE and message.get("kind") == "disconnected":
            self.ui.speak("Conexión con el servidor perdida.", interrupt=True)
            self.audio.play_sfx("jugador_desconectado")

    def _handle_match_audio(self, message: dict[str, Any]) -> None:
        """Apply a server audio instruction from the authoritative engine."""

        action = str(message.get("action") or "")
        if action == "crowd":
            try:
                factor = float(message.get("factor", 1.0))
            except (TypeError, ValueError):
                factor = 1.0
            self.audio.start_crowd_ambience(factor)
        elif action == "stop_crowd":
            self.audio.stop_crowd_ambience()
        elif action == "theme":
            try:
                volume = float(message.get("volume", 1.0))
            except (TypeError, ValueError):
                volume = 1.0
            self.audio.play_theme(
                str(message.get("name") or ""), loop=bool(message.get("loop")),
                volume=volume,
            )
        elif action == "menu_music":
            self.audio.play_menu_music()
        elif action == "stop_music":
            try:
                fade = int(message.get("fade_ms", 600))
            except (TypeError, ValueError):
                fade = 600
            self.audio.stop_music(fade)
        elif action == "jingle":
            self.ctx_audio_jingle(message)

    def ctx_audio_jingle(self, message: dict[str, Any]) -> None:
        """Play a server-sent jingle through the shared AudioUI helper."""

        try:
            ms = int(message.get("ms", 4000))
        except (TypeError, ValueError):
            ms = 4000
        try:
            volume = float(message.get("volume", 1.0))
        except (TypeError, ValueError):
            volume = 1.0
        self.ui.play_jingle(str(message.get("name") or ""), ms, volume=volume)

    def sharing_match(self) -> bool:
        """True when a local match can be mirrored to the current room."""

        return bool(self.online.connected and self.online.room)

    def share_match_event(self, kind: str, text: str = "", **fields: Any) -> None:
        if self.sharing_match():
            self.online.send_match_event(kind, text, **fields)

    def share_match_state(self, **fields: Any) -> None:
        if self.sharing_match():
            self.online.send_match_state(**fields)
            if fields.get("status"):
                self.online_match_status = str(fields["status"])


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
