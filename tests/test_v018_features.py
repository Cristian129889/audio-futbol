"""Tests for the v0.18.0 encrypted vault, economy, server and new data."""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from audiofutbol.data.competition_loader import load_competition_data
from audiofutbol.data.economy import EconomyService
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.music_player import MusicPlayer
from audiofutbol.engine.paths import AppPaths
from audiofutbol.net.client import OnlineClient, ServerDirectory, ServerEntry
from audiofutbol.net.protocol import (
    MAX_PAYLOAD,
    MSG,
    ProtocolError,
    frame,
    recv_json,
    send_json,
)
from audiofutbol.net.server import AudioServer, ServerConfig
from audiofutbol.scenes.online import OnlineScene
from audiofutbol.security.cql import CQLStore
from audiofutbol.security.crypto import IntegrityError, chacha20_block, seal, unseal


def test_chacha20_rfc_8439_vector_and_authenticated_encryption() -> None:
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    nonce = bytes.fromhex("000000090000004a00000000")
    block = chacha20_block(key, 1, nonce)
    assert block[:16].hex() == "10f1e7e4d13b5915500fdd1fa32071c4"

    master = b"m" * 32
    payload = seal(b"mensaje secreto", master, b"vault/ns/key")
    assert b"mensaje secreto" not in payload
    assert unseal(payload, master, b"vault/ns/key") == b"mensaje secreto"
    with pytest.raises(IntegrityError):
        unseal(payload[:-1] + bytes([payload[-1] ^ 1]), master, b"vault/ns/key")
    with pytest.raises(IntegrityError):
        unseal(payload, b"x" * 32, b"vault/ns/key")


def test_cql_store_encrypted_sql_documents(tmp_path: Path) -> None:
    key = b"k" * 32
    store = CQLStore(tmp_path / "vault.db", key)
    store.put("economy", "uno", {"coins": 10}, tags=("profile", "rich"))
    store.put("economy", "dos", {"coins": 20}, tags=("profile",))
    assert store.get("economy", "uno") == {"coins": 10}
    assert store.query("economy", tag="rich") == [{"coins": 10}]
    assert store.query("economy", where=lambda v: v["coins"] > 15) == [{"coins": 20}]
    assert sorted(item["coins"] for item in store.all("economy")) == [10, 20]
    assert store.sql("SELECT COUNT(*) FROM vault WHERE ns = 'economy'")[0][0] == 2

    with store.transaction() as connection:
        connection.execute(
            "UPDATE vault SET value = ? WHERE ns = 'economy' AND k = 'uno'",
            (b"cambio malicioso",),
        )
    with pytest.raises(IntegrityError):
        store.get("economy", "uno")
    store.close()


def test_economy_rewards_unlocks_and_transfers(tmp_path: Path) -> None:
    economy = EconomyService.from_base_dir(tmp_path)
    assert economy.coins() == 300
    reward = economy.record_match("River Plate", goals_for=3, goals_against=1, won=True)
    assert reward.coins == 180 + 3 * 12
    assert economy.coins() == 300 + reward.coins
    assert economy.ledger(1)[0]["amount"] == reward.coins

    economy.add_coins(1500, "prueba")
    ok, message = economy.unlock_team("River Plate")
    assert ok and "desbloqueado" in message
    assert economy.is_team_unlocked("River Plate")

    player = {"jugador": "Prueba FC", "calidad": 78, "numero": 9, "posicion": "DEL"}
    price = economy.player_price(player)
    ok, message = economy.buy_player("River Plate", player)
    assert ok and str(price) in message
    assert economy.is_player_owned("River Plate", player)
    assert economy.coins() == 300 + reward.coins + 1500 - 900 - price


def test_server_directory_saves_names_ips_and_ports(tmp_path: Path) -> None:
    path = tmp_path / "servidores.json"
    directory = ServerDirectory(path)
    assert directory.set_player_name("Árbitro") == "Árbitro"
    directory.add(ServerEntry("Casa", "192.168.1.10", 3840, [3840, 3841]))
    reloaded = ServerDirectory(path)
    assert reloaded.player_name() == "Árbitro"
    entry = reloaded.get("casa")
    assert entry is not None
    assert entry.port == 3840
    assert entry.ports == [3840, 3841]
    assert "192.168.1.10:3840, 3841" in entry.label()
    assert reloaded.remove("Casa") is True


