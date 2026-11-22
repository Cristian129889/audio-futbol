"""World Cup hub scene (new, continue, and tournament navigation)."""

from __future__ import annotations

import random

from audiofutbol.domain.competitions.world_cup import GROUP_LETTERS, WorldCup
from audiofutbol.scenes.base import GameContext
from audiofutbol.scenes.match_scene import run_tournament_match
from audiofutbol.ui.widgets import squad_lines


def _finish_world_cup(ctx: GameContext, wc: WorldCup) -> None:
    assert wc.podium is not None
    champ, runner_up, third = wc.podium
    ctx.audio.play_theme("musica_fanfarria", volume=0.8)
    ctx.audio.play_sfx("ovacion")
    ctx.ui.speak(
        f"¡{champ.name} es campeón del mundo! Subcampeón: {runner_up.name}. "
        f"Tercer lugar: {third.name}."
    )
    top = wc.top_scorers(1)
    if top:
        ctx.ui.speak("Máximo goleador: " + top[0])
    if champ is wc.user_team:
        ctx.ui.speak("¡Enhorabuena, has ganado el Mundial!")
    ctx.saves.delete(WorldCup.KEY)
    ctx.ui.reader(
        "Resumen final del Mundial",
        [
            f"Campeón: {champ.name}",
            f"Subcampeón: {runner_up.name}",
            f"Tercer lugar: {third.name}",
        ]
        + wc.top_scorers(10),
    )
    ctx.audio.play_menu_music()


def run_world_cup_hub(ctx: GameContext, wc: WorldCup) -> None:
    while True:
        if wc.champion is not None:
            _finish_world_cup(ctx, wc)
            return

        phase = wc.phase_name()
        idx = wc.user_match_index()
        ops: list[str] = []
        actions: list[str] = []

        if idx is not None:
            a, b = wc.pairs[idx]
            rival = b if a is wc.user_team else a
            ops.append(f"Jugar: {phase}, contra {rival.name}")
            actions.append("jugar")
            ops.append("Simular mi partido")
            actions.append("sim_mio")
        else:
            ops.append(f"Simular: {phase}")
            actions.append("sim_fase")
            ops.append("Simular hasta el final del Mundial")
            actions.append("sim_todo")

        if wc.phases[wc.phase_idx][1] == "g" or not wc.user_eliminated:
            ops.append("Ver tabla de mi grupo")
            actions.append("mi_grupo")
        ops.append("Ver todos los grupos")
        actions.append("grupos")
        if wc.group_count == 12 and wc.phases[wc.phase_idx][1] == "g":
            ops.append("Ver los mejores terceros")
            actions.append("terceros")

        ops.extend(
            [
                "Ver resultados de esta fase",
                "Ver el sorteo",
                "Goleadores del torneo",
                "Mi plantilla",
                "Guardar y volver al menú",
            ]
        )
        actions.extend(["resultados", "sorteo", "goleadores", "plantilla", "guardar"])

        status = f"Mundial. {phase}. Tu selección es {wc.user_team.name}."
        if wc.user_eliminated:
            status += " Estás eliminado."
        elif wc.phases[wc.phase_idx][1] == "k3" and wc.user_team in wc.semi_winners:
            status += " Ya estás clasificado a la gran final."
        choice = ctx.ui.menu(status, ops)
        if choice is None:
            if ctx.ui.confirm("¿Volver al menú sin guardar?"):
                return
            continue

        act = actions[choice]
        if act in ("jugar", "sim_mio"):
            run_tournament_match(ctx, wc, simulate=(act == "sim_mio"))
            wc.simulate_remaining(ctx.match_settings())
            notice = wc.close_phase()
            if notice:
                ctx.ui.speak(notice)
            if wc.champion is None:
                ctx.ui.reader("Resultados de la fase", wc.results_lines(phase))
        elif act == "sim_fase":
            wc.simulate_remaining(ctx.match_settings())
            notice = wc.close_phase()
            if notice:
                ctx.ui.speak(notice)
        elif act == "sim_todo":
            while wc.champion is None:
                wc.simulate_remaining(ctx.match_settings())
                wc.close_phase()
        elif act == "mi_grupo":
            grp = wc.group_of(wc.user_team)
            if grp:
                ctx.ui.reader("Tabla de tu grupo", wc.table_lines(grp))
        elif act == "grupos":
            r = ctx.ui.menu(
                "Elige un grupo",
                [f"Grupo {GROUP_LETTERS[i]}" for i in range(wc.group_count)],
            )
            while r is not None:
                ctx.ui.reader(
                    f"Grupo {GROUP_LETTERS[r]}", wc.table_lines(wc.groups[r])
                )
                r = ctx.ui.menu(
                    "Elige un grupo",
                    [f"Grupo {GROUP_LETTERS[i]}" for i in range(wc.group_count)],
                    initial_index=r,
                )
        elif act == "terceros":
            ctx.ui.reader("Mejores terceros de grupo", wc.best_thirds_lines())
        elif act == "resultados":
            seen: list[str] = []
            for ph, *_ in wc.history:
                if ph not in seen:
                    seen.append(ph)
            if not seen:
                ctx.ui.speak("Todavía no se ha jugado ningún partido.")
            else:
                r = ctx.ui.menu("Elige una fase", seen, initial_index=len(seen) - 1)
                if r is not None:
                    ctx.ui.reader(seen[r], wc.results_lines(seen[r]))
        elif act == "sorteo":
            ctx.ui.reader("Sorteo del Mundial", wc.draw_lines)
        elif act == "goleadores":
            ctx.ui.reader(
                "Goleadores del torneo",
                wc.top_scorers(20) or ["Todavía no hay goles"],
            )
        elif act == "plantilla":
            ctx.ui.reader(f"Plantilla de {wc.user_team.name}", squad_lines(wc.user_team))
        elif act == "guardar":
            if ctx.saves.save(WorldCup.KEY, wc):
                ctx.ui.speak("Partida guardada.")
            else:
                ctx.ui.speak("No se pudo guardar la partida.")
            return


def start_new_world_cup(ctx: GameContext) -> None:
    teams = ctx.national_teams
    n = 48 if len(teams) >= 48 else 32 if len(teams) >= 32 else 16 if len(teams) >= 16 else 8
    user_team = ctx.ui.choose_team("Elige tu selección para el Mundial", teams)
    if user_team is None:
        return
    top = sorted(teams, key=lambda t: -t.strength())[:n]
    if user_team not in top:
        top[-1] = user_team
    random.shuffle(top)
    ctx.ui.speak(f"Mundial de {n} selecciones. Se hace el sorteo.")
    ctx.audio.play_sfx("sorteo")
    wc = WorldCup(top, user_team)
    grp = wc.group_of(user_team)
    if grp:
        rivals = ", ".join(t.name for t in grp if t is not user_team)
        letter = GROUP_LETTERS[wc.groups.index(grp)]
        ctx.ui.speak(f"{user_team.name} queda en el grupo {letter}, con {rivals}.")
    run_world_cup_hub(ctx, wc)


def continue_world_cup(ctx: GameContext) -> None:
    loaded = ctx.saves.load(WorldCup.KEY)
    if not isinstance(loaded, WorldCup):
        ctx.ui.speak("No se pudo cargar la partida guardada.")
        return
    run_world_cup_hub(ctx, loaded)
