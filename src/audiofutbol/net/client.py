"""Client-side TCP connection with a thread-safe incoming message queue."""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .match_sync import MAX_MATCH_EVENTS, empty_state, event_line
from .protocol import MSG, ProtocolError, frame, recv_json, send_json


class ConnectionError(RuntimeError):
    """Raised when the client cannot connect or the link drops."""


@dataclass
class ServerEntry:
    """One saved server.  Extra ports are kept for advanced self-hosted setups."""

    name: str
    host: str
    port: int
    ports: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        primary = _valid_port(self.port, 3840)
        self.port = primary
        extras = [_valid_port(value, primary) for value in self.ports]
        self.ports = [primary] + [value for value in extras if value != primary]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "ports": list(self.ports),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServerEntry":
        primary = _valid_port(raw.get("port"), 3840)
        extras = raw.get("ports")
        if not isinstance(extras, list):
            extras = []
        return cls(
            str(raw.get("name") or "Servidor"),
            str(raw.get("host") or "127.0.0.1"),
            primary,
            [_valid_port(value, primary) for value in extras],
        )

    def label(self) -> str:
        ports = ", ".join(str(value) for value in self.ports)
        return f"{self.name} ({self.host}:{ports})"


def _valid_port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1 <= port <= 65535 else fallback


