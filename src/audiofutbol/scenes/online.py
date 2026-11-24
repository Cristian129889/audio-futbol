"""Online lobby: server directory, chat, rooms, friends and accessible music.

Everything that touches Pygame stays on the main thread.  Network messages are
queued by :class:`OnlineClient` and drained from ``on_idle`` callbacks passed to
the audio UI, so screen-reader announcements and the visual HUD never race the
receiver thread.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pygame

from audiofutbol.engine.music_player import MusicPlayer, Track
from audiofutbol.net.client import ConnectionError, OnlineClient, ServerDirectory, ServerEntry
from audiofutbol.net.protocol import MSG
from audiofutbol.scenes.base import GameContext, Scene

LOGGER = logging.getLogger("audiofutbol.online")


class _Disconnected(Exception):
    """Raised from an idle pump when the server link drops unexpectedly."""


class _MatchEventReady(Exception):
    """Raised from an idle pump when a prompt or the match end arrives."""


class OnlineScene:
    """Self-hostable multiplayer lobby scene.

    The player can save any number of servers, connect with one keystroke, chat
    with ``-`` (global) and ``}`` (room), manage rooms/friends with F3 and open
    the bundled music player with Tab / Shift+Tab.
    """

    def __init__(self) -> None:
        self.client: OnlineClient
        self.directory: Optional[ServerDirectory] = None
        self.player: Optional[MusicPlayer] = None
        self.history: list[str] = []
        self.friend_requests: list[str] = []
        self.room_invitations: list[tuple[str, str, str]] = []
        self._known_players: set[str] = set()
        self._players_seen = False
        self._closing = False
        self._in_music = False
        self._ctx: Optional[GameContext] = None

    # -- lifecycle ---------------------------------------------------------
    def run(self, ctx: GameContext) -> Scene | None:
        self._ctx = ctx
        self.client = ctx.online
        ctx.online_message_handler = self._handle_message
        ctx.window.clear_key_hooks()
        self.directory = ServerDirectory(ctx.paths.server_list_file)
        self.player = MusicPlayer(ctx.paths, ctx.audio, lambda text: ctx.ui.speak(text))
        self._install_hooks(ctx)
        try:
            self._server_menu(ctx)
        except _Disconnected:
            ctx.ui.speak("Conexión perdida con el servidor.", interrupt=True)
            ctx.audio.play_sfx("jugador_desconectado")
        finally:
            self._closing = True
            ctx.online_message_handler = None
            ctx.window.clear_key_hooks()
            # Keep the connection alive: server match narration and room events
            # keep arriving while the player is in another scene.
            if self.player is not None and self.player.audio.music_playing():
                self.player.audio.stop_music(300)
        return None

    def _install_hooks(self, ctx: GameContext) -> None:
        """Contextual F1/F2/F3/Tab shortcuts for the whole online experience."""

        def refresh() -> str:
            return "releer"

        def on_status() -> None:
            self._server_status(ctx)

        def on_ping() -> None:
            self._server_ping(ctx)

        def on_players() -> None:
            self._player_menu(ctx)

        def on_tab() -> None:
            if self._in_music:
                ctx.ui.speak("Ya estás en el reproductor de música.", interrupt=True)
                return
            mods = 0
            try:
                mods = pygame.key.get_mods()
            except Exception:  # pragma: no cover - pygame always provides it
                pass
            direct_search = bool(mods & pygame.KMOD_SHIFT)
            self._in_music = True
            try:
                self._music_menu(ctx, direct_search=direct_search)
            finally:
                self._in_music = False

        ctx.window.register_key_hook(pygame.K_F1, on_status)
        ctx.window.register_key_hook(pygame.K_F2, on_ping)
        ctx.window.register_key_hook(pygame.K_F3, on_players)
        ctx.window.register_key_hook(pygame.K_TAB, on_tab)
        refresh  # keep the helper referenced for readability in this scope

    def _ask_text(self, title: str, initial: str = "",
                  hint: str = "") -> str | None:
        """Text entry without global hooks, so keys like Tab can be typed."""

        ctx = self._ctx
        if ctx is None:
            return None
        ctx.window.clear_key_hooks()
        try:
            return ctx.ui.input_text(title, initial=initial, hint=hint, on_idle=self._pump)
        finally:
            self._install_hooks(ctx)

    # -- network pumping ---------------------------------------------------
    def _pump(self) -> None:
        """Drain the client queue.  Safe to call many times per frame."""
        while True:
            message = self.client.poll()
            if message is None:
                return
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        if self.directory is None:
            return
        kind = message.get("type")
        ui_sfx = {
            MSG.CHAT: "mensaje",
            MSG.DIRECT_MESSAGE: "mensaje",
            MSG.MESSAGE_SENT: "mensaje_enviado",
            MSG.FRIEND_REQUEST: "mensaje",
            MSG.ROOM_INVITE: "mensaje",
        }

        if kind == MSG.NOTICE:
            notice_kind = str(message.get("kind") or "")
            text = str(message.get("text") or "")
            if notice_kind == "disconnected" and not self._closing:
                raise _Disconnected()
            if text:
                self._speak(text, "jugador_desconectado" if notice_kind == "disconnected" else None)
            return

        if kind == MSG.ERROR:
            self._speak(f"Error del servidor: {message.get('text', '')}", "volver")
            return

        if kind == MSG.WELCOME:
            self._players_seen = False
            self._known_players.clear()
            self.history.clear()
            self.friend_requests.clear()
            self.room_invitations.clear()
            self._speak(str(message.get("motd") or "Conectado al servidor."), "jugador_conectado")
            return

        if kind == MSG.PLAYER_LIST:
            players = list(message.get("players") or [])
            names = {str(item.get("name") or "") for item in players if item}
            if not self._players_seen:
                self._known_players = names
                self._players_seen = True
                return
            for name in sorted(names - self._known_players):
                self._speak(f"{name} se ha conectado.", "jugador_conectado")
            for name in sorted(self._known_players - names):
                self._speak(f"{name} se ha desconectado.", "jugador_desconectado")
            self._known_players = names
            return

        if kind == MSG.CHAT:
            scope = "sala" if message.get("scope") == "room" else "global"
            text = f"{message.get('sender', '?')} ({scope}): {message.get('text', '')}"
            self._remember(text)
            self._speak(text, ui_sfx[kind], interrupt=False)
            return

        if kind == MSG.DIRECT_MESSAGE:
            text = f"Mensaje privado de {message.get('sender', '?')}: {message.get('text', '')}"
            self._remember(text)
            self._speak(text, ui_sfx[kind], interrupt=False)
            return

        if kind == MSG.MESSAGE_SENT:
            self._speak(f"Mensaje enviado a {message.get('to', 'la sala')}.",
                        ui_sfx[kind], interrupt=False)
            return

        if kind == MSG.MESSAGE_BLOCKED:
            self._speak(f"No se pudo enviar el mensaje: {message.get('to', '')} te ha bloqueado.",
                        "volver")
            return

        if kind == MSG.FRIEND_REQUEST:
            sender = str(message.get("sender") or "").strip()
            if sender and sender not in self.friend_requests:
                self.friend_requests.append(sender)
            self._speak(str(message.get("text") or f"Solicitud de amistad de {sender}."),
                        ui_sfx[kind])
            return

        if kind == MSG.ROOM_INVITE:
            sender = str(message.get("sender") or "")
            room_id = str(message.get("room_id") or "")
            room_name = str(message.get("room_name") or "una sala")
            entry = (sender, room_id, room_name)
            if entry not in self.room_invitations:
                self.room_invitations.append(entry)
            self._speak(
                f"{sender} te invita a {room_name}. Pulsa F3 para ver la invitación.",
                ui_sfx[kind],
            )
            return

        if kind == MSG.MUSIC_STATE:
            music = dict(message.get("music") or {})
            track = str(music.get("track") or "sin pista")
            state = "en reproducción" if music.get("playing") else "en pausa"
            self._speak(f"La sala tiene {track} {state}.", None, interrupt=False)
            return

        if kind in (
            MSG.MATCH_START, MSG.MATCH_STOP, MSG.MATCH_PROMPT, MSG.MATCH_DECISION,
            MSG.MATCH_NARRATION, MSG.MATCH_SFX, MSG.MATCH_AUDIO, MSG.MATCH_HUD,
            MSG.MATCH_END, MSG.MATCH_EVENT, MSG.MATCH_STATE, MSG.MATCH_RESET,
        ):
            if self._ctx is not None:
                self._ctx.handle_online_match_message(message)
            return

        if kind in (MSG.PLAYER_LIST, MSG.ROOM_LIST, MSG.FRIEND_LIST, MSG.BLOCK_LIST,
                    MSG.ROOM_UPDATE, MSG.PONG, MSG.WELCOME):
            return

    def _remember(self, text: str) -> None:
        self.history.append(text)
        self.history[:] = self.history[-100:]

    def _speak(self, text: str, sound: str | None = None,
               interrupt: bool = True) -> None:
        if not text:
            return
        ctx = getattr(self, "_ctx", None)
        if ctx is not None:
            ctx.ui.speak(text, interrupt=interrupt)
            if sound:
                ctx.audio.play_sfx(sound)

    def _status_line(self) -> str:
        if not self.client.connected:
            return "Sin conexión. Elige un servidor y pulsa Enter para conectar."
        room = str((self.client.room or {}).get("name") or "Sala principal")
        music = self.client.music or {}
        track = str(music.get("track") or "sin música")
        state = "▶" if music.get("playing") else "Ⅱ"
        return (
            f"{self.client.server_name} | {len(self.client.players)} jugadores | "
            f"{room} | Música: {state} {track}"
        )

    # -- authoritative match ----------------------------------------------
    def _authoritative_match(self, ctx: GameContext) -> None:
        """Spectate the server's match and answer decisions when controlling."""

        seen_prompts: set[str] = set()
        ctx.ui.speak(
            "Entrando en el partido autoritativo del servidor. F2 consulta el ping.",
            interrupt=True,
        )

        def pump_or_interrupt() -> None:
            self._pump()
            if not self.client.match_active or self.client.match_prompt is not None:
                raise _MatchEventReady

        while self.client.connected and self.client.match_active:
            self._pump()
            if not self.client.match_active:
                break
            prompt = self.client.match_prompt
            prompt_id = str((prompt or {}).get("prompt_id") or "")
            if prompt and prompt_id not in seen_prompts:
                seen_prompts.add(prompt_id)
                self.client.match_prompt = None
                self._answer_match_prompt(ctx, prompt)
                continue
            status = self.client.match_hud or "Partido autoritativo en curso."
            ctx.window.update_status(status)
            try:
                ctx.window.wait_key(pump_or_interrupt)
            except _MatchEventReady:
                continue

        end = self.client.match_end
        if end is not None:
            text = str(end.get("text") or "El partido en línea terminó.")
            ctx.ui.speak(text, interrupt=True)
        ctx.window.update_status("")
        self.client.match_hud = ""
        self.client.match_end = None
        ctx.ui.menu(
            "Partido en línea terminado",
            ["Volver al vestíbulo"],
            on_idle=self._pump,
        )

    def _answer_match_prompt(self, ctx: GameContext,
                             prompt: dict[str, Any]) -> None:
        """Present one server decision to the controlling client."""

        prompt_id = str(prompt.get("prompt_id") or "")
        title = str(prompt.get("title") or "Decisión del partido")
        options = [str(item) for item in (prompt.get("options") or [])]
        controller_id = str(prompt.get("controller_id") or "")
        controller_name = str(prompt.get("controller_name") or "El anfitrión")
        if controller_id != self.client.client_id:
            ctx.ui.speak(f"Esperando la decisión de {controller_name}. {title}",
                         interrupt=False)
            return
        initial = max(0, min(int(prompt.get("initial_index") or 0), len(options) - 1))
        choice = ctx.ui.menu(
            f"{title} (decisión de {controller_name})",
            options,
            initial_index=initial,
            on_idle=self._pump,
        )
        self.client.send_match_decision(prompt_id, choice)

    # -- small helpers -----------------------------------------------------
    @staticmethod
    def _duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days} día{'s' if days != 1 else ''}")
        if hours:
            parts.append(f"{hours} hora{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minuto{'s' if minutes != 1 else ''}")
        parts.append(f"{secs} segundo{'s' if secs != 1 else ''}")
        return " ".join(parts)

    def _player_name(self) -> str:
        return self.directory.player_name() if self.directory else "Jugador"

    def _select_player(self, ctx: GameContext, title: str = "Elige un jugador",
                       pool: list[dict[str, Any]] | None = None) -> Optional[dict[str, Any]]:
        players = pool if pool is not None else [
            item for item in self.client.players
            if str(item.get("id")) != self.client.client_id
        ]
        if not players:
            ctx.ui.speak("No hay jugadores disponibles.", interrupt=True)
            return None
        labels = [
            f"{item.get('name', '?')}. {item.get('room', 'Sala principal')}"
            for item in players
        ] + ["Volver"]
        choice = ctx.ui.menu(title, labels, on_idle=self._pump)
        if choice is None or choice >= len(players):
            return None
        return players[choice]

    # -- server directory --------------------------------------------------
    def _server_menu(self, ctx: GameContext) -> None:
        self._ctx = ctx
        assert self.directory is not None
        while True:
            self._pump()
            entries = self.directory.all()
            tail = ["Conectar a nueva dirección", "Eliminar servidor guardado",
                    "Cambiar nombre de jugador", "Volver"]
            options = [entry.label() for entry in entries] + tail
            choice = ctx.ui.menu(
                "Servidores de AudioFútbol",
                options,
                on_idle=self._pump,
            )
            if choice is None or choice == len(options) - 1:
                return
            if choice < len(entries):
                self._connect(ctx, entries[choice])
                continue
            action = choice - len(entries)
            if action == 0:
                self._add_server(ctx)
            elif action == 1:
                self._remove_server(ctx)
            elif action == 2:
                new_name = self._ask_text(
                    "Tu nombre en línea", self._player_name(),
                    "De 1 a 24 caracteres. Enter confirma, Escape cancela.",
                )
                if new_name:
                    self.directory.set_player_name(new_name)
                    ctx.ui.speak(f"Tu nombre en línea será {new_name}.", interrupt=True)

    def _add_server(self, ctx: GameContext) -> None:
        assert self.directory is not None
        name = self._ask_text("Nombre del servidor", hint="Ejemplo: Servidor de mi casa.")
        host_raw = self._ask_text(
            "Dirección IP o nombre del equipo",
            hint="Puedes escribir 192.168.1.10 o 192.168.1.10:3840.",
        )
        if not name or not host_raw:
            ctx.ui.speak("Se canceló la entrada del servidor.", interrupt=True)
            return
        ports_raw = self._ask_text(
            "Puerto o puertos", "3840",
            "Separa varios puertos con comas. El primero se usa para conectarse.",
        )
        host = host_raw.strip()
        if ":" in host and host.rsplit(":", 1)[1].strip().isdigit():
            host, inline_port = host.rsplit(":", 1)
            ports_raw = inline_port.strip()
        ports: list[int] = []
        for token in (ports_raw or "").replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                ctx.ui.speak(f"Puerto no válido: {token}.", interrupt=True)
                return
            if 1 <= value <= 65535:
                ports.append(value)
        if not host or not ports:
            ctx.ui.speak("Faltan la dirección o un puerto válido.", interrupt=True)
            return
        entry = ServerEntry(name.strip()[:40], host, ports[0], ports)
        choice = ctx.ui.menu(
            "Guardar servidor",
            ["Guardar y conectar", "Guardar sin conectar", "Cancelar"],
        )
        if choice is None or choice == 2:
            ctx.ui.speak("Servidor no guardado.", interrupt=True)
            return
        self.directory.add(entry)
        ctx.ui.speak(f"Servidor {entry.label()} guardado.", interrupt=True)
        if choice == 0:
            self._connect(ctx, entry)

    def _remove_server(self, ctx: GameContext) -> None:
        assert self.directory is not None
        entries = self.directory.all()
        if not entries:
            ctx.ui.speak("No hay servidores guardados.", interrupt=True)
            return
        options = [entry.label() for entry in entries] + ["Cancelar"]
        choice = ctx.ui.menu("Eliminar servidor guardado", options)
        if choice is not None and choice < len(entries):
            self.directory.remove(entries[choice].name)
            ctx.ui.speak(f"{entries[choice].name} eliminado.", interrupt=True)

    def _connect(self, ctx: GameContext, entry: ServerEntry) -> None:
        ctx.ui.speak(f"Conectando a {entry.label()}...", interrupt=True)
        try:
            self.client.connect(entry.host, entry.port, self._player_name())
        except ConnectionError as exc:
            LOGGER.info("connection failed: %s", exc)
            ctx.ui.speak(str(exc), interrupt=True)
            ctx.audio.play_sfx("volver")
            return
        ctx.audio.play_sfx("jugador_conectado")
        self._lobby(ctx)

    # -- lobby -------------------------------------------------------------
    def _lobby(self, ctx: GameContext) -> None:
        while self.client.connected:
            self._shortcut_pump(ctx)
            if self.client.match_active:
                self._authoritative_match(ctx)
                continue
            in_room = bool(self.client.room)
            options = [
                "Leer chat reciente",
                "Escribir mensaje global (-)",
                "Escribir mensaje de sala (})",
                "Lista de salas y unirse",
                "Crear sala",
                "Mi sala" if in_room else "Entrar en una sala",
                "Reproductor de música (Tab)",
                "Jugadores, amigos e invitaciones (F3)",
                "Estado del servidor (F1)",
                "Ping / lag del servidor (F2)",
                "Desconectar",
            ]
            choice = ctx.ui.menu(
                f"Vestíbulo de {self.client.server_name or 'servidor'}",
                options,
                shortcuts=self._chat_shortcuts(ctx),
                on_idle=self._pump,
            )
            if choice is None:
                self._disconnect(ctx)
                return
            if choice == 0:
                lines = self.history[-50:] or ["Todavía no hay mensajes."]
                ctx.ui.reader("Chat reciente", lines, on_idle=self._pump)
            elif choice == 1:
                self._chat(ctx, "global")
            elif choice == 2:
                self._chat(ctx, "room")
            elif choice == 3:
                self._room_list(ctx)
            elif choice == 4:
                self._create_room(ctx)
            elif choice == 5:
                self._my_room(ctx)
            elif choice == 6:
                self._in_music = True
                try:
                    self._music_menu(ctx)
                finally:
                    self._in_music = False
            elif choice == 7:
                self._player_menu(ctx)
            elif choice == 8:
                self._server_status(ctx)
            elif choice == 9:
                self._server_ping(ctx)
            elif choice == 10:
                self._disconnect(ctx)
                return

    def _shortcut_pump(self, ctx: GameContext) -> None:
        """Refresh the HUD status before opening another menu."""
        ctx.window.update_status(self._status_line())
        self._pump()

    def _chat_shortcuts(self, ctx: GameContext) -> dict[int, Any]:
        def global_chat() -> str:
            self._chat(ctx, "global")
            return "releer"

        def room_chat() -> str:
            self._chat(ctx, "room")
            return "releer"

        return {
            pygame.K_MINUS: global_chat,
            pygame.K_RIGHTBRACKET: room_chat,
        }

    def _disconnect(self, ctx: GameContext) -> None:
        self._closing = True
        self.client.send(MSG.ROOM_LEAVE)
        self.client.close()
        ctx.ui.speak("Desconectado del servidor.", interrupt=True)
        ctx.audio.play_sfx("jugador_desconectado")
        self._closing = False

    def _chat(self, ctx: GameContext, scope: str) -> None:
        if scope == "room" and not self.client.room:
            ctx.ui.speak("Entra en una sala para usar el chat de sala.", interrupt=True)
            return
        label = "global" if scope == "global" else "de la sala"
        text = self._ask_text(
            f"Mensaje {label}",
            hint="Escribe y pulsa Enter para enviar, Escape para cancelar.",
        )
        if not text:
            return
        if self.client.send(MSG.CHAT, text=text, scope=scope):
            self._remember(f"Tú ({label}): {text}")
            ctx.audio.play_sfx("mensaje_enviado")
        else:
            ctx.ui.speak("No se pudo enviar el mensaje.", interrupt=True)

    def _server_status(self, ctx: GameContext) -> None:
        if not self.client.connected:
            ctx.ui.speak("Conéctate a un servidor para consultar su estado.", interrupt=True)
            return
        info = self.client.status()
        if info is None:
            ctx.ui.speak("El servidor no respondió a la consulta de estado.", interrupt=True)
            ctx.audio.play_sfx("volver")
            return
        uptime = self._duration(int(info.get("uptime") or 0))
        text = (
            f"{info.get('server_name', self.client.server_name)}. "
            f"Tiempo encendido: {uptime}. "
            f"Último reinicio: {info.get('last_restart', 'desconocido')} (UTC)."
        )
        ctx.ui.speak(text, interrupt=True)
        ctx.audio.play_sfx("estado_servidor")

    def _server_ping(self, ctx: GameContext) -> None:
        if not self.client.connected:
            ctx.ui.speak("Conéctate a un servidor para medir el ping o lag.", interrupt=True)
            return
        latency = self.client.ping()
        if latency is None:
            ctx.ui.speak("No se recibió respuesta de ping.", interrupt=True)
            ctx.audio.play_sfx("volver")
            return
        ctx.ui.speak(
            f"Ping o lag con {self.client.server_name}: {round(latency)} milisegundos "
            "de ida y vuelta.",
            interrupt=True,
        )
        ctx.audio.play_sfx("ping")

    # -- rooms -------------------------------------------------------------
    def _room_list(self, ctx: GameContext) -> None:
        rooms = list(self.client.rooms)
        if not rooms:
            ctx.ui.speak("No hay salas creadas. Puedes crear una.", interrupt=True)
            return
        labels = [
            f"{room.get('name', '?')}: {room.get('players', 0)} jugadores, "
            f"{room.get('bots', 0)} bots"
            + (" (jugando)" if room.get("in_match") else "")
            for room in rooms
        ] + ["Volver"]
        choice = ctx.ui.menu("Salas disponibles", labels, on_idle=self._pump)
        if choice is not None and choice < len(rooms):
            self.client.send(MSG.ROOM_JOIN, room_id=rooms[choice].get("id", ""))
            ctx.ui.speak(f"Solicitud de entrada enviada a {rooms[choice].get('name')}.",
                         interrupt=True)

    def _create_room(self, ctx: GameContext) -> None:
        name = self._ask_text(
            "Nombre de la nueva sala", f"Sala de {self._player_name()}",
            "Enter crea la sala, Escape cancela.",
        )
        if name:
            self.client.send(MSG.ROOM_CREATE, name=name[:40])
            ctx.ui.speak(f"Creando la sala {name}.", interrupt=True)

    def _my_room(self, ctx: GameContext) -> None:
        room = self.client.room
        if not room:
            ctx.ui.speak("No estás en una sala. Crea una o únete a otra.", interrupt=True)
            return
        bots = int(room.get("bots") or 0)
        is_host = str(room.get("host") or "") == self.client.client_id
        actions: list[tuple[str, str]] = [
            ("Iniciar partido autoritativo del servidor", "start"),
            ("Leer goles, tarjetas y jugadas compartidas", "events"),
            ("Marcador compartido de la sala", "state"),
            (f"Añadir bot. Ahora hay {bots}", "add_bot"),
            ("Quitar bot", "remove_bot"),
            ("Invitar jugador", "invite"),
            ("Expulsar jugador", "kick"),
            ("Reproductor de música (Tab)", "music"),
            ("Chat de sala (})", "chat"),
        ]
        if is_host:
            actions.append(("Reiniciar marcador compartido", "reset"))
        actions.extend([("Salir de la sala", "leave"), ("Volver", "back")])
        choice = ctx.ui.menu(
            f"Mi sala: {room.get('name', '?')}",
            [label for label, _ in actions],
            shortcuts=self._chat_shortcuts(ctx),
            on_idle=self._pump,
        )
        if choice is None:
            return
        action = actions[choice][1]
        if action == "back":
            return
        if action == "start":
            self.client.send(
                MSG.ROOM_START,
                turno_seg=ctx.config.turno_seg,
                var=ctx.config.var,
                aviso_tiros=ctx.config.aviso_tiros,
                vol_ambiente=ctx.config.vol_ambiente,
            )
            ctx.ui.speak(
                "El servidor va a ejecutar el partido. Todos veréis y oiréis "
                "exactamente la misma simulación.",
                interrupt=True,
            )
        elif action == "events":
            self._shared_match_events(ctx)
        elif action == "state":
            self._shared_match_state(ctx)
        elif action == "add_bot":
            self.client.send(MSG.ROOM_ADD_BOT)
        elif action == "remove_bot":
            self.client.send(MSG.ROOM_REMOVE_BOT)
        elif action == "invite":
            target = self._select_player(ctx, "Invitar a tu sala")
            if target:
                self.client.send(MSG.ROOM_INVITE, name=target.get("name", ""))
                ctx.ui.speak(f"Invitación enviada a {target.get('name')}.", interrupt=True)
        elif action == "kick":
            room_name = str(room.get("name") or "")
            pool = [
                player for player in self.client.players
                if str(player.get("room") or "") == room_name
                and str(player.get("id")) != self.client.client_id
            ]
            target = self._select_player(ctx, "Expulsar de tu sala", pool)
            if target:
                self.client.send(MSG.ROOM_KICK, name=target.get("name", ""))
                ctx.ui.speak(f"{target.get('name')} expulsado de la sala.", interrupt=True)
        elif action == "music":
            self._in_music = True
            try:
                self._music_menu(ctx)
            finally:
                self._in_music = False
        elif action == "chat":
            self._chat(ctx, "room")
        elif action == "reset":
            if self.client.reset_match():
                ctx.ui.speak("Marcador compartido reiniciado.", interrupt=True)
                ctx.audio.play_sfx("silbato")
        elif action == "leave":
            self.client.send(MSG.ROOM_LEAVE)
            ctx.ui.speak("Saliste de la sala.", interrupt=True)

    def _shared_match_events(self, ctx: GameContext) -> None:
        lines = self.client.match_lines()
        if not lines:
            ctx.ui.speak("Todavía no hay eventos compartidos en esta sala.", interrupt=True)
            return
        ctx.ui.reader(
            "Goles, tarjetas y jugadas compartidas",
            lines[-100:],
            on_idle=self._pump,
        )

    def _shared_match_state(self, ctx: GameContext) -> None:
        state = self.client.match_state
        totals = dict(state.get("totals") or {})
        matches = list(state.get("matches") or [])
        parts = [
            "Marcador compartido de la sala:",
            f"{totals.get('events', 0)} eventos, {totals.get('goals', 0)} goles, "
            f"{totals.get('yellow_cards', 0)} tarjetas amarillas, "
            f"{totals.get('red_cards', 0)} rojas.",
        ]
        if not matches:
            parts.append("Ningún jugador ha enviado su marcador local todavía.")
        for item in matches:
            home = str(item.get("home") or "Local") or "Local"
            away = str(item.get("away") or "Visitante") or "Visitante"
            parts.append(
                f"{item.get('sender', 'Jugador')}: {home} {item.get('home_score', 0)} "
                f"a {item.get('away_score', 0)} {away}, minuto {item.get('minute', 0)}, "
                f"{item.get('status', 'en juego')}."
            )
        ctx.ui.reader("Marcador compartido", parts, on_idle=self._pump)

    # -- friends and player actions ----------------------------------------
    def _player_menu(self, ctx: GameContext) -> None:
        if not self.client.connected:
            ctx.ui.speak("Conéctate para ver los jugadores en línea.", interrupt=True)
            return
        specials: list[tuple[str, Any]] = []
        if self.friend_requests:
            specials.append(("friends", None))
        if self.room_invitations:
            specials.append(("invites", None))
        players = [
            item for item in self.client.players
            if str(item.get("id")) != self.client.client_id
        ]
        labels: list[str] = []
        for kind, _payload in specials:
            if kind == "friends":
                labels.append(f"Solicitudes de amistad pendientes: {len(self.friend_requests)}")
            else:
                labels.append(f"Invitaciones a salas pendientes: {len(self.room_invitations)}")
        labels.extend(
            f"{item.get('name', '?')}. {item.get('room', 'Sala principal')}"
            + (" (amigo)" if str(item.get("name")) in self.client.friends else "")
            for item in players
        )
        labels.append("Volver")
        choice = ctx.ui.menu("Jugadores conectados. Pulsa F3 para actualizar",
                             labels, on_idle=self._pump)
        if choice is None or choice == len(labels) - 1:
            return
        if choice < len(specials):
            kind, _payload = specials[choice]
            if kind == "friends":
                self._friend_requests_menu(ctx)
            else:
                self._invitations_menu(ctx)
            return
        target = players[choice - len(specials)]
        self._player_actions(ctx, target)

    def _friend_requests_menu(self, ctx: GameContext) -> None:
        names = list(self.friend_requests)
        options = [f"Aceptar a {name}" for name in names] + ["Volver"]
        choice = ctx.ui.menu("Solicitudes de amistad", options, on_idle=self._pump)
        if choice is None or choice >= len(names):
            return
        name = names[choice]
        if self.client.send(MSG.FRIEND_ACCEPT, sender=name):
            self.friend_requests = [item for item in self.friend_requests if item != name]
            ctx.ui.speak(f"Aceptaste a {name} como amigo.", interrupt=True)
            ctx.audio.play_sfx("aceptar")

    def _invitations_menu(self, ctx: GameContext) -> None:
        invites = list(self.room_invitations)
        options = [
            f"{sender} te invita a {room_name}" for sender, _room_id, room_name in invites
        ] + ["Volver"]
        choice = ctx.ui.menu("Invitaciones a salas", options, on_idle=self._pump)
        if choice is None or choice >= len(invites):
            return
        sender, room_id, room_name = invites[choice]
        if self.client.send(MSG.ROOM_JOIN, room_id=room_id):
            self.room_invitations = [item for item in self.room_invitations if item != invites[choice]]
            ctx.ui.speak(f"Entrando en {room_name}, invitación de {sender}.", interrupt=True)

    def _player_actions(self, ctx: GameContext, target: dict[str, Any]) -> None:
        name = str(target.get("name") or "")
        blocked = name in self.client.blocks
        options = [
            "Enviar solicitud de amistad",
            "Invitar a mi sala",
            "Enviar mensaje privado",
            "Bloquear" if not blocked else "Desbloquear",
            "Volver",
        ]
        choice = ctx.ui.menu(f"Acciones para {name}", options, on_idle=self._pump)
        if choice is None or choice == 4:
            return
        if choice == 0:
            sent = self.client.send(MSG.FRIEND_REQUEST, to=name)
            message = f"Solicitud de amistad enviada a {name}." if sent else "No se pudo enviar."
        elif choice == 1:
            sent = self.client.send(MSG.ROOM_INVITE, name=name)
            message = f"Invitación enviada a {name}." if sent else "No se pudo invitar."
        elif choice == 2:
            text = self._ask_text(f"Mensaje privado para {name}",
                                  hint="Enter envía, Escape cancela.")
            if not text:
                return
            sent = self.client.send(MSG.DIRECT_MESSAGE, to=name, text=text)
            message = f"Mensaje enviado a {name}." if sent else "No se pudo enviar."
        else:
            message_type = MSG.UNBLOCK if blocked else MSG.BLOCK
            sent = self.client.send(message_type, name=name)
            message = (
                f"{name} desbloqueado." if blocked else f"{name} bloqueado."
            ) if sent else "No se pudo cambiar el bloqueo."
        ctx.ui.speak(message, interrupt=True)
        ctx.audio.play_sfx("mensaje_enviado" if choice != 3 else "aceptar")

    # -- music player ------------------------------------------------------
    def _music_menu(self, ctx: GameContext, direct_search: bool = False) -> None:
        assert self.player is not None
        if direct_search:
            self._music_search(ctx)
            return
        while True:
            self._shortcut_pump(ctx)
            volume = round(ctx.audio.config.vol_musica * 100)
            options = [
                "Buscar en YouTube y reproducir",
                "Reproducir esta URL",
                "Pausar o reanudar",
                "Avanzar 10 segundos",
                "Retroceder 10 segundos",
                f"Volumen de música: {volume} por ciento. Flechas para ajustar",
                "Leer pista actual",
                "Enviar pista a la sala",
                "Volver",
            ]

            def adjust(index: int, delta: int) -> str | None:
                if index != 5:
                    return None
                current = ctx.audio.config.vol_musica
                message = self.player.set_volume(current + 0.05 * delta)
                ctx.save_config()
                return message

            choice = ctx.ui.menu(
                "Reproductor de música", options, adjust=adjust, on_idle=self._pump
            )
            if choice is None or choice == 8:
                return
            if choice == 0:
                self._music_search(ctx)
            elif choice == 1:
                self._music_url(ctx)
            elif choice == 2:
                message = self.player.toggle_pause()
                ctx.ui.speak(message, interrupt=True)
                if self.client.room:
                    if self.player.paused:
                        self.client.send(MSG.MUSIC_PAUSE)
                    else:
                        self.client.send(MSG.MUSIC_PLAY)
            elif choice == 3:
                ctx.ui.speak(self.player.forward(), interrupt=True)
            elif choice == 4:
                ctx.ui.speak(self.player.rewind(), interrupt=True)
            elif choice == 5:
                ctx.ui.speak(self.player.set_volume(ctx.audio.config.vol_musica + 0.05),
                             interrupt=True)
                ctx.save_config()
            elif choice == 6:
                ctx.ui.speak(self.player.status(), interrupt=True)
            elif choice == 7:
                self._send_music_to_room(ctx)

    def _music_search(self, ctx: GameContext) -> None:
        assert self.player is not None
        query = self._ask_text(
            "Buscar música en YouTube",
            hint="Escribe un título o artista. Enter busca, Escape cancela.",
        )
        if not query:
            return
        results, note = self.player.search(query, limit=8)
        ctx.ui.speak(note, interrupt=True)
        if not results:
            if ctx.ui.confirm("No hay resultados. ¿Quieres pegar una URL?"):
                self._music_url(ctx)
            return
        options = [
            f"{index + 1}. {track.label()}" for index, track in enumerate(results)
        ] + ["Reproducir esta URL", "Volver"]
        choice = ctx.ui.menu("Resultados de música", options, on_idle=self._pump)
        if choice is None or choice == len(results) + 1:
            return
        if choice == len(results):
            self._music_url(ctx)
        else:
            self._play_track(ctx, results[choice])

    def _music_url(self, ctx: GameContext) -> None:
        assert self.player is not None
        url = self._ask_text(
            "Reproducir esta URL",
            hint="Pega una URL de audio o de YouTube. Enter reproduce, Escape cancela.",
        )
        if not url:
            return
        if url.startswith(("www.", "youtu.be/")):
            url = "https://" + url
        ok, message = self.player.play_query(url)
        ctx.ui.speak(message, interrupt=True)
        ctx.audio.play_sfx("aceptar" if ok else "volver")
        if ok:
            self._publish_music(ctx, self.player.tracks[self.player.index])

    def _play_track(self, ctx: GameContext, track: Track) -> None:
        assert self.player is not None
        ok, message = self.player.play(track)
        ctx.ui.speak(message, interrupt=True)
        ctx.audio.play_sfx("aceptar" if ok else "volver")
        if ok:
            self._publish_music(ctx, track)

    def _publish_music(self, ctx: GameContext, track: Track) -> None:
        if self.client.room:
            self.client.send(MSG.MUSIC_PLAY, track=track.title, url=track.url)

    def _send_music_to_room(self, ctx: GameContext) -> None:
        assert self.player is not None
        if not self.client.room:
            ctx.ui.speak("Entra en una sala para compartir música.", interrupt=True)
            return
        url = self._ask_text(
            "URL de la pista para la sala",
            hint="Deja el campo vacío para compartir la pista actual.",
        )
        if url is None:
            return
        track = self.player.tracks[self.player.index] if self.player.index >= 0 else None
        if url:
            if url.startswith(("www.", "youtu.be/")):
                url = "https://" + url
            self.client.send(MSG.MUSIC_URL, url=url, track=url)
        elif track is not None:
            self.client.send(MSG.MUSIC_URL, url=track.url, track=track.title)
        else:
            ctx.ui.speak("No hay ninguna pista cargada.", interrupt=True)
            return
        ctx.ui.speak("Pista compartida con la sala.", interrupt=True)
        ctx.audio.play_sfx("mensaje_enviado")
