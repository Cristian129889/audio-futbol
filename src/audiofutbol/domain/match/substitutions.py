"""Substitutions (with natural vs cover-role choice and GK protection), fatigue, injuries, time-wasting."""

from __future__ import annotations

from typing import Callable

from audiofutbol.domain.match.ports import MatchIO
from audiofutbol.domain.match.state import MatchState
from audiofutbol.domain.models import POSITION_NAMES_ES, Player


class SubstitutionManager:
    """Manages squad fatigue, injuries, manual/AI substitutions, and time-wasting."""

    def __init__(
        self,
        state: MatchState,
        io: MatchIO,
        crowd_react: Callable[[str, float], None],
        give_card: Callable[[int, Player, str, str | None], None],
    ) -> None:
        self.state = state
        self.io = io
        self._crowd_react = crowd_react
        self._give_card = give_card

    def _speak(self, text: str, interrupt: bool = False) -> None:
        if not self.state.muted:
            self.io.speak(text, interrupt=interrupt)

    def _sfx(self, event: str) -> None:
        if not self.state.muted:
            self.io.play_sfx(event)

    def bench(self, team_idx: int) -> list[Player]:
        on_pitch = {id(p) for p in self.state.xi[team_idx]}
        return [
            p
            for p in self.state.teams[team_idx].players
            if id(p) not in on_pitch
            and id(p) not in self.state.subbed_off[team_idx]
            and id(p) not in self.state.excluded_suspended[team_idx]
        ]

    def eligible_replacements(self, team_idx: int, out_player: Player) -> list[Player]:
        """Return valid bench replacements for `out_player`.

        Prevents subbing a backup goalkeeper in for an outfield player.
        When replacing a goalkeeper, prioritizes backup goalkeepers first.
        """
        bench_players = self.bench(team_idx)
        out_role = self.state.player_role(out_player)
        if out_role != "POR":
            outfield = [p for p in bench_players if p.position != "POR"]
            return sorted(
                outfield, key=lambda p: (p.position != out_role, -p.overall)
            )
        return sorted(
            bench_players, key=lambda p: (p.position != "POR", -p.overall)
        )

    def remaining_subs(self, team_idx: int, human: bool = False) -> int | None:
        limit = self.state.max_substitutions if (human and team_idx == 0) else 5
        if limit is None:
            return None
        return max(0, limit - self.state.substitutions_used[team_idx])

    def player_status_text(self, player: Player) -> str:
        fatigue_pct = int(round(self.state.fatigue.get(id(player), 0.0)))
        role = self.state.player_role(player)
        pos_txt = POSITION_NAMES_ES[player.position]
        if role != player.position:
            pos_txt += f" jugando como {POSITION_NAMES_ES[role]}"
        text = (
            f"{player.name}, {pos_txt}, calidad efectiva {int(round(self.state.effective_overall(player)))}, "
            f"cansancio {fatigue_pct} por ciento"
        )
        if id(player) in self.state.injured:
            text += ", lesionado"
        if self.state.match_yellows.get(id(player), 0):
            text += ", con amarilla"
        return text

    def remaining_subs_text(self, team_idx: int = 0, human: bool = True) -> str:
        rem = self.remaining_subs(team_idx, human=human)
        if rem is None:
            return "Cambios ilimitados"
        if rem == 0:
            return "Ya no te quedan cambios"
        suffix = "" if rem == 1 else "s"
        return f"Te quedan {rem} cambio{suffix}"

    def apply_substitution(
        self,
        team_idx: int,
        out_player: Player,
        in_player: Player,
        play_as_position: str | None = None,
    ) -> None:
        idx = self.state.xi[team_idx].index(out_player)
        self.state.xi[team_idx][idx] = in_player
        self.state.subbed_off[team_idx].add(id(out_player))
        self.state.position_overrides.pop(id(out_player), None)
        if play_as_position and play_as_position != in_player.position:
            self.state.position_overrides[id(in_player)] = play_as_position
        else:
            self.state.position_overrides.pop(id(in_player), None)

        self.state.substitutions_used[team_idx] += 1
        self.state.lost_seconds += 25
        self.state.fatigue.setdefault(id(in_player), 0.0)
        if self.state.ball_carrier is out_player:
            self.state.ball_carrier = in_player

        assigned = self.state.player_role(in_player)
        if assigned == "POR" and self.state.player_role(self.state.xi[team_idx][0]) != "POR":
            self.state.xi[team_idx].remove(in_player)
            self.state.xi[team_idx].insert(0, in_player)

        has_natural_gk = any(
            p.position == "POR" and self.state.player_role(p) == "POR"
            for p in self.state.xi[team_idx]
        )
        self.state.gk_penalty[team_idx] = 0 if has_natural_gk else -25
        self.state.ratings[team_idx] = self.state.compute_ratings(team_idx)
        self._sfx("cambio")
        self.io.update_hud(self.state.hud_status())

    def read_lineup(self) -> None:
        lines = [self.state.formation_text(0) + "."]
        lines.extend(self.player_status_text(p) for p in self.state.xi[0])
        lines.append(
            f"Cambios hechos: {self.state.substitutions_used[0]}. "
            f"{self.remaining_subs_text(0)}."
        )
        self.io.read_lines("Tu alineación en el campo", lines)

    def _preview_formation_code(self, out_player: Player, in_player: Player, as_role: str) -> str:
        c = self.state.formation_counts(0)
        old_role = self.state.player_role(out_player)
        c[old_role] = max(0, c.get(old_role, 0) - 1)
        c[as_role] = c.get(as_role, 0) + 1
        return f"{c['DEF']}-{c['MED']}-{c['DEL']}"

    def open_substitution_menu(self, forced_out: Player | None = None) -> bool:
        rem = self.remaining_subs(0, human=True)
        if rem is not None and rem <= 0:
            self.io.speak(
                f"Ya hiciste tus {self.state.max_substitutions} cambios. No puedes cambiar más.",
                interrupt=True,
            )
            return False
        if not self.bench(0):
            self.io.speak("No tienes suplentes disponibles.", interrupt=True)
            return False

        pos_out = 0
        while True:
            if forced_out is not None:
                out_player = forced_out
            else:
                on_pitch = list(self.state.xi[0])
                idx = self.io.choose(
                    f"Cambios. {self.state.formation_text(0)}. {self.remaining_subs_text(0)}. ¿Quién sale?",
                    [self.player_status_text(p) for p in on_pitch],
                    initial_index=pos_out,
                )
                if idx is None:
                    return False
                pos_out = idx
                out_player = on_pitch[idx]

            candidates = self.eligible_replacements(0, out_player)
            if not candidates:
                self.io.speak(
                    "No tienes suplentes de campo disponibles para ese cambio. "
                    "Un portero suplente no puede entrar como jugador de campo.",
                    interrupt=True,
                )
                if forced_out is not None:
                    return False
                continue

            idx_in = self.io.choose(
                f"Sale {out_player.name}. ¿Quién entra?",
                [
                    f"{p.name}, {POSITION_NAMES_ES[p.position]}, calidad {p.overall}"
                    for p in candidates
                ],
            )
            if idx_in is None:
                if forced_out is not None:
                    return False
                continue

            in_player = candidates[idx_in]
            out_role = self.state.player_role(out_player)
            chosen_role = in_player.position

            # Ask tactical role if natural positions differ on outfield players
            if out_role != "POR" and in_player.position != out_role:
                natural_code = self._preview_formation_code(out_player, in_player, in_player.position)
                cover_code = self._preview_formation_code(out_player, in_player, out_role)
                role_choice = self.io.choose(
                    f"{in_player.name} es {POSITION_NAMES_ES[in_player.position]}, "
                    f"pero sale {out_player.name} que jugaba de {POSITION_NAMES_ES[out_role]}. "
                    "¿Dónde lo colocas?",
                    [
                        f"En su posición natural ({POSITION_NAMES_ES[in_player.position]}): cambia el dibujo a {natural_code}, rinde al 100 por ciento",
                        f"En la posición de {out_player.name} ({POSITION_NAMES_ES[out_role]}): mantiene el dibujo {cover_code}, rinde algo menos fuera de puesto",
                    ],
                )
                if role_choice is None:
                    if forced_out is not None:
                        return False
                    continue
                chosen_role = in_player.position if role_choice == 0 else out_role
            elif out_role == "POR" and in_player.position != "POR":
                chosen_role = "POR"

            role_desc = (
                f"como {POSITION_NAMES_ES[chosen_role]}"
                if chosen_role != in_player.position
                else f"en su posición natural de {POSITION_NAMES_ES[in_player.position]}"
            )
            if self.io.confirm(
                f"Sale {out_player.name} y entra {in_player.name} {role_desc}. ¿Confirmas el cambio?"
            ):
                self.apply_substitution(0, out_player, in_player, play_as_position=chosen_role)
                msg = (
                    f"Cambio hecho. Sale {out_player.name}, entra {in_player.name} {role_desc}. "
                    f"Dibujo actual {self.state.formation_code(0)}. {self.remaining_subs_text(0)}."
                )
                if out_role == "POR" and in_player.position != "POR":
                    msg += " Cuidado, te quedas sin portero natural."
                self.io.speak(msg)
                return True

    def ai_substitutions(self, team_idx: int) -> None:
        if team_idx == 0 and not self.state.auto:
            return
        rem = self.remaining_subs(team_idx)
        if (rem is not None and rem <= 0) or not self.state.xi[team_idx]:
            return
        candidates = [
            p
            for p in self.state.xi[team_idx]
            if id(p) in self.state.injured
            or (
                self.state.turn >= 20 * self.state.factor
                and self.state.fatigue.get(id(p), 0.0) >= 68
            )
        ]
        if not candidates:
            return
        out_player = max(
            candidates,
            key=lambda p: (id(p) in self.state.injured, self.state.fatigue.get(id(p), 0.0)),
        )
        if id(out_player) not in self.state.injured and not self.state.roll(
            0.25 / self.state.factor
        ):
            return
        replacements = self.eligible_replacements(team_idx, out_player)
        if not replacements:
            return
        out_role = self.state.player_role(out_player)
        same_pos = [p for p in replacements if p.position == out_role]
        if not same_pos and out_role == "POR" and id(out_player) not in self.state.injured:
            return
        pool = same_pos or replacements
        in_player = max(pool, key=lambda p: p.overall)
        play_as = "POR" if out_role == "POR" else in_player.position
        self.apply_substitution(team_idx, out_player, in_player, play_as_position=play_as)
        self._speak(
            f"Cambio en {self.state.team_name(team_idx)}: sale {out_player.name}, entra {in_player.name}."
        )

    def check_injuries(self) -> None:
        for team_idx in (0, 1):
            if not self.state.xi[team_idx] or not self.state.roll(
                0.006 / self.state.factor
            ):
                continue
            weights = [
                1.0 + self.state.fatigue.get(id(p), 0.0) / 40.0
                for p in self.state.xi[team_idx]
            ]
            player = self.state.rng.choices(self.state.xi[team_idx], weights=weights)[0]
            if id(player) in self.state.injured:
                continue
            self.state.injured.add(id(player))
            self.state.lost_seconds += 60
            self._sfx("silbato")
            self._speak(
                f"¡{player.name}, de {self.state.team_name(team_idx)}, "
                "se queda tendido en el campo! Parece una lesión."
            )
            self.state.add_highlight(
                f"Se lesiona {player.name}, de {self.state.team_name(team_idx)}."
            )
            self.state.ratings[team_idx] = self.state.compute_ratings(team_idx)
            if team_idx == 0 and not self.state.auto:
                rem = self.remaining_subs(0, human=True)
                if (rem is None or rem > 0) and self.eligible_replacements(0, player):
                    self.io.speak("Tienes que hacer un cambio. Elige quién entra.")
                    if not self.open_substitution_menu(forced_out=player):
                        self.io.speak(
                            f"{player.name} seguirá en el campo lesionado y rendirá mucho menos."
                        )
                else:
                    self._speak(
                        f"No te quedan cambios disponibles, {player.name} seguirá jugando lesionado."
                    )
            else:
                self.ai_substitutions(team_idx)

    def apply_turn_fatigue(self) -> None:
        rates = {"POR": 0.5, "DEF": 2.0, "MED": 2.6, "DEL": 2.3}
        user_stamina_mult = float(self.state.tactic_profile().get("stamina", 1.0))
        for team_idx in (0, 1):
            mult = user_stamina_mult if team_idx == 0 else 1.0
            for player in self.state.xi[team_idx]:
                pid = id(player)
                role = self.state.player_role(player)
                delta = (
                    rates.get(role, 2.0)
                    * mult
                    * self.state.rng.uniform(0.8, 1.2)
                    / self.state.factor
                )
                self.state.fatigue[pid] = min(100.0, self.state.fatigue.get(pid, 0.0) + delta)

        if not self.state.muted:
            for player in self.state.xi[0]:
                pid = id(player)
                if (
                    self.state.fatigue.get(pid, 0.0) >= 75
                    and pid not in self.state.fatigue_warned
                    and pid not in self.state.injured
                    and self.state.turn - self.state.last_fatigue_warn_turn
                    >= 4 * self.state.factor
                ):
                    self.state.fatigue_warned.add(pid)
                    self.state.last_fatigue_warn_turn = self.state.turn
                    self._speak(
                        f"{player.name}, de tu equipo, está muy cansado. Puedes cambiarlo con la tecla C."
                    )
                    break

    def _time_waste_phrase(self, player: Player) -> str:
        if self.state.player_role(player) == "POR":
            return self.state.rng.choice(
                [
                    "tarda una eternidad en sacar de meta",
                    "se queda con el balón en las manos y no lo suelta",
                    "se agacha a atarse los botines antes de sacar",
                ]
            )
        return self.state.rng.choice(
            [
                "retrasa el saque a propósito",
                "se tira al suelo y pide atención médica",
                "camina despacio a buscar el balón",
                "se queda discutiendo con el árbitro",
                "coloca el balón una y otra vez antes del saque",
            ]
        )

    def waste_time(self, team_idx: int = 0) -> bool:
        if self.state.suspended_match or self.state.abandoned:
            return False
        if self.state.last_time_waste_turn.get(team_idx) == self.state.turn:
            if team_idx == 0:
                self.io.speak(
                    "Ya hiciste tiempo en esta jugada. Espera a la siguiente.",
                    interrupt=True,
                )
            return False

        self.state.last_time_waste_turn[team_idx] = self.state.turn
        player = self.state.pick_player(
            team_idx, {"POR": 1.2, "DEF": 3.0, "MED": 2.5, "DEL": 1.0}
        )
        secs = self.state.rng.choice((20, 30, 40))
        self.state.lost_seconds += secs
        self.state.wasted_seconds += secs
        times = self.state.time_waste_by_player.get(id(player), 0)
        self.state.time_waste_by_player[id(player)] = times + 1
        self.state.time_waste_by_team[team_idx] += 1

        for teammate in self.state.xi[team_idx]:
            pid = id(teammate)
            if pid in self.state.fatigue:
                self.state.fatigue[pid] = max(0.0, self.state.fatigue[pid] - 4.0)

        self._sfx("silbato")
        self._speak(
            f"¡Pausa! {player.name}, de {self.state.team_name(team_idx)}, "
            f"hace tiempo: {self._time_waste_phrase(player)}."
        )
        if team_idx == 1:
            self._crowd_react("abucheo", 1.4)
        if team_idx == 0 and self.state.goals[0] < self.state.goals[1]:
            self._speak("Ojo: vas perdiendo, y perder tiempo no te ayuda.")

        prob_yellow = min(
            0.9,
            (0.12 if times == 0 else 0.75)
            + 0.06 * max(0, self.state.time_waste_by_team[team_idx] - 2),
        )
        if self.state.roll(prob_yellow):
            self._speak("El árbitro se acerca y lo amonesta por perder tiempo.")
            self._give_card(team_idx, player, "amarilla", "por perder tiempo")
        elif times == 0:
            self._speak("El árbitro le llama la atención y le pide que juegue.")
        else:
            self._speak("El árbitro le advierte otra vez: la próxima será tarjeta.")
        return True

    def ai_waste_time(self) -> None:
        if (
            self.state.suspended_match
            or self.state.total_turns <= 0
            or self.state.turn < 0.65 * self.state.total_turns
        ):
            return
        score = (
            self.state.aggregate()
            if self.state.first_leg is not None
            else self.state.goals
        )
        teams_to_check = (1,) if not self.state.auto else (0, 1)
        for team_idx in teams_to_check:
            if score[team_idx] > score[1 - team_idx] and self.state.roll(
                0.05 / self.state.factor
            ):
                self._speak(f"Minuto {self.state.minute()}.")
                self.waste_time(team_idx)
