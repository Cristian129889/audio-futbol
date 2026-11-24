"""Server-authoritative match runner.

The match engine lives on the server.  Clients only receive narration, sound
instructions, structured events and scoreboard snapshots, and the controlling
client sends back the decisions requested by the engine.  This keeps every
player in a room on exactly the same match instead of simulating it locally.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from audiofutbol.data.csv_loader import create_generic_team
from audiofutbol.domain.match.engine import MatchEngine
from audiofutbol.domain.match.ports import MatchSettings
from audiofutbol.net.match_sync import KIND_INFO, normalize_event
from audiofutbol.net.protocol import MSG, frame

LOGGER = logging.getLogger("audiofutbol.server.match")

Broadcast = Callable[[dict[str, Any]], None]
RecordEvent = Callable[[dict[str, Any]], None]
RecordState = Callable[[dict[str, Any]], None]
Finish = Callable[[str], None]

DECISION_TIMEOUT = 25.0
_EVENT_SOUNDS = {info[1] for info in KIND_INFO.values()}


@dataclass
class _PendingDecision:
    options: list[str]
    choice: int | None = None
    answered: bool = False


class ServerMatchIO:
    """MatchIO adapter that turns engine output into room broadcasts."""

    def __init__(self, runner: "AuthoritativeMatch") -> None:
        self._runner = runner

    # -- narration and events ---------------------------------------------
    def speak(self, text: str, interrupt: bool = False) -> None:
        self._runner.broadcast(frame(
            MSG.MATCH_NARRATION,
            room_id=self._runner.room_id,
            text=str(text)[:600],
            interrupt=bool(interrupt),
        ))

    def emit_match_event(self, kind: str, text: str = "", **payload: Any) -> None:
        # Detailed text normally travels as MATCH_NARRATION.  If the engine was
        # muted (for example after choosing to simulate the rest), keep it in
        # the event so the room still hears every important moment.
        engine = self._runner.engine
        detail = str(text) if engine is not None and engine.state.muted else ""
        event = normalize_event(
            {
                "kind": kind,
                "text": detail,
                "minute": payload.get("minute", ""),
                "team": payload.get("team", ""),
                "player": payload.get("player", ""),
                "score": payload.get("score", ""),
                "extra": {
                    key: value for key, value in payload.items()
                    if key not in ("minute", "team", "player", "score")
                },
            },
            sender="Servidor",
            sender_id="servidor",
            room_id=self._runner.room_id,
        )
        self._runner.record_event(event)

    def emit_match_state(self, **payload: Any) -> None:
        self._runner.record_state(dict(payload))

    def update_hud(self, status_line: str) -> None:
        self._runner.broadcast(frame(
            MSG.MATCH_HUD, room_id=self._runner.room_id, status=str(status_line)[:300]
        ))

    # -- audio -------------------------------------------------------------
    def play_sfx(self, event: str, pan: float | None = None,
                 volume: float = 1.0) -> None:
        name = str(event)
        if name in _EVENT_SOUNDS:
            # The structured event already carries the matching sound.
            return
        self._runner.broadcast(frame(
            MSG.MATCH_SFX, room_id=self._runner.room_id, event=name[:80],
            pan=pan, volume=float(volume),
        ))

    def _audio(self, action: str, **fields: Any) -> None:
        self._runner.broadcast(frame(
            MSG.MATCH_AUDIO, room_id=self._runner.room_id, action=action, **fields
        ))

    def start_crowd(self, attendance_factor: float) -> None:
        self._audio("crowd", factor=float(attendance_factor))

    def stop_crowd(self) -> None:
        self._audio("stop_crowd")

    def play_theme(self, name: str, loop: bool = False,
                   volume: float = 1.0) -> None:
        self._audio("theme", name=str(name)[:80], loop=bool(loop), volume=float(volume))

    def play_menu_music(self) -> None:
        self._audio("menu_music")

    def stop_music(self, fade_ms: int = 1200) -> None:
        self._audio("stop_music", fade_ms=int(fade_ms))

    def play_jingle(self, name: str, ms: int, volume: float = 1.0) -> None:
        self._audio("jingle", name=str(name)[:80], ms=int(ms), volume=float(volume))

    def pause(self, ms: int) -> None:
        # Clients cannot be blocked by the server's pacing; narration carries on.
        return None

    # -- interaction -------------------------------------------------------
    def choose(
        self,
        title: str,
        options: list[str],
        shortcuts: dict[str, Callable[[], str | None]] | None = None,
        quick_keys: dict[str, int] | None = None,
        initial_index: int = 0,
    ) -> int | None:
        return self._runner.request_decision(
            title, options, kind="choose", initial_index=initial_index
        )

    def confirm(self, question: str) -> bool:
        choice = self._runner.request_decision(
            question, ["Sí", "No"], kind="confirm", initial_index=0
        )
        if choice is None:
            return False
        return choice == 0

    def read_lines(self, title: str, lines: list[str]) -> None:
        self._broadcast_lines(title, lines)

    def read_summary_stream(self, title: str, lines: list[str]) -> None:
        self._broadcast_lines(title, lines)

    def _broadcast_lines(self, title: str, lines: list[str]) -> None:
        self.speak(str(title)[:200], interrupt=True)
        for line in list(lines)[:40]:
            text = str(line).strip()
            if text:
                self.speak(text[:600])


@dataclass
class AuthoritativeMatch:
    """One authoritative match for one room."""

    room_id: str
    room_name: str
    controller_id: str
    controller_name: str
    settings: MatchSettings
    broadcast: Broadcast
    record_event: RecordEvent
    record_state: RecordState
    finish: Finish
    turn_seconds: int = 60
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _condition: threading.Condition = field(default_factory=threading.Condition)
    _pending: dict[str, _PendingDecision] = field(default_factory=dict)
    _controller_lock: threading.Lock = field(default_factory=threading.Lock)
    _controller_id: str = ""
    _controller_name: str = ""
    engine: MatchEngine | None = None

    def __post_init__(self) -> None:
        self._controller_id = self.controller_id
        self._controller_name = self.controller_name

    @property
    def current_controller_id(self) -> str:
        with self._controller_lock:
            return self._controller_id

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"af-match-{self.room_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def set_controller(self, client_id: str, name: str) -> None:
        with self._controller_lock:
            self._controller_id = client_id
            self._controller_name = name
        if self.engine is not None:
            self.engine.state.auto = False
            self.engine.state.muted = False
        self.broadcast(frame(
            MSG.MATCH_PROMPT,
            room_id=self.room_id,
            prompt_id="controller",
            kind="controller",
            controller_id=client_id,
            controller_name=name,
            title=f"{name} toma las decisiones del partido.",
            options=[],
            timeout=0,
        ))

    def submit_decision(self, client_id: str, prompt_id: str,
                        choice: int | None) -> bool:
        with self._controller_lock:
            controller = self._controller_id
        if client_id != controller:
            return False
        with self._condition:
            pending = self._pending.get(prompt_id)
            if pending is None or pending.answered:
                return False
            if choice is not None:
                try:
                    index = int(choice)
                except (TypeError, ValueError):
                    return False
                if not 0 <= index < len(pending.options):
                    return False
                pending.choice = index
            else:
                pending.choice = None
            pending.answered = True
            self._condition.notify_all()
            return True

    # -- internals ---------------------------------------------------------
    def _run(self) -> None:
        try:
            home_name = (self.controller_name or "Equipo local")[:32]
            away_name = "Rival de la sala"
            home = create_generic_team(home_name or "Equipo local", 75)
            away = create_generic_team(away_name, 74)
            io = ServerMatchIO(self)
            self.engine = MatchEngine(
                user_team=home,
                rival_team=away,
                io=io,
                settings=self.settings,
                auto=not bool(self._controller_id),
                title=self.room_name or "Partido en línea",
                max_substitutions=5,
                prematch_menu=True,
                rng=random.Random(f"{self.room_id}:{time.time_ns()}"),
            )
            result = self.engine.play()
            goals = tuple(int(value) for value in result.goals[:2])
            text = (
                f"Final del partido autoritativo. {home.name} {goals[0]}, "
                f"{away.name} {goals[1]}."
            )
            if result.abandoned:
                text = "El partido se abandonó. " + text
            self.broadcast(frame(
                MSG.MATCH_END,
                room_id=self.room_id,
                text=text,
                home=home.name,
                away=away.name,
                home_score=goals[0],
                away_score=goals[1],
                abandoned=bool(result.abandoned),
            ))
        except Exception as exc:  # pragma: no cover - defensive server guard
            LOGGER.exception("authoritative match crashed in room %s", self.room_id)
            self.broadcast(frame(
                MSG.MATCH_END, room_id=self.room_id,
                text=f"El partido de la sala terminó con un error: {exc}",
                home="", away="", home_score=0, away_score=0, abandoned=True,
            ))
        finally:
            if not self._stop.is_set():
                self.finish(self.room_id)

    def request_decision(self, title: str, options: list[str], *,
                         kind: str = "choose", initial_index: int = 0) -> int | None:
        options = [str(option)[:200] for option in options][:40]
        if self._stop.is_set():
            return 0
        if not options:
            return None
        with self._controller_lock:
            controller_id = self._controller_id
            controller_name = self._controller_name
        if not controller_id:
            return None if kind == "choose" else 0
        prompt_id = uuid.uuid4().hex[:12]
        pending = _PendingDecision(options=options)
        with self._condition:
            self._pending[prompt_id] = pending
        self.broadcast(frame(
            MSG.MATCH_PROMPT,
            room_id=self.room_id,
            prompt_id=prompt_id,
            kind=kind,
            title=str(title)[:600],
            options=options,
            initial_index=max(0, min(initial_index, len(options) - 1)),
            controller_id=controller_id,
            controller_name=controller_name,
            timeout=DECISION_TIMEOUT,
        ))
        deadline = time.monotonic() + DECISION_TIMEOUT
        with self._condition:
            while not pending.answered and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=min(remaining, 0.5))
            self._pending.pop(prompt_id, None)
            answered = pending.answered
            choice = pending.choice
        if self._stop.is_set():
            return 0
        if not answered:
            self.broadcast(frame(
                MSG.MATCH_NARRATION,
                room_id=self.room_id,
                text=f"{controller_name} no respondió; se aplica la opción por defecto.",
                interrupt=False,
            ))
            return 0
        return choice
