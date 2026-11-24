"""Accessible menu for coins, team unlocks and the player transfer market."""

from __future__ import annotations

import time
from typing import Any

from audiofutbol.data.csv_loader import load_club_players, read_squad_csv
from audiofutbol.data.economy import PREMIUM_TEAMS
from audiofutbol.domain.text import normalize_text
from audiofutbol.scenes.base import GameContext


def _club_display_names(ctx: GameContext) -> dict[str, str]:
    """Map normalized club names to their original spelling from the CSV files."""
    names: dict[str, str] = {}
    if not ctx.paths.clubs_dir.is_dir():
        return names
    for path in sorted(ctx.paths.clubs_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".csv", ".tsv"):
            continue
        try:
            for team_name, _player, _pos, _ovr, _num in read_squad_csv(path):
                names.setdefault(normalize_text(team_name).strip(), team_name)
        except Exception:
            continue
    return names


def _history_lines(ctx: GameContext, economy: Any) -> list[str]:
    lines: list[str] = []
    for entry in economy.ledger(limit=30):
        try:
            stamp = time.strftime("%d/%m/%Y %H:%M", time.localtime(float(entry.get("at", 0))))
        except (TypeError, ValueError, OSError):
            stamp = "fecha desconocida"
        amount = int(entry.get("amount", 0))
        sign = "+" if amount >= 0 else ""
        lines.append(f"{stamp}. {entry.get('reason', 'movimiento')}. {sign}{amount} monedas.")
    return lines or ["Todavía no hay movimientos de monedas."]


def run_economy_menu(ctx: GameContext) -> None:
    """Show the encrypted vault economy: balance, unlocks and transfers."""
    economy = ctx.economy
    if economy is None:
        ctx.ui.speak("La economía no está disponible en esta instalación.", interrupt=True)
        return
    clubs = load_club_players(ctx.paths)
    display = _club_display_names(ctx)
    available = sorted(clubs)
    if not available:
        ctx.ui.speak("No hay clubes disponibles para fichar jugadores.", interrupt=True)

    while True:
        ctx.ui.speak(economy.summary(), interrupt=False)
        choice = ctx.ui.menu(
            "Mi equipo, monedas y fichajes",
            [
                "Ver saldo y movimientos",
                "Desbloquear equipo",
                "Fichar jugador",
                "Ver jugadores fichados",
                "Volver",
            ],
        )
        if choice is None or choice == 4:
            return

        if choice == 0:
            ctx.ui.reader("Movimientos de monedas", _history_lines(ctx, economy))
            continue

        if choice == 1:
            locked = [name for name in PREMIUM_TEAMS if not economy.is_team_unlocked(name)]
            if not locked:
                ctx.ui.speak("Ya todos los equipos están desbloqueados.", interrupt=True)
                continue
            options = [
                f"{name}. {economy.unlock_cost(name)} monedas" for name in locked
            ] + ["Volver"]
            pick = ctx.ui.menu("Equipos bloqueados", options)
            if pick is None or pick >= len(locked):
                continue
            team = locked[pick]
            if ctx.ui.confirm(f"Desbloquear {team} por {economy.unlock_cost(team)} monedas?"):
                ok, message = economy.unlock_team(team)
                ctx.ui.speak(message, interrupt=True)
                ctx.audio.play_sfx("aceptar" if ok else "volver")
            continue

        if choice == 2:
            if not available:
                continue
            team_options = [
                display.get(key, key.replace("_", " ")) for key in available
            ] + ["Volver"]
            team_pick = ctx.ui.menu("Elige un club para fichar", team_options)
            if team_pick is None or team_pick >= len(available):
                continue
            key = available[team_pick]
            team_name = display.get(key, key.replace("_", " "))
            pool = [player.to_dict() for player in clubs[key].values()]
            offers = economy.market_players(pool, team_name, limit=25)
            if not offers:
                ctx.ui.speak(f"Ya tienes todos los jugadores de {team_name}.", interrupt=True)
                continue
            offer_labels = [
                f"{economy.player_name(item)}, calidad {economy.player_quality(item)}. "
                f"{economy.player_price(item)} monedas"
                for item in offers
            ] + ["Volver"]
            pick = ctx.ui.menu(f"Jugadores disponibles en {team_name}", offer_labels)
            if pick is None or pick >= len(offers):
                continue
            player = offers[pick]
            if ctx.ui.confirm(
                f"Fichar a {economy.player_name(player)} por {economy.player_price(player)} monedas?"
            ):
                ok, message = economy.buy_player(team_name, player)
                ctx.ui.speak(message, interrupt=True)
                ctx.audio.play_sfx("aceptar" if ok else "volver")
            continue

        if choice == 3:
            if not available:
                continue
            team_options = [
                display.get(key, key.replace("_", " ")) for key in available
            ] + ["Volver"]
            team_pick = ctx.ui.menu("Elige un club para ver tus fichajes", team_options)
            if team_pick is None or team_pick >= len(available):
                continue
            key = available[team_pick]
            team_name = display.get(key, key.replace("_", " "))
            owned = economy.owned_players(team_name)
            lines = [
                f"{economy.player_name(item)}, calidad {economy.player_quality(item)}, "
                f"dorsal {item.get('number', item.get('numero', 0))}"
                for item in owned
            ] or ["Todavía no has fichado jugadores en este club."]
            ctx.ui.reader(f"Tus jugadores en {team_name}", lines)
