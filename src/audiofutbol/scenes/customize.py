"""Tournament customization: swap participants, pick any club/national team (PES/FIFA style)."""

from __future__ import annotations

import random

from audiofutbol.data.club_pool import build_club_pool, fresh_copy
from audiofutbol.domain.models import Team
from audiofutbol.domain.text import normalize_text
from audiofutbol.scenes.base import GameContext

_pool_cache: dict[str, list[Team]] = {}


def get_club_pool(ctx: GameContext) -> list[Team]:
    key = str(ctx.paths.base_dir)
    if key not in _pool_cache:
        _pool_cache[key] = build_club_pool(ctx.paths)
    return _pool_cache[key]


def _norm(team: Team) -> str:
    return normalize_text(team.name).strip()


def pick_from(ctx: GameContext, title: str, candidates: list[Team]) -> Team | None:
    """Choose one team; long club lists are first filtered by country."""
    if not candidates:
        ctx.ui.speak("No hay más equipos disponibles.")
        return None
    countries = sorted({t.country for t in candidates if t.country})
    if len(candidates) > 24 and len(countries) > 1:
        labels = ["Todos los países"] + [
            f"{c}, {sum(1 for t in candidates if t.country == c)} clubes" for c in countries
        ]
        sel = ctx.ui.menu(f"{title}. Elige el país", labels)
        if sel is None:
            return None
        if sel > 0:
            candidates = [t for t in candidates if t.country == countries[sel - 1]]
    ordered = sorted(candidates, key=lambda t: normalize_text(t.name))
    return ctx.ui.choose_team(title, ordered)


def customize_slots(ctx: GameContext, teams_by_id: dict[str, Team]) -> None:
    """Replace official participants in-place, keeping each slot (and its fixed calendar)."""
    pool = get_club_pool(ctx)
    original = dict(teams_by_id)
    while True:
        choice = ctx.ui.menu(
            f"Personalizar participantes. Hay {len(teams_by_id)} clubes",
            [
                "Reemplazar un club por otro de cualquier liga",
                "Restablecer los participantes oficiales",
                "Terminar y continuar",
            ],
        )
        if choice is None or choice == 2:
            return
        if choice == 1:
            teams_by_id.clear()
            teams_by_id.update(original)
            ctx.ui.speak("Participantes oficiales restablecidos.")
            continue
        current = sorted(teams_by_id.items(), key=lambda kv: normalize_text(kv[1].name))
        idx = ctx.ui.menu("Elige el club que sale", [t.name for _, t in current])
        if idx is None:
            continue
        slot_id, leaving = current[idx]
        inside = {_norm(t) for t in teams_by_id.values()}
        options = [t for t in pool if _norm(t) not in inside]
        new = pick_from(ctx, f"Elige el club que entra en lugar de {leaving.name}", options)
        if new is None:
            continue
        clone = fresh_copy(new)
        clone.team_id = slot_id
        clone.group = leaving.group
        teams_by_id[slot_id] = clone
        ctx.ui.speak(f"{clone.name} entra en lugar de {leaving.name}.")


def pick_participants(
    ctx: GameContext, pool: list[Team], size: int, unit: str, use_country: bool
) -> list[Team] | None:
    """Free selection of exactly `size` participants. Returns None if cancelled."""
    chosen: list[Team] = []
    while True:
        left = size - len(chosen)
        choice = ctx.ui.menu(
            f"Participantes: {len(chosen)} de {size}",
            [
                f"Añadir {unit}",
                f"Quitar {unit}",
                f"Completar con los mejores, faltan {left}",
                f"Completar al azar, faltan {left}",
                "Ver participantes elegidos",
                "Empezar el torneo",
                "Cancelar",
            ],
        )
        if choice is None or choice == 6:
            if ctx.ui.confirm("¿Cancelar y volver al menú?"):
                return None
            continue
        names = {_norm(t) for t in chosen}
        free = [t for t in pool if _norm(t) not in names]
        if choice == 0:
            if left <= 0:
                ctx.ui.speak("Ya están todos los participantes. Quita uno para añadir otro.")
                continue
            team = (
                pick_from(ctx, "Añadir", free)
                if use_country
                else ctx.ui.choose_team("Añadir", sorted(free, key=lambda t: normalize_text(t.name)))
            )
            if team is not None:
                chosen.append(fresh_copy(team))
                ctx.ui.speak(f"{team.name} añadido. {len(chosen)} de {size}.")
        elif choice == 1:
            if not chosen:
                ctx.ui.speak("Todavía no has elegido ninguno.")
                continue
            i = ctx.ui.menu("Elige cuál quitar", [t.name for t in chosen])
            if i is not None:
                ctx.ui.speak(f"{chosen.pop(i).name} quitado.")
        elif choice in (2, 3):
            order = (
                sorted(free, key=lambda t: -t.strength())
                if choice == 2
                else random.sample(free, len(free))
            )
            chosen.extend(fresh_copy(t) for t in order[:left])
            ctx.ui.speak(f"Completado. Ahora hay {len(chosen)} de {size}.")
        elif choice == 4:
            ctx.ui.reader("Participantes", [t.name for t in chosen] or ["Todavía no hay ninguno"])
        elif choice == 5:
            if left > 0:
                ctx.ui.speak(f"Faltan {left} participantes.")
            else:
                return chosen


def choose_size(ctx: GameContext, sizes: list[int], unit_plural: str) -> int | None:
    sel = ctx.ui.menu(
        f"Cuántos {unit_plural} participan", [f"{n} {unit_plural}" for n in sizes]
    )
    return None if sel is None else sizes[sel]


def choose_national_field(
    ctx: GameContext, label: str
) -> tuple[list[Team], Team] | None:
    """Official top-N national teams or a fully custom field. Returns (teams, user_team)."""
    pool = ctx.national_teams + ctx.extra_national_teams
    mode = ctx.ui.menu(
        label,
        [
            "Oficial, con las mejores selecciones",
            "Personalizado, eliges cuántas y cuáles selecciones",
        ],
    )
    if mode is None:
        return None
    if mode == 0:
        teams = ctx.national_teams
        n = 48 if len(teams) >= 48 else 32 if len(teams) >= 32 else 16 if len(teams) >= 16 else 8
        user = ctx.ui.choose_team(f"Elige tu selección. {label}", teams)
        if user is None:
            return None
        top = sorted(teams, key=lambda t: -t.strength())[:n]
        if user not in top:
            top[-1] = user
        top_copies = [fresh_copy(t) for t in top]
        user_copy = next(t for t in top_copies if t.name == user.name)
        random.shuffle(top_copies)
        return top_copies, user_copy
    sizes = [n for n in (8, 16, 32, 48) if n <= len(pool)]
    size = choose_size(ctx, sizes, "selecciones")
    if size is None:
        return None
    chosen = pick_participants(ctx, pool, size, "una selección", use_country=False)
    if chosen is None:
        return None
    user = ctx.ui.choose_team("Elige tu selección", chosen)
    if user is None:
        return None
    random.shuffle(chosen)
    return chosen, user