class ServerDirectory:
    """Saved server list (names/IPs/ports are not secret, so plain JSON)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[ServerEntry] = []
        self._player_name = "Jugador"
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self._entries = [ServerEntry.from_dict(item) for item in raw.get("servers", [])]
            self._player_name = str(raw.get("player_name") or "Jugador")[:24]
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            self._entries = []
            self._player_name = "Jugador"

    def save(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "player_name": self._player_name,
                    "servers": [entry.to_dict() for entry in self._entries],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def player_name(self) -> str:
        return self._player_name

    def set_player_name(self, name: str) -> str:
        self._player_name = str(name).strip()[:24] or "Jugador"
        self.save()
        return self._player_name

    def all(self) -> list[ServerEntry]:
        return list(self._entries)

    def add(self, entry: ServerEntry) -> None:
        self._entries = [item for item in self._entries if item.name.casefold() != entry.name.casefold()]
        self._entries.append(entry)
        self.save()

    def remove(self, name: str) -> bool:
        before = len(self._entries)
        self._entries = [item for item in self._entries if item.name.casefold() != name.casefold()]
        self.save()
        return len(self._entries) != before

    def get(self, name: str) -> Optional[ServerEntry]:
        return next((item for item in self._entries if item.name.casefold() == name.casefold()), None)


class OnlineClient:
    """Non-blocking view of a TCP connection; Pygame stays on the main thread."""

    def __init__(self) -> None:
        self._socket: Optional[socket.socket] = None
        self._inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lock = threading.RLock()
        self._receiver: Optional[threading.Thread] = None
        self.client_id = ""
        self.server_name = ""
        self.motd = ""
        self.connected = False
        self.players: list[dict[str, Any]] = []
        self.rooms: list[dict[str, Any]] = []
        self.friends: list[str] = []
        self.blocks: list[str] = []
        self.room: Optional[dict[str, Any]] = None
        self.music: dict[str, Any] = {}
        self.match_events: list[dict[str, Any]] = []
        self.match_state: dict[str, Any] = empty_state("")
        self.announced_match_events: set[str] = set()
        self.match_authoritative = False
        self.match_active = False
        self.match_prompt: dict[str, Any] | None = None
        self.match_end: dict[str, Any] | None = None
        self.match_hud = ""
        self.uptime = 0
        self.last_restart = ""

    def connect(self, host: str, port: int, name: str, timeout: float = 5.0) -> None:
        self.close()
        self.match_events = []
        self.match_state = empty_state("")
        self.announced_match_events.clear()
        self.match_authoritative = False
        self.match_active = False
        self.match_prompt = None
        self.match_end = None
        self.match_hud = ""
        try:
            connection = socket.create_connection((host, int(port)), timeout=timeout)
            connection.settimeout(None)
        except OSError as exc:
            raise ConnectionError(f"No se pudo conectar a {host}:{port}: {exc}") from exc
        try:
            send_json(connection, frame(MSG.HELLO, name=name, client="AudioFutbol/0.18"))
        except (OSError, ProtocolError) as exc:
            connection.close()
            raise ConnectionError("El servidor rechazó el saludo inicial.") from exc
        with self._lock:
            self._socket = connection
            self.connected = True
        self._receiver = threading.Thread(
            target=self._receive_loop, args=(connection,), daemon=True,
            name="af-net-receiver",
        )
        self._receiver.start()

    def _receive_loop(self, connection: socket.socket) -> None:
        try:
            while True:
                message = recv_json(connection)
                self._apply_state(message)
                self._inbox.put(message)
        except (OSError, ProtocolError):
            pass
        finally:
            with self._lock:
                self.connected = False
                self._socket = None
            self._inbox.put(frame(MSG.NOTICE, text="Conexión con el servidor perdida.",
                                  kind="disconnected"))

    def _apply_state(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == MSG.WELCOME:
            self.client_id = str(message.get("client_id") or "")
            self.server_name = str(message.get("server_name") or "")
            self.motd = str(message.get("motd") or "")
            self.uptime = int(message.get("uptime") or 0)
            self.last_restart = str(message.get("last_restart") or "")
        elif kind == MSG.PLAYER_LIST:
            self.players = list(message.get("players") or [])
        elif kind == MSG.ROOM_LIST:
            self.rooms = list(message.get("rooms") or [])
        elif kind == MSG.FRIEND_LIST:
            self.friends = [str(item) for item in message.get("friends") or []]
        elif kind == MSG.BLOCK_LIST:
            self.blocks = [str(item) for item in message.get("blocks") or []]
        elif kind == MSG.ROOM_UPDATE:
            self.room = dict(message.get("room") or {})
        elif kind == MSG.MUSIC_STATE:
            self.music = dict(message.get("music") or {})
        elif kind == MSG.MATCH_START:
            self.match_events = []
            self.announced_match_events.clear()
            self.match_state = empty_state(str(message.get("room_id") or ""))
            self.match_authoritative = True
            self.match_active = True
            self.match_prompt = None
            self.match_end = None
            self.match_hud = "Partido autoritativo del servidor iniciado."
        elif kind == MSG.MATCH_STOP:
            self.match_active = False
            self.match_prompt = None
            self.match_hud = ""
        elif kind == MSG.MATCH_PROMPT:
            prompt = dict(message)
            if str(prompt.get("kind") or "") == "controller":
                self.match_prompt = None
            else:
                self.match_prompt = prompt
        elif kind == MSG.MATCH_HUD:
            self.match_hud = str(message.get("status") or "")[:300]
        elif kind == MSG.MATCH_END:
            self.match_end = dict(message)
            self.match_active = False
            self.match_prompt = None
        elif kind == MSG.MATCH_STATE:
            state = dict(message.get("state") or {})
            self.match_state = state
            self.match_events = [
                dict(item) for item in (state.get("events") or [])
            ][-MAX_MATCH_EVENTS:]
        elif kind == MSG.MATCH_EVENT:
            event = dict(message.get("event") or {})
            if event:
                event_id = str(event.get("id") or "")
                if not any(str(item.get("id")) == event_id for item in self.match_events):
                    self.match_events.append(event)
                    self.match_events[:] = self.match_events[-MAX_MATCH_EVENTS:]
                self.match_state["events"] = list(self.match_events)
        elif kind == MSG.MATCH_RESET:
            room_id = str(message.get("room_id") or "")
            self.match_events = []
            self.match_state = empty_state(room_id)
            self.match_state["reset_text"] = str(message.get("text") or "")
            self.announced_match_events.clear()
            self.match_active = False
            self.match_authoritative = False
            self.match_prompt = None
            self.match_end = None
            self.match_hud = ""

    def send(self, message_type: str, **fields: Any) -> bool:
        with self._lock:
            connection = self._socket
            if connection is None or not self.connected:
                return False
            try:
                send_json(connection, frame(message_type, **fields))
                return True
            except (OSError, ProtocolError):
                self.connected = False
                return False

    # -- shared match events -----------------------------------------------
    def send_match_event(self, kind: str, text: str = "", **fields: Any) -> bool:
        """Share one local event (goal, card, chance...) with the current room."""

        return self.send(MSG.MATCH_EVENT, kind=kind, text=text, **fields)

    def send_match_state(self, **fields: Any) -> bool:
        """Share this client's local scoreboard snapshot with the current room."""

        return self.send(MSG.MATCH_STATE, **fields)

    def reset_match(self) -> bool:
        """Ask the server to clear the room's shared match log (host only)."""

        return self.send(MSG.MATCH_RESET)

    def send_match_decision(self, prompt_id: str,
                            choice: int | None) -> bool:
        """Answer a decision requested by the server-authoritative engine."""

        return self.send(MSG.MATCH_DECISION, prompt_id=prompt_id, choice=choice)

    def match_lines(self) -> list[str]:
        """Accessible one-line descriptions of everything the room has seen."""

        return [event_line(event) for event in self.match_events]

    def poll(self, timeout: float = 0.0) -> Optional[dict[str, Any]]:
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while True:
            message = self.poll()
            if message is None:
                return messages
            messages.append(message)

    def ping(self, timeout: float = 2.0) -> Optional[float]:
        """Ping/lag in milliseconds (client-server RTT), or None on timeout."""
        sent_at = time.time()
        token = uuid.uuid4().hex[:8]
        if not self.send(MSG.PING, sent_at=sent_at, token=token):
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.poll(max(0.0, deadline - time.monotonic()))
            if message is None:
                continue
            if message.get("type") == MSG.PONG and message.get("token") == token:
                self.uptime = int(message.get("uptime") or self.uptime)
                return max(0.0, (time.time() - sent_at) * 1000)
            # Keep other events for the scene instead of discarding them.
            self._inbox.put(message)
        return None

    def status(self, timeout: float = 2.0) -> Optional[dict[str, Any]]:
        """Ask for F1 data (uptime and last restart)."""
        token = uuid.uuid4().hex[:8]
        if not self.send(MSG.PING, token=token, want_status=True):
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.poll(max(0.0, deadline - time.monotonic()))
            if message is None:
                continue
            if message.get("type") == MSG.PONG and message.get("token") == token:
                self.last_restart = str(message.get("last_restart") or self.last_restart)
                return {
                    "uptime": int(message.get("uptime") or 0),
                    "last_restart": self.last_restart,
                    "server_name": str(message.get("server_name") or self.server_name),
                }
            self._inbox.put(message)
        return None

    def close(self) -> None:
        with self._lock:
            connection = self._socket
            self._socket = None
            self.connected = False
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        while not self._inbox.empty():
            try:
                self._inbox.get_nowait()
            except queue.Empty:
                break
