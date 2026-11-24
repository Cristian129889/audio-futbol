"""Pygame-free helpers for room-scoped match event formatting.

Online rooms use a server-authoritative engine in ``net/authoritative.py``.
These helpers sanitize and format its structured events (and are also used by
the legacy local-match sharing fallback), so the server and the UI speak the
same language.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

MAX_MATCH_EVENTS = 200
MAX_TEXT_LENGTH = 240
MAX_SMALL_LENGTH = 80
MAX_COLLECTION_ITEMS = 40

# (Spanish label, sound event name in sonidos.txt)
KIND_INFO: dict[str, tuple[str, str]] = {
    "goal": ("gol", "gol"),
    "gol": ("gol", "gol"),
    "yellow_card": ("tarjeta amarilla", "tarjeta"),
    "red_card": ("tarjeta roja", "tarjeta_roja"),
    "card": ("tarjeta", "tarjeta"),
    "substitution": ("cambio", "cambio"),
    "chance": ("ocasión", "balon"),
    "save": ("parada", "parada"),
    "penalty": ("penal", "penal"),
    "var": ("VAR", "var"),
    "whistle": ("silbato", "silbato"),
    "full_time": ("final del partido", "silbato_final"),
    "message": ("mensaje", "mensaje"),
    "play": ("jugada", "balon"),
}


def clean(value: Any, *, max_length: int = MAX_TEXT_LENGTH, depth: int = 0) -> Any:
    """Return a bounded JSON-safe copy of a client-supplied value."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_length]
    if depth >= 3:
        return str(value)[:max_length]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
            clean_key = str(key)[:MAX_SMALL_LENGTH]
            result[clean_key] = clean(
                item, max_length=MAX_SMALL_LENGTH, depth=depth + 1
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            clean(item, max_length=MAX_SMALL_LENGTH, depth=depth + 1)
            for item in list(value)[:MAX_COLLECTION_ITEMS]
        ]
    return str(value)[:max_length]


def normalize_event(
    payload: Mapping[str, Any], *, sender: str, sender_id: str,
    room_id: str,
) -> dict[str, Any]:
    """Build a safe room event from an untrusted client message."""

    kind = str(payload.get("kind") or payload.get("event") or "play").strip().lower()
    kind = kind[:MAX_SMALL_LENGTH] or "play"
    raw_text = str(payload.get("text") or "").strip()[:MAX_TEXT_LENGTH]
    event: dict[str, Any] = {
        "id": str(payload.get("id") or "").strip()[:80]
        or f"{sender_id}-{time.time_ns()}",
        "kind": kind,
        "text": raw_text,
        "minute": clean(payload.get("minute", ""), max_length=MAX_SMALL_LENGTH),
        "team": clean(payload.get("team", ""), max_length=MAX_SMALL_LENGTH),
        "player": clean(payload.get("player", ""), max_length=MAX_SMALL_LENGTH),
        "score": clean(payload.get("score", ""), max_length=MAX_SMALL_LENGTH),
        "sender": sender,
        "sender_id": sender_id,
        "room_id": room_id,
        "at": float(payload.get("at") or time.time()),
    }
    extra = payload.get("extra")
    if isinstance(extra, Mapping):
        event["extra"] = clean(extra)
    return event


def normalize_state(
    payload: Mapping[str, Any], *, sender: str, sender_id: str,
    room_id: str,
) -> dict[str, Any]:
    """Build one client's local scoreboard snapshot for the shared room state."""

    status = str(payload.get("status") or "live").strip().lower()[:MAX_SMALL_LENGTH]
    return {
        "client_id": sender_id,
        "sender": sender,
        "home": clean(payload.get("home", ""), max_length=MAX_SMALL_LENGTH),
        "away": clean(payload.get("away", ""), max_length=MAX_SMALL_LENGTH),
        "home_score": clean(payload.get("home_score", 0), max_length=MAX_SMALL_LENGTH),
        "away_score": clean(payload.get("away_score", 0), max_length=MAX_SMALL_LENGTH),
        "minute": clean(payload.get("minute", 0), max_length=MAX_SMALL_LENGTH),
        "status": status,
        "updated_at": time.time(),
        "room_id": room_id,
    }


def empty_state(room_id: str) -> dict[str, Any]:
    return {
        "room_id": room_id,
        "events": [],
        "matches": [],
        "totals": totals([]),
        "updated_at": time.time(),
    }


def totals(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Aggregate the counts that matter for an accessible shared scoreboard."""

    result = {
        "events": 0,
        "goals": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "cards": 0,
        "substitutions": 0,
        "plays": 0,
    }
    for event in events:
        result["events"] += 1
        kind = str(event.get("kind") or "")
        if kind in ("goal", "gol"):
            result["goals"] += 1
        elif kind == "yellow_card":
            result["yellow_cards"] += 1
        elif kind == "red_card":
            result["red_cards"] += 1
        elif kind == "card":
            result["cards"] += 1
        elif kind == "substitution":
            result["substitutions"] += 1
        else:
            result["plays"] += 1
    result["cards"] += result["yellow_cards"] + result["red_cards"]
    return result


def room_state(
    room_id: str,
    events: Iterable[Mapping[str, Any]],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compose the state broadcast to every client in a room."""

    event_list = list(events)[-MAX_MATCH_EVENTS:]
    return {
        "room_id": room_id,
        "events": event_list,
        "matches": [dict(item) for item in snapshots.values()],
        "totals": totals(event_list),
        "updated_at": time.time(),
    }


def label_for(kind: str) -> str:
    return KIND_INFO.get(kind, KIND_INFO.get(kind.strip().lower(), ("jugada", "balon")))[0]


def sound_for(kind: str) -> str:
    return KIND_INFO.get(kind, KIND_INFO.get(kind.strip().lower(), ("jugada", "balon")))[1]


def event_line(event: Mapping[str, Any]) -> str:
    """Render one shared event as a short accessible line."""

    pieces: list[str] = []
    minute = event.get("minute")
    if minute not in (None, "", 0, "0"):
        pieces.append(f"{minute}'")
    label = label_for(str(event.get("kind") or "play"))
    pieces.append(label)
    player = str(event.get("player") or "").strip()
    team = str(event.get("team") or "").strip()
    who = ", ".join(part for part in (player, team) if part)
    if who:
        pieces.append(who)
    score = str(event.get("score") or "").strip()
    if score:
        pieces.append(f"marcador {score}")
    text = str(event.get("text") or "").strip()
    if text:
        pieces.append(text)
    sender = str(event.get("sender") or "").strip()
    if sender:
        pieces.append(f"por {sender}")
    return " - ".join(pieces)


def announcement(event: Mapping[str, Any], own_sender_id: str = "") -> str:
    """Text spoken when a shared event arrives (empty for your own events)."""

    if str(event.get("sender_id") or "") == own_sender_id:
        return ""
    line = event_line(event)
    return f"En línea: {line}." if line else ""
