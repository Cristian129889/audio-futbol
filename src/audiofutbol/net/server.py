"""Self-hostable TCP server: rooms, chat, friends, blocks and music state.

The server has no Pygame dependency, so it can be shipped as its own
``AudioFutbolServer.exe``.  Its settings live in ``server/config.json`` next
to the executable; friends, blocks and restart history are stored in the
encrypted vault under ``server/``.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..data.economy import EconomyService
from ..domain.match.ports import MatchSettings
from ..security.cql import CQLStore
from ..security.crypto import load_or_create_key
from .authoritative import AuthoritativeMatch
from .match_sync import (
    MAX_MATCH_EVENTS,
    empty_state,
    normalize_event,
    normalize_state,
    room_state,
)
from .protocol import MSG, ProtocolError, frame, recv_json, send_json

LOGGER = logging.getLogger("audiofutbol.server")
VERSION = "0.18.0"


def default_data_dir() -> Path:
    """Directory used by the standalone server executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "servidor"
    return Path(__file__).resolve().parents[3] / "servidor"


@dataclass
class ServerConfig:
    name: str = "Servidor de AudioFutbol"
    host: str = "127.0.0.1"
    port: int = 3840
    max_players: int = 32
    motd: str = "Bienvenido al servidor de AudioFutbol."
    passphrase: str = ""

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ServerConfig":
        target = path or (default_data_dir() / "config.json")
        if not target.exists():
            config = cls()
            config.save(target)
            return config
        try:
            raw = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            name=str(raw.get("name") or cls.name),
            host=str(raw.get("host") or cls.host),
            port=_valid_port(raw.get("port"), cls.port),
            max_players=max(2, min(128, int(raw.get("max_players", cls.max_players) or 32))),
            motd=str(raw.get("motd") or cls.motd),
            passphrase=str(raw.get("passphrase") or ""),
        )

    def save(self, path: Optional[Path] = None) -> None:
        target = path or (default_data_dir() / "config.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "max_players": self.max_players,
            "motd": self.motd,
            "passphrase": self.passphrase,
        }


def _valid_port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1 <= port <= 65535 else fallback


@dataclass
class _Client:
    client_id: str
    name: str
    connection: socket.socket
    address: tuple[str, int]
    room_id: Optional[str] = None
    send_lock: threading.Lock = field(default_factory=threading.Lock)

    def send(self, payload: dict[str, Any]) -> None:
        with self.send_lock:
            send_json(self.connection, payload)


@dataclass
class _Room:
    room_id: str
    name: str
    host_id: str
    players: list[str] = field(default_factory=list)
    bots: int = 0
    in_match: bool = False
    music: dict[str, Any] = field(
        default_factory=lambda: {
            "playing": False,
            "track": "Sin pista",
            "url": "",
            "volume": 0.7,
            "position": 0.0,
        }
    )
    match_events: list[dict[str, Any]] = field(default_factory=list)
    match_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    match_runner: Optional["AuthoritativeMatch"] = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.room_id,
            "name": self.name,
            "host": self.host_id,
            "players": len(self.players),
            "bots": self.bots,
            "in_match": self.in_match,
            "music": dict(self.music),
            "match_totals": room_state(self.room_id, self.match_events, self.match_states)["totals"],
        }


