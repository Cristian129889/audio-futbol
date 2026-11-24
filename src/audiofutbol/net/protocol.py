"""Versioned, length-prefixed JSON messages used by client and server."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

HEADER_SIZE = 4
MAX_PAYLOAD = 2 * 1024 * 1024
PROTOCOL_VERSION = 1


class MSG:
    """Message type constants (kept as strings for readable logs)."""

    HELLO = "hello"
    WELCOME = "welcome"
    ERROR = "error"
    NOTICE = "notice"

    CHAT = "chat"
    DIRECT_MESSAGE = "direct_message"
    MESSAGE_SENT = "message_sent"
    MESSAGE_BLOCKED = "message_blocked"

    PING = "ping"
    PONG = "pong"
    STATUS = "status"

    PLAYER_LIST = "player_list"
    FRIEND_REQUEST = "friend_request"
    FRIEND_ACCEPT = "friend_accept"
    FRIEND_LIST = "friend_list"

    BLOCK = "block"
    UNBLOCK = "unblock"
    BLOCK_LIST = "block_list"

    ROOM_LIST = "room_list"
    ROOM_CREATE = "room_create"
    ROOM_JOIN = "room_join"
    ROOM_LEAVE = "room_leave"
    ROOM_UPDATE = "room_update"
    ROOM_START = "room_start"
    ROOM_ADD_BOT = "room_add_bot"
    ROOM_REMOVE_BOT = "room_remove_bot"
    ROOM_INVITE = "room_invite"
    ROOM_KICK = "room_kick"

    MUSIC_STATE = "music_state"
    MUSIC_PLAY = "music_play"
    MUSIC_PAUSE = "music_pause"
    MUSIC_NEXT = "music_next"
    MUSIC_PREVIOUS = "music_previous"
    MUSIC_VOLUME = "music_volume"
    MUSIC_URL = "music_url"

    # Server-authoritative match: the engine runs on the server and every
    # client receives the same narration, sounds, events and scoreboard.
    MATCH_START = "match_start"
    MATCH_STOP = "match_stop"
    MATCH_PROMPT = "match_prompt"
    MATCH_DECISION = "match_decision"
    MATCH_NARRATION = "match_narration"
    MATCH_SFX = "match_sfx"
    MATCH_AUDIO = "match_audio"
    MATCH_HUD = "match_hud"
    MATCH_END = "match_end"

    # Shared local-match events (used only when there is no authoritative
    # server match running in the room).
    MATCH_EVENT = "match_event"
    MATCH_STATE = "match_state"
    MATCH_RESET = "match_reset"


class ProtocolError(ValueError):
    """Raised when a peer sends an invalid or oversized frame."""


def send_json(connection: socket.socket, payload: dict[str, Any]) -> None:
    """Send one UTF-8 JSON frame with a 4-byte big-endian length header."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_PAYLOAD:
        raise ProtocolError("frame exceeds the maximum allowed size")
    connection.sendall(struct.pack(">I", len(data)) + data)


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(min(remaining, 65536))
        if not chunk:
            raise ProtocolError("connection closed while reading a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_json(connection: socket.socket) -> dict[str, Any]:
    """Read one JSON frame; raises :class:`ProtocolError` for bad input."""
    header = _recv_exact(connection, HEADER_SIZE)
    (size,) = struct.unpack(">I", header)
    if size == 0 or size > MAX_PAYLOAD:
        raise ProtocolError("invalid frame length")
    raw = _recv_exact(connection, size)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("frame is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ProtocolError("frame must contain a string 'type'")
    return value


def frame(message_type: str, **fields: Any) -> dict[str, Any]:
    """Small helper to build a message with the protocol version."""
    return {"type": message_type, "version": PROTOCOL_VERSION, **fields}