def test_protocol_rejects_oversized_frames() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent = b""

        def sendall(self, data: bytes) -> None:
            self.sent += data

    message = frame(MSG.CHAT, text="hola")
    assert message["type"] == MSG.CHAT
    assert message["version"] == 1
    connection = FakeSocket()
    send_json(connection, message)  # type: ignore[arg-type]
    assert connection.sent[:4] == len(connection.sent[4:]).to_bytes(4, "big")
    assert recv_json.__name__ == "recv_json"
    with pytest.raises(ProtocolError):
        send_json(connection, frame(MSG.CHAT, text="x" * (MAX_PAYLOAD + 1)))  # type: ignore[arg-type]


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_online_server_chat_status_rooms_and_friends(tmp_path: Path) -> None:
    config = ServerConfig(name="Servidor de prueba", host="127.0.0.1", port=0, max_players=8)
    server = AudioServer(config, tmp_path / "serverdata")
    server.start()
    port = int(server._socket.getsockname()[1])  # type: ignore[union-attr]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first = OnlineClient()
    second = OnlineClient()
    try:
        first.connect("127.0.0.1", port, "Alpha", timeout=2)
        second.connect("127.0.0.1", port, "Beta", timeout=2)
        assert _wait_for(lambda: len(first.players) == 2 and len(second.players) == 2)

        assert first.ping() is not None
        status = first.status()
        assert status is not None
        assert status["server_name"] == "Servidor de prueba"
        assert "uptime" in status and "last_restart" in status

        first.send(MSG.CHAT, text="hola sala", scope="global")
        received = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not received:
            incoming = second.poll(0.2)
            if incoming and incoming.get("type") == MSG.CHAT:
                received.append(incoming)
        assert received and received[0]["text"] == "hola sala"

        first.send(MSG.ROOM_CREATE, name="Sala Alpha")
        assert _wait_for(lambda: first.room is not None)
        room_id = str((first.room or {})["id"])
        second.send(MSG.ROOM_JOIN, room_id=room_id)
        assert _wait_for(lambda: (second.room or {}).get("id") == room_id)

        first.send(MSG.FRIEND_REQUEST, to="Beta")
        request_seen = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not request_seen:
            incoming = second.poll(0.2)
            if incoming and incoming.get("type") == MSG.FRIEND_REQUEST:
                request_seen = True
        assert request_seen
        second.send(MSG.FRIEND_ACCEPT, sender="Alpha")
        assert _wait_for(lambda: "Beta" in first.friends and "Alpha" in second.friends)

        second.send(MSG.BLOCK, name="Alpha")
        assert _wait_for(lambda: "Alpha" in second.blocks)
        second.drain()
        first.send(MSG.CHAT, text="mensaje bloqueado", scope="global")
        time.sleep(0.2)
        assert not [
            msg for msg in second.drain()
            if msg.get("type") == MSG.CHAT and msg.get("text") == "mensaje bloqueado"
        ]
    finally:
        first.close()
        second.close()
        server.stop()
        thread.join(timeout=2)