class AudioServer:
    """Threaded server.  Call :meth:`start` then :meth:`serve_forever`."""

    def __init__(self, config: Optional[ServerConfig] = None,
                 data_dir: Optional[Path] = None) -> None:
        self.config = config or ServerConfig.load()
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = load_or_create_key(self.data_dir / "vault.key", self.config.passphrase or None)
        self.vault = CQLStore(self.data_dir / "vault.db", key)
        self.economy = EconomyService(self.vault)
        self.started_at = time.time()
        self.last_restart = str(
            self.vault.get("server", "last_restart")
            or datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
        self.vault.put("server", "last_restart", self.last_restart, tags=("server",))
        self._lock = threading.RLock()
        self._clients: dict[str, _Client] = {}
        self._rooms: dict[str, _Room] = {}
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._friends: dict[str, list[str]] = dict(self.vault.get("social", "friends", {}))
        self._blocks: dict[str, list[str]] = dict(self.vault.get("social", "blocks", {}))
        self._requests: dict[str, list[str]] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind((self.config.host, self.config.port))
            self._socket.listen(self.config.max_players)
            self._socket.settimeout(0.5)
            self._running = True
        LOGGER.info("%s listening on %s:%s", self.config.name, self.config.host, self.config.port)

    def serve_forever(self) -> None:
        self.start()
        while self._running:
            try:
                connection, address = self._socket.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._client_loop,
                args=(connection, address),
                name=f"af-client-{address[1]}",
                daemon=True,
            ).start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
            clients = list(self._clients.values())
            runners = [
                room.match_runner for room in self._rooms.values()
                if room.match_runner is not None
            ]
        for runner in runners:
            runner.stop()
        for client in clients:
            try:
                client.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.connection.close()
            except OSError:
                pass
        self.vault.close()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _key(name: str) -> str:
        return " ".join(str(name).strip().casefold().split())

    def _client_by_name(self, name: str) -> Optional[_Client]:
        wanted = self._key(name)
        with self._lock:
            return next((c for c in self._clients.values() if self._key(c.name) == wanted), None)

    def _is_blocked(self, blocker: str, target: str) -> bool:
        return self._key(target) in {self._key(n) for n in self._blocks.get(self._key(blocker), [])}

    def _persist_social(self) -> None:
        self.vault.put("social", "friends", self._friends, tags=("friends",))
        self.vault.put("social", "blocks", self._blocks, tags=("blocks",))

    def _send(self, client: _Client, message_type: str, **fields: Any) -> None:
        try:
            client.send(frame(message_type, **fields))
        except (OSError, ProtocolError):
            LOGGER.debug("failed to send %s to %s", message_type, client.name)

    def _send_error(self, client: _Client, text: str) -> None:
        self._send(client, MSG.ERROR, text=text)

    def _broadcast(self, payload: dict[str, Any], room_id: Optional[str] = None) -> None:
        with self._lock:
            clients = [
                c for c in self._clients.values()
                if room_id is None or c.room_id == room_id
            ]
        for client in clients:
            try:
                client.send(payload)
            except (OSError, ProtocolError):
                continue

    def _player_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": c.client_id,
                    "name": c.name,
                    "room": self._rooms[c.room_id].name if c.room_id in self._rooms else "Sala principal",
                }
                for c in sorted(self._clients.values(), key=lambda item: item.name.casefold())
            ]

    def _room_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [room.public() for room in sorted(self._rooms.values(), key=lambda r: r.name.casefold())]

    def _broadcast_players(self) -> None:
        self._broadcast(frame(MSG.PLAYER_LIST, players=self._player_list()))

    def _broadcast_rooms(self) -> None:
        self._broadcast(frame(MSG.ROOM_LIST, rooms=self._room_list()))

    def _broadcast_room(self, room: _Room) -> None:
        self._broadcast(frame(MSG.ROOM_UPDATE, room=room.public()), room.room_id)

    def _room_of(self, client: _Client) -> Optional[_Room]:
        with self._lock:
            return self._rooms.get(client.room_id) if client.room_id else None

    # -- shared match events -----------------------------------------------
    def _match_state_payload(self, room: _Room) -> dict[str, Any]:
        with self._lock:
            state = room_state(room.room_id, room.match_events, room.match_states)
        return frame(MSG.MATCH_STATE, room_id=room.room_id, state=state)

    def _send_match_history(self, client: _Client, room: _Room) -> None:
        """Give a newly joined client the event log and scoreboard so far."""

        self._send(client, MSG.MATCH_STATE, room_id=room.room_id,
                   state=self._match_state_payload(room)["state"])

    def _broadcast_match_state(self, room: _Room) -> None:
        self._broadcast(self._match_state_payload(room), room.room_id)

    def _forget_player_match(self, room: _Room, client_id: str) -> None:
        with self._lock:
            room.match_states.pop(client_id, None)
            still_there = room.room_id in self._rooms
        if still_there:
            self._broadcast_match_state(room)

    def _reset_room_match(self, room: _Room, sender: str = "el servidor") -> None:
        runner: Optional[AuthoritativeMatch] = None
        with self._lock:
            runner = room.match_runner
            room.match_runner = None
            room.in_match = False
            room.match_events.clear()
            room.match_states.clear()
            state = empty_state(room.room_id)
        if runner is not None:
            runner.stop()
            self._broadcast(
                frame(MSG.MATCH_STOP, room_id=room.room_id, sender=sender,
                      text=f"{sender} detuvo el partido autoritativo."),
                room.room_id,
            )
        self._broadcast(
            frame(MSG.MATCH_RESET, room_id=room.room_id, sender=sender,
                  text=f"{sender} reinició el marcador compartido."),
            room.room_id,
        )
        self._broadcast(
            frame(MSG.MATCH_STATE, room_id=room.room_id, state=state), room.room_id
        )
        self._broadcast_room(room)

    # -- connection handling ----------------------------------------------
    def _client_loop(self, connection: socket.socket, address: tuple[str, int]) -> None:
        connection.settimeout(None)
        client: Optional[_Client] = None
        try:
            greeting = recv_json(connection)
            if greeting.get("type") != MSG.HELLO:
                raise ProtocolError("expected hello")
            if len(self._clients) >= self.config.max_players:
                send_json(connection, frame(MSG.ERROR, text="El servidor está lleno."))
                return
            name = str(greeting.get("name") or "Jugador").strip()[:24] or "Jugador"
            client = _Client(uuid.uuid4().hex[:12], name, connection, address)
            with self._lock:
                self._clients[client.client_id] = client
            self._send(
                client,
                MSG.WELCOME,
                client_id=client.client_id,
                server_name=self.config.name,
                motd=self.config.motd,
                uptime=int(time.time() - self.started_at),
                last_restart=self.last_restart,
            )
            self._broadcast_players()
            self._broadcast_rooms()
            self._send(client, MSG.FRIEND_LIST, friends=self._friends.get(self._key(name), []))
            self._send(client, MSG.BLOCK_LIST, blocks=self._blocks.get(self._key(name), []))

            while True:
                message = recv_json(connection)
                if message.get("version", 1) > 1:
                    self._send_error(client, "Versión de protocolo no compatible.")
                    continue
                self._dispatch(client, message)
        except (OSError, ProtocolError) as exc:
            LOGGER.debug("client %s disconnected: %s", address, exc)
        finally:
            if client is not None:
                self._disconnect(client)

    def _disconnect(self, client: _Client) -> None:
        with self._lock:
            self._clients.pop(client.client_id, None)
            room = self._rooms.get(client.room_id) if client.room_id else None
            if room is not None:
                room.players = [pid for pid in room.players if pid != client.client_id]
                if not room.players or room.host_id == client.client_id:
                    if room.host_id == client.client_id and room.players:
                        room.host_id = room.players[0]
                    if not room.players:
                        self._rooms.pop(room.room_id, None)
        self._broadcast_players()
        self._broadcast_rooms()
        if room is not None and room.room_id in self._rooms:
            self._forget_player_match(room, client.client_id)
            if room.match_runner is not None:
                self._update_match_controller(room)
        try:
            client.connection.close()
        except OSError:
            pass

    # -- dispatch ----------------------------------------------------------
    def _dispatch(self, client: _Client, message: dict[str, Any]) -> None:
        kind = message.get("type")
        handlers = {
            MSG.PING: self._on_ping,
            MSG.CHAT: self._on_chat,
            MSG.DIRECT_MESSAGE: self._on_direct_message,
            MSG.FRIEND_REQUEST: self._on_friend_request,
            MSG.FRIEND_ACCEPT: self._on_friend_accept,
            MSG.BLOCK: self._on_block,
            MSG.UNBLOCK: self._on_unblock,
            MSG.ROOM_CREATE: self._on_room_create,
            MSG.ROOM_JOIN: self._on_room_join,
            MSG.ROOM_LEAVE: self._on_room_leave,
            MSG.ROOM_START: self._on_room_start,
            MSG.ROOM_ADD_BOT: self._on_bot,
            MSG.ROOM_REMOVE_BOT: self._on_bot,
            MSG.ROOM_INVITE: self._on_room_invite,
            MSG.ROOM_KICK: self._on_room_kick,
            MSG.MUSIC_PLAY: self._on_music,
            MSG.MUSIC_PAUSE: self._on_music,
            MSG.MUSIC_NEXT: self._on_music,
            MSG.MUSIC_PREVIOUS: self._on_music,
            MSG.MUSIC_VOLUME: self._on_music,
            MSG.MUSIC_URL: self._on_music,
            MSG.MATCH_EVENT: self._on_match_event,
            MSG.MATCH_STATE: self._on_match_state,
            MSG.MATCH_RESET: self._on_match_reset,
            MSG.MATCH_DECISION: self._on_match_decision,
        }
        handler = handlers.get(kind)
        if handler is None:
            self._send_error(client, f"Mensaje desconocido: {kind}")
            return
        try:
            handler(client, message)
        except (KeyError, TypeError, ValueError) as exc:
            self._send_error(client, f"Mensaje mal formado: {exc}")

    def _on_ping(self, client: _Client, message: dict[str, Any]) -> None:
        self._send(
            client,
            MSG.PONG,
            sent_at=message.get("sent_at", 0),
            token=message.get("token", ""),
            server_time=time.time(),
            uptime=int(time.time() - self.started_at),
            last_restart=self.last_restart,
            server_name=self.config.name,
        )

    def _on_chat(self, client: _Client, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()[:500]
        if not text:
            return
        scope = "room" if message.get("scope") == "room" else "global"
        payload = frame(
            MSG.CHAT,
            sender=client.name,
            sender_id=client.client_id,
            text=text,
            scope=scope,
            at=time.time(),
        )
        with self._lock:
            recipients = [
                other for other in self._clients.values()
                if other.client_id != client.client_id
                and not self._is_blocked(other.name, client.name)
                and (scope != "room" or other.room_id == client.room_id)
            ]
        for other in recipients:
            try:
                other.send(payload)
            except (OSError, ProtocolError):
                continue
        self._send(client, MSG.MESSAGE_SENT, to="sala", text=text)

    def _on_direct_message(self, client: _Client, message: dict[str, Any]) -> None:
        target_name = str(message.get("to") or "").strip()
        text = str(message.get("text") or "").strip()[:500]
        target = self._client_by_name(target_name)
        if target is None:
            self._send_error(client, f"{target_name} no está conectado.")
            return
        if self._is_blocked(target.name, client.name):
            self._send(client, MSG.MESSAGE_BLOCKED, to=target.name, text=text)
            return
        self._send(
            target,
            MSG.DIRECT_MESSAGE,
            sender=client.name,
            sender_id=client.client_id,
            text=text,
            at=time.time(),
        )
        self._send(client, MSG.MESSAGE_SENT, to=target.name, text=text)

    def _on_friend_request(self, client: _Client, message: dict[str, Any]) -> None:
        target_name = str(message.get("to") or "").strip()
        target = self._client_by_name(target_name)
        if target is None:
            self._send_error(client, f"{target_name} no está conectado.")
            return
        if target.client_id == client.client_id:
            return
        with self._lock:
            self._requests.setdefault(self._key(target.name), [])
            pending = self._requests[self._key(target.name)]
            if client.name not in pending:
                pending.append(client.name)
        self._send(
            target,
            MSG.FRIEND_REQUEST,
            sender=client.name,
            text=f"{client.name} quiere ser tu amigo. Usa F3 para aceptar.",
        )
        self._send(client, MSG.NOTICE, text=f"Solicitud de amistad enviada a {target.name}.")

    def _on_friend_accept(self, client: _Client, message: dict[str, Any]) -> None:
        sender_name = str(message.get("sender") or "").strip()
        with self._lock:
            pending = self._requests.get(self._key(client.name), [])
            if sender_name not in pending:
                self._send_error(client, "No hay una solicitud pendiente de ese jugador.")
                return
            pending.remove(sender_name)
            my_key = self._key(client.name)
            their_key = self._key(sender_name)
            mine = self._friends.setdefault(my_key, [])
            theirs = self._friends.setdefault(their_key, [])
            if sender_name not in mine:
                mine.append(sender_name)
            if client.name not in theirs:
                theirs.append(client.name)
            self._persist_social()
        self._send(client, MSG.FRIEND_LIST, friends=self._friends.get(self._key(client.name), []))
        target = self._client_by_name(sender_name)
        if target is not None:
            self._send(target, MSG.FRIEND_LIST, friends=self._friends.get(self._key(target.name), []))
            self._send(target, MSG.NOTICE, text=f"{client.name} aceptó tu solicitud de amistad.")
        self._send(client, MSG.NOTICE, text=f"Ahora eres amigo de {sender_name}.")

    def _on_block(self, client: _Client, message: dict[str, Any]) -> None:
        name = str(message.get("name") or "").strip()
        with self._lock:
            key = self._key(client.name)
            blocks = self._blocks.setdefault(key, [])
            if name not in blocks:
                blocks.append(name)
            self._persist_social()
        self._send(client, MSG.BLOCK_LIST, blocks=list(self._blocks[key]))
        self._send(client, MSG.NOTICE, text=f"{name} bloqueado. No recibirás sus mensajes.")

    def _on_unblock(self, client: _Client, message: dict[str, Any]) -> None:
        name = str(message.get("name") or "").strip()
        with self._lock:
            key = self._key(client.name)
            blocks = self._blocks.get(key, [])
            if name in blocks:
                blocks.remove(name)
            self._persist_social()
        self._send(client, MSG.BLOCK_LIST, blocks=list(self._blocks.get(key, [])))
        self._send(client, MSG.NOTICE, text=f"{name} desbloqueado.")

    def _on_room_create(self, client: _Client, message: dict[str, Any]) -> None:
        name = str(message.get("name") or f"Sala de {client.name}").strip()[:40]
        room = _Room(uuid.uuid4().hex[:8], name, client.client_id, [client.client_id])
        room.music["track"] = str(message.get("track") or "Sin pista")
        with self._lock:
            if client.room_id and client.room_id in self._rooms:
                old = self._rooms[client.room_id]
                old.players = [pid for pid in old.players if pid != client.client_id]
            self._rooms[room.room_id] = room
            client.room_id = room.room_id
        self._broadcast_rooms()
        self._send_room_detail(client, room)
        self._send_match_history(client, room)
        self._send(client, MSG.NOTICE, text=f"Sala creada: {name}.")

    def _send_room_detail(self, client: _Client, room: _Room) -> None:
        self._send(client, MSG.ROOM_UPDATE, room=room.public())

    def _on_room_join(self, client: _Client, message: dict[str, Any]) -> None:
        room_id = str(message.get("room_id") or "")
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                self._send_error(client, "Esa sala ya no existe.")
                return
            if room.in_match:
                self._send_error(client, "La sala ya está jugando.")
                return
            if client.room_id and client.room_id in self._rooms:
                old = self._rooms[client.room_id]
                old.players = [pid for pid in old.players if pid != client.client_id]
            client.room_id = room.room_id
            if client.client_id not in room.players:
                room.players.append(client.client_id)
        self._broadcast_rooms()
        self._broadcast_room(room)
        self._send_room_detail(client, room)
        self._send_match_history(client, room)
        self._send(client, MSG.NOTICE, text=f"Entraste en la sala {room.name}.")

    def _on_room_leave(self, client: _Client, message: dict[str, Any]) -> None:
        with self._lock:
            room = self._rooms.get(client.room_id) if client.room_id else None
            if room is None:
                return
            room.players = [pid for pid in room.players if pid != client.client_id]
            client.room_id = None
            if not room.players:
                self._rooms.pop(room.room_id, None)
            else:
                if room.host_id == client.client_id:
                    room.host_id = room.players[0]
                room.music["playing"] = False
        self._broadcast_rooms()
        if room.room_id in self._rooms:
            self._forget_player_match(room, client.client_id)
            if room.match_runner is not None:
                self._update_match_controller(room)
        self._send(client, MSG.NOTICE, text="Saliste de la sala.")

    def _host_room(self, client: _Client) -> Optional[_Room]:
        room = self._room_of(client)
        if room is None:
            self._send_error(client, "No estás en una sala.")
            return None
        if room.host_id != client.client_id:
            self._send_error(client, "Solo el anfitrión puede hacer eso.")
            return None
        return room

    @staticmethod
    def _match_settings(message: dict[str, Any]) -> MatchSettings:
        """Sanitize the host's gameplay preferences for the server engine."""
        try:
            turn_seconds = int(message.get("turno_seg", 60))
        except (TypeError, ValueError):
            turn_seconds = 60
        try:
            crowd_volume = float(message.get("vol_ambiente", 0.05))
        except (TypeError, ValueError):
            crowd_volume = 0.05
        return MatchSettings(
            turn_seconds=max(10, min(180, turn_seconds)),
            var_enabled=bool(message.get("var", True)),
            shot_notice_enabled=bool(message.get("aviso_tiros", True)),
            crowd_volume=max(0.0, min(1.0, crowd_volume)),
        )

    def _run_authoritative_match(self, room: _Room, client: _Client,
                                 message: dict[str, Any]) -> None:
        self._reset_room_match(room, "el servidor")
        with self._lock:
            room.in_match = True
            controller = next(
                (pid for pid in room.players if pid in self._clients),
                client.client_id,
            )
            controller_name = (
                self._clients[controller].name if controller in self._clients
                else client.name
            )
        runner = AuthoritativeMatch(
            room_id=room.room_id,
            room_name=room.name,
            controller_id=controller,
            controller_name=controller_name,
            settings=self._match_settings(message),
            broadcast=lambda payload: self._broadcast(payload, room.room_id),
            record_event=lambda event: self._record_authoritative_event(room, event),
            record_state=lambda snapshot: self._record_authoritative_state(room, snapshot),
            finish=lambda room_id: self._finish_authoritative_match(room, room_id),
        )
        with self._lock:
            room.match_runner = runner
        self._broadcast(
            frame(
                MSG.MATCH_START,
                room_id=room.room_id,
                controller_id=controller,
                controller_name=controller_name,
                text=(
                    "Partido autoritativo iniciado. El servidor ejecuta el motor y "
                    f"{controller_name} envía las decisiones."
                ),
            ),
            room.room_id,
        )
        self._broadcast_room(room)
        self._broadcast_rooms()
        runner.start()

    def _record_authoritative_event(self, room: _Room,
                                    event: dict[str, Any]) -> None:
        with self._lock:
            room.match_events.append(event)
            room.match_events[:] = room.match_events[-MAX_MATCH_EVENTS:]
            state = room_state(room.room_id, room.match_events, room.match_states)
        self._broadcast(
            frame(MSG.MATCH_EVENT, room_id=room.room_id, event=event), room.room_id
        )
        self._broadcast(
            frame(MSG.MATCH_STATE, room_id=room.room_id, state=state), room.room_id
        )
        self._broadcast_room(room)

    def _record_authoritative_state(self, room: _Room,
                                    snapshot: dict[str, Any]) -> None:
        authoritative = normalize_state(
            snapshot, sender="Servidor", sender_id="servidor", room_id=room.room_id
        )
        with self._lock:
            room.match_states.clear()
            room.match_states["servidor"] = authoritative
            state = room_state(room.room_id, room.match_events, room.match_states)
        self._broadcast(
            frame(MSG.MATCH_STATE, room_id=room.room_id, state=state), room.room_id
        )

    def _finish_authoritative_match(self, room: _Room, room_id: str) -> None:
        with self._lock:
            if room.room_id != room_id:
                return
            room.match_runner = None
            room.in_match = False
        self._broadcast_room(room)
        self._broadcast_rooms()

    def _update_match_controller(self, room: _Room) -> None:
        with self._lock:
            runner = room.match_runner
            if runner is None:
                return
            candidates = [
                self._clients[pid] for pid in room.players if pid in self._clients
            ]
        if not candidates:
            runner.stop()
            return
        chosen = next(
            (c for c in candidates if c.client_id == runner.current_controller_id), None
        )
        if chosen is None:
            chosen = candidates[0]
        runner.set_controller(chosen.client_id, chosen.name)

    def _on_room_start(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._host_room(client)
        if room is None:
            return
        self._run_authoritative_match(room, client, message)

    def _on_bot(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._host_room(client)
        if room is None:
            return
        adding = message.get("type") == MSG.ROOM_ADD_BOT
        room.bots = max(0, min(10, room.bots + (1 if adding else -1)))
        self._broadcast_room(room)
        self._send(client, MSG.NOTICE, text=f"Bots en la sala: {room.bots}.")

    def _on_room_invite(self, client: _Client, message: dict[str, Any]) -> None:
        target_name = str(message.get("name") or "").strip()
        target = self._client_by_name(target_name)
        room = self._room_of(client)
        if target is None:
            self._send_error(client, f"{target_name} no está conectado.")
            return
        if room is None:
            self._send_error(client, "Crea una sala antes de invitar.")
            return
        self._send(
            target,
            MSG.ROOM_INVITE,
            sender=client.name,
            room_id=room.room_id,
            room_name=room.name,
            text=f"{client.name} te invita a {room.name}.",
        )
        self._send(client, MSG.NOTICE, text=f"Invitación enviada a {target.name}.")

    def _on_room_kick(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._host_room(client)
        if room is None:
            return
        target = self._client_by_name(str(message.get("name") or ""))
        if target is None or target.client_id not in room.players:
            self._send_error(client, "Ese jugador no está en tu sala.")
            return
        if target.client_id == client.client_id:
            return
        room.players.remove(target.client_id)
        target.room_id = None
        if room.match_runner is not None:
            self._update_match_controller(room)
        self._send(target, MSG.NOTICE, text=f"{client.name} te expulsó de la sala.")
        self._broadcast_room(room)
        self._broadcast_rooms()

    def _on_music(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._room_of(client)
        if room is None:
            self._send_error(client, "Entra en una sala para controlar la música.")
            return
        kind = message.get("type")
        if kind == MSG.MUSIC_PLAY:
            room.music["playing"] = True
            if message.get("url"):
                room.music["url"] = str(message["url"])[:500]
            if message.get("track"):
                room.music["track"] = str(message["track"])[:120]
        elif kind == MSG.MUSIC_PAUSE:
            room.music["playing"] = False
        elif kind == MSG.MUSIC_VOLUME:
            try:
                room.music["volume"] = max(0.0, min(1.0, float(message.get("volume", 0.7))))
            except (TypeError, ValueError):
                return
        elif kind == MSG.MUSIC_URL:
            room.music["url"] = str(message.get("url") or "")[:500]
            if message.get("track"):
                room.music["track"] = str(message["track"])[:120]
        else:
            return
        self._broadcast(frame(MSG.MUSIC_STATE, room_id=room.room_id, music=dict(room.music)),
                        room.room_id)

    def _on_match_event(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._room_of(client)
        if room is None:
            self._send_error(client, "Entra en una sala para compartir eventos del partido.")
            return
        if room.match_runner is not None:
            return
        event = normalize_event(
            message, sender=client.name, sender_id=client.client_id, room_id=room.room_id
        )
        with self._lock:
            room.match_events.append(event)
            room.match_events[:] = room.match_events[-MAX_MATCH_EVENTS:]
            state = room_state(room.room_id, room.match_events, room.match_states)
        self._broadcast(
            frame(MSG.MATCH_EVENT, room_id=room.room_id, event=event), room.room_id
        )
        self._broadcast(
            frame(MSG.MATCH_STATE, room_id=room.room_id, state=state), room.room_id
        )
        self._broadcast_room(room)

    def _on_match_state(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._room_of(client)
        if room is None or room.match_runner is not None:
            return
        snapshot = normalize_state(
            message, sender=client.name, sender_id=client.client_id, room_id=room.room_id
        )
        with self._lock:
            room.match_states[client.client_id] = snapshot
            state = room_state(room.room_id, room.match_events, room.match_states)
        self._broadcast(
            frame(MSG.MATCH_STATE, room_id=room.room_id, state=state), room.room_id
        )
        self._broadcast_room(room)

    def _on_match_reset(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._host_room(client)
        if room is None:
            return
        self._reset_room_match(room, client.name)

    def _on_match_decision(self, client: _Client, message: dict[str, Any]) -> None:
        room = self._room_of(client)
        if room is None or room.match_runner is None:
            return
        prompt_id = str(message.get("prompt_id") or "")[:80]
        raw_choice = message.get("choice", "invalid")
        choice: Optional[int]
        if raw_choice is None:
            choice = None
        else:
            try:
                choice = int(raw_choice)
            except (TypeError, ValueError):
                self._send_error(client, "Decisión de partido no válida.")
                return
        if not room.match_runner.submit_decision(client.client_id, prompt_id, choice):
            self._send_error(client, "Esa decisión ya no está disponible.")

    # -- F1 / F2 helpers ---------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Payload spoken by the client when F1 is pressed."""
        with self._lock:
            return {
                "server_name": self.config.name,
                "uptime_seconds": int(time.time() - self.started_at),
                "last_restart": self.last_restart,
                "players": len(self._clients),
                "rooms": len(self._rooms),
                "version": VERSION,
            }

    def ping_payload(self) -> dict[str, Any]:
        return {"server_time": time.time(), "uptime": int(time.time() - self.started_at)}


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``AudioFutbolServer.exe`` (no console output)."""
    args = list(sys.argv[1:] if argv is None else argv)
    data_dir = Path(args[0]) if args else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=data_dir / "server.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    config = ServerConfig.load(data_dir / "config.json")
    server = AudioServer(config, data_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("server stopped by user")
    except Exception:
        LOGGER.exception("server crashed")
        return 1
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