def test_online_shared_match_events_state_history_and_reset(tmp_path: Path) -> None:
    config = ServerConfig(name="Servidor de partidos", host="127.0.0.1", port=0,
                          max_players=8)
    server = AudioServer(config, tmp_path / "matchdata")
    server.start()
    port = int(server._socket.getsockname()[1])  # type: ignore[union-attr]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    alpha = OnlineClient()
    beta = OnlineClient()
    newcomer = OnlineClient()

    def next_message(client: OnlineClient, kind: str, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = client.poll(0.05)
            if message and message.get("type") == kind:
                return message
        return None

    try:
        alpha.connect("127.0.0.1", port, "Alpha", timeout=2)
        beta.connect("127.0.0.1", port, "Beta", timeout=2)
        alpha.send(MSG.ROOM_CREATE, name="Sala de partido")
        assert _wait_for(lambda: alpha.room is not None)
        room_id = str((alpha.room or {})["id"])
        beta.send(MSG.ROOM_JOIN, room_id=room_id)
        assert _wait_for(lambda: (beta.room or {}).get("id") == room_id)
        alpha.drain()
        beta.drain()

        assert alpha.send_match_event(
            "goal", "¡Gol de Alpha!", minute=23, team="Local",
            player="Delantero", score="1-0",
        )
        event = next_message(beta, MSG.MATCH_EVENT)
        assert event is not None
        shared = dict(event.get("event") or {})
        assert shared["kind"] == "goal"
        assert shared["sender"] == "Alpha"
        assert shared["text"] == "¡Gol de Alpha!"
        assert shared["minute"] == 23

        assert alpha.send_match_state(
            home="Local", away="Visitante", home_score=1,
            away_score=0, minute=23, status="live",
        )
        state_message = None
        state_deadline = time.monotonic() + 2
        while time.monotonic() < state_deadline and state_message is None:
            candidate = beta.poll(0.05)
            if candidate and candidate.get("type") == MSG.MATCH_STATE:
                if (candidate.get("state") or {}).get("matches"):
                    state_message = candidate
        assert state_message is not None
        state = dict(state_message.get("state") or {})
        assert state["totals"]["goals"] == 1
        assert len(state["matches"]) == 1
        assert state["matches"][0]["sender"] == "Alpha"

        newcomer.connect("127.0.0.1", port, "Nuevo", timeout=2)
        newcomer.send(MSG.ROOM_JOIN, room_id=room_id)
        assert _wait_for(lambda: (newcomer.room or {}).get("id") == room_id)
        history = next_message(newcomer, MSG.MATCH_STATE)
        assert history is not None
        assert len(history["state"]["events"]) == 1
        assert history["state"]["events"][0]["id"] == shared["id"]
        assert _wait_for(lambda: len(newcomer.match_events) == 1)

        assert alpha.reset_match()
        reset = next_message(beta, MSG.MATCH_RESET)
        assert reset is not None and reset["sender"] == "Alpha"
        cleared = next_message(beta, MSG.MATCH_STATE)
        assert cleared is not None and cleared["state"]["events"] == []
        assert _wait_for(
            lambda: not alpha.match_events and not beta.match_events and not newcomer.match_events
        )
    finally:
        alpha.close()
        beta.close()
        newcomer.close()
        server.stop()
        thread.join(timeout=2)


def test_online_text_prompt_uses_active_context() -> None:
    scene = OnlineScene()
    cleared: list[int] = []

    class Window:
        def clear_key_hooks(self) -> None:
            cleared.append(1)

    class UI:
        def input_text(self, title: str, initial: str = "", hint: str = "",
                       on_idle: Any = None) -> str:
            assert title == "Tu nombre en línea"
            assert initial == "Jugador"
            assert "24" in hint
            assert callable(on_idle)
            return "Nuevo nombre"

    hooks: list[int] = []
    scene._ctx = SimpleNamespace(window=Window(), ui=UI())  # type: ignore[assignment]
    scene._install_hooks = lambda ctx: hooks.append(1)  # type: ignore[method-assign]

    assert scene._ask_text("Tu nombre en línea", "Jugador", "De 1 a 24 caracteres.") == "Nuevo nombre"
    assert cleared == [1] and hooks == [1]


def test_online_server_authoritative_match_is_shared(tmp_path: Path) -> None:
    config = ServerConfig(name="Servidor autoritativo", host="127.0.0.1", port=0,
                          max_players=8)
    server = AudioServer(config, tmp_path / "authoritativedata")
    server.start()
    port = int(server._socket.getsockname()[1])  # type: ignore[union-attr]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    alpha = OnlineClient()
    beta = OnlineClient()
    seen: dict[str, list[dict[str, Any]]] = {"Alpha": [], "Beta": []}
    prompts: dict[str, int] = {"Alpha": 0, "Beta": 0}
    stop_workers = threading.Event()

    def collect(client: OnlineClient, name: str) -> None:
        while not stop_workers.is_set():
            message = client.poll(0.01)
            if message is None:
                continue
            seen[name].append(message)
            if (
                message.get("type") == MSG.MATCH_PROMPT
                and str(message.get("controller_id")) == client.client_id
                and message.get("options")
            ):
                prompts[name] += 1
                title = str(message.get("title") or "")
                options = [str(option) for option in message.get("options") or []]
                choice = 0
                if title.startswith("Descanso."):
                    # Option 0 just reopens the substitutions menu after all five
                    # changes have been used; option 1 starts the second half.
                    choice = 1
                elif title.startswith("Pausa") or title.startswith("El partido"):
                    choice = 1 if len(options) > 1 else 0
                client.send_match_decision(
                    str(message.get("prompt_id") or ""), choice
                )

    workers = []
    try:
        alpha.connect("127.0.0.1", port, "Alpha", timeout=2)
        beta.connect("127.0.0.1", port, "Beta", timeout=2)
        alpha.send(MSG.ROOM_CREATE, name="Sala autoritativa")
        assert _wait_for(lambda: alpha.room is not None)
        room_id = str((alpha.room or {})["id"])
        beta.send(MSG.ROOM_JOIN, room_id=room_id)
        assert _wait_for(lambda: (beta.room or {}).get("id") == room_id)
        alpha.drain()
        beta.drain()

        for client, name in ((alpha, "Alpha"), (beta, "Beta")):
            worker = threading.Thread(target=collect, args=(client, name), daemon=True)
            worker.start()
            workers.append(worker)

        alpha.send(
            MSG.ROOM_START,
            turno_seg=120,
            var=False,
            aviso_tiros=False,
            vol_ambiente=0.0,
        )
        started = _wait_for(
            lambda: any(
                msg.get("type") == MSG.MATCH_START for msg in seen["Alpha"]
            ) and any(msg.get("type") == MSG.MATCH_START for msg in seen["Beta"]),
            timeout=10,
        )
        assert started
        assert alpha.match_authoritative and beta.match_authoritative

        finished = _wait_for(
            lambda: any(msg.get("type") == MSG.MATCH_END for msg in seen["Alpha"])
            and any(msg.get("type") == MSG.MATCH_END for msg in seen["Beta"]),
            timeout=90,
        )
        assert finished, "el partido autoritativo no terminó a tiempo"
        stop_workers.set()
        for worker in workers:
            worker.join(timeout=2)

        narration_alpha = [
            msg.get("text") for msg in seen["Alpha"]
            if msg.get("type") == MSG.MATCH_NARRATION
        ]
        narration_beta = [
            msg.get("text") for msg in seen["Beta"]
            if msg.get("type") == MSG.MATCH_NARRATION
        ]
        events_alpha = [
            msg["event"]["id"] for msg in seen["Alpha"]
            if msg.get("type") == MSG.MATCH_EVENT and msg.get("event")
        ]
        events_beta = [
            msg["event"]["id"] for msg in seen["Beta"]
            if msg.get("type") == MSG.MATCH_EVENT and msg.get("event")
        ]
        assert narration_alpha and narration_alpha == narration_beta
        assert events_alpha and events_alpha == events_beta
        assert prompts["Alpha"] >= 1 and prompts["Beta"] == 0
        assert not [
            msg for msg in seen["Alpha"] + seen["Beta"]
            if msg.get("type") == MSG.ERROR
            and "Decisión" in str(msg.get("text") or "")
        ]

        final_alpha = next(
            msg for msg in seen["Alpha"] if msg.get("type") == MSG.MATCH_END
        )
        final_beta = next(
            msg for msg in seen["Beta"] if msg.get("type") == MSG.MATCH_END
        )
        assert final_alpha["text"] == final_beta["text"]
        assert beta.send(MSG.MATCH_RESET)
        error = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and error is None:
            message = beta.poll(0.05)
            if message and message.get("type") == MSG.ERROR:
                error = message
        assert error is not None and "anfitrión" in str(error.get("text") or "")
    finally:
        stop_workers.set()
        for worker in workers:
            worker.join(timeout=2)
        alpha.close()
        beta.close()
        server.stop()
        thread.join(timeout=2)


def test_music_player_local_search_and_invalid_query(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    (paths.music_dir / "Tema de prueba").mkdir(parents=True, exist_ok=True)
    (paths.music_dir / "tema_local.wav").write_bytes(b"RIFF")
    audio = AudioEngine(paths, GameConfig(), init_mixer=False)
    player = MusicPlayer(paths, audio)
    assert any(track.title == "tema local" for track in player.local_tracks())
    ok, message = player.play_query("no es un número")
    assert not ok and "número" in message
    assert "pista cargada" in player.status()


def test_new_club_and_competition_data_is_valid() -> None:
    paths = AppPaths.detect()
    with (paths.clubs_dir / "nuevos_clubes.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 250
    teams: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        teams.setdefault(row["equipo"], []).append(row)
    assert len(teams) == 12
    for name, players in teams.items():
        assert len(players) >= 11, name
        dorsals = [int(player["numero"]) for player in players]
        assert len(set(dorsals)) == len(dorsals), name
        assert all(1 <= number <= 99 for number in dorsals), name
        assert {player["posicion"] for player in players} <= {"POR", "DEF", "MED", "DEL"}
        assert all(55 <= int(player["calidad"]) <= 92 for player in players), name

    expected = {
        "serie_a_2026_27.json": 20,
        "bundesliga_2026_27.json": 18,
        "liga_argentina_2026.json": 18,
        "ligas_historicas.json": 16,
    }
    for filename, count in expected.items():
        data, teams_by_id = load_competition_data(paths, filename)
        assert data is not None and teams_by_id is not None
        assert len(teams_by_id) == count
        assert all(team.players for team in teams_by_id.values())
