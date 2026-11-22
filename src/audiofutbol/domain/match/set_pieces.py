"""Set pieces, shots, goals, duels, aerial play, fouls, cards, and shootouts."""

from __future__ import annotations

from typing import Callable

from audiofutbol.domain.match.ports import MatchIO
from audiofutbol.domain.match.state import PENALTY_DIRECTIONS, ZONES_OWN, MatchState
from audiofutbol.domain.match.var import VarSystem
from audiofutbol.domain.models import Player


class SetPieceResolver:
    """Resolves shots, goals, fouls, cards, corners, free kicks, penalties, and restarts."""

    def __init__(
        self,
        state: MatchState,
        io: MatchIO,
        var: VarSystem,
        choose_fn: Callable[[str, list[str], list[int] | None, dict[str, int] | None], int],
        crowd_react: Callable[[str, float], None],
    ) -> None:
        self.state = state
        self.io = io
        self.var = var
        self._choose = choose_fn
        self._crowd_react = crowd_react

    def _speak(self, text: str) -> None:
        if not self.state.muted:
            self.io.speak(text)

    def _sfx(self, event: str, pan: float | None = None) -> None:
        if not self.state.muted:
            self.io.play_sfx(event, pan=pan)

    def _announce_shots(self, team_idx: int) -> None:
        if self.state.settings.shot_notice_enabled:
            self._speak(self.state.shots_text(team_idx))

    def _announce_scoreboard(self) -> None:
        self._speak(self.state.scoreboard_text())
        self.io.update_hud(self.state.hud_status())

    # ------------------------------------------------------- Goalkeeper 1v1
    def goalkeeper_duel(self, att_idx: int, striker: Player | None = None) -> tuple[bool, float]:
        def_idx = 1 - att_idx
        gk = self.state.goalkeeper(def_idx)
        if gk is None:
            return False, 1.0
        striker = striker or self.state.pick_player(
            att_idx, {"DEL": 5.0, "MED": 2.0, "DEF": 0.3, "POR": 0.0}
        )
        self._speak(f"¡{striker.name} se queda solo frente al portero {gk.name}!")
        roll = self.state.rng.random()

        if roll < 0.03:
            self.state.fouls[def_idx] += 1
            self._sfx("falta")
            self.state.lost_seconds += 5
            self._crowd_react("abucheo" if def_idx == 0 else "ovacion", 1.8)
            if self.state.roll(0.5):
                self._speak(
                    f"¡El portero {gk.name} sale desesperado y derriba a {striker.name} dentro del área!"
                )
            else:
                self._speak(
                    f"¡El portero {gk.name} sale a cortar y se lleva por delante a {striker.name}! "
                    "El árbitro señala falta."
                )
            r = self.state.rng.random()
            if r < 0.15:
                self.give_card(def_idx, gk, self.var.review_red_card(def_idx, gk))
            elif r < 0.65:
                self.give_card(def_idx, gk, "amarilla")
            self.state.add_highlight(
                f"Penal por derribo del portero {gk.name}, de {self.state.team_name(def_idx)}, sobre {striker.name}."
            )
            if self.state.suspended_match:
                return True, 1.0
            self.state.possession, self.state.zone = att_idx, 3
            self.var.review_penalty(def_idx, att_idx, self.free_kick, self.penalty)
            return True, 1.0

        if roll < 0.095:
            self._speak(
                f"El portero {gk.name} sale muy lejos de su área para cortar el pase."
            )
            if self.state.roll(0.62 + (self.state.rating(def_idx, "gk") - 70) / 100.0 * 0.5):
                self._sfx("parada")
                if self.state.roll(0.5):
                    self._speak("Llega primero y despeja de pie, lejos del área.")
                else:
                    self._speak(
                        f"Se adelanta a {striker.name} y despeja de cabeza, fuera del área."
                    )
                if self.state.roll(0.4):
                    self.state.add_highlight(
                        f"El portero {gk.name}, de {self.state.team_name(def_idx)}, sale lejos del área y corta el pase de peligro."
                    )
                self.state.possession, self.state.zone = def_idx, 1
                return True, 1.0
            self._speak(f"¡Llega tarde y {striker.name} lo supera! El arco queda vacío.")
            return False, 3.0

        if roll < 0.30:
            if self.state.roll(0.5):
                self._speak(
                    f"El portero {gk.name} sale a achicar el ángulo y {striker.name} se queda con poco espacio."
                )
            else:
                self._speak(
                    f"{striker.name} se planta ante el portero, que sale a su encuentro y cierra el ángulo."
                )
            return False, 0.78

        return False, 1.0

    # ------------------------------------------------------- Aerial & Rebounds
    def defensive_header(self, def_idx: int, att_idx: int, from_corner: bool = False) -> None:
        defender = self.state.pick_player(
            def_idx, {"DEF": 6.0, "MED": 1.3, "DEL": 0.4, "POR": 0.0}
        )
        self._sfx("cabezazo", self.state.rng.uniform(-0.6, 0.6))
        phrase = self.state.rng.choice(
            [
                "¡Cabezazo defensivo de %s! Despeja el centro.",
                "%s se eleva por encima de todos y despeja de cabeza.",
                "¡%s cabecea con fuerza y aleja el peligro!",
            ]
        )
        self._speak(phrase % defender.name)
        if self.state.roll(0.25):
            self.state.add_highlight(
                f"Cabezazo defensivo de {defender.name}, de {self.state.team_name(def_idx)}, que evita el peligro."
            )
        if self.state.can_chain_corner(att_idx, from_corner) and self.state.roll(0.22):
            self._speak("El despeje sale por la línea de fondo.")
            self.corner(att_idx, consecutive=from_corner)
        else:
            self.state.possession, self.state.zone = def_idx, 1

    def aerial_defense(
        self, att_idx: int, label: str, from_corner: bool = False
    ) -> tuple[str, float]:
        def_idx = 1 - att_idx
        diff = (self.state.rating(def_idx, "dfn") - self.state.rating(att_idx, "att")) / 100.0
        gk = self.state.goalkeeper(def_idx)

        if def_idx == 0:
            choice = self._choose(
                f"{label} del rival. ¿Cómo defiendes?",
                [
                    "Despejar de cabeza con los centrales",
                    "Que el portero salga a atrapar el balón",
                    "Marcar de cerca a los rematadores",
                ],
                [45, 30, 25],
                None,
            )
            if self.state.abandoned:
                return "sigue", 1.0
        else:
            choice = self.state.rng.choices((0, 1, 2), weights=[50, 28, 22])[0]

        if choice == 1 and gk is None:
            choice = 0

        if choice == 0:
            if self.state.roll(0.30 + diff * 0.5):
                self.defensive_header(def_idx, att_idx, from_corner)
                return "despeje", 1.0
            return "sigue", 1.0

        if choice == 1 and gk is not None:
            if self.state.roll(0.30 + (self.state.rating(def_idx, "gk") - 70) / 100.0 * 0.6):
                self._sfx("parada")
                phrase = self.state.rng.choice(
                    [
                        "El portero %s sale con decisión y atrapa el balón en el aire.",
                        "El portero %s sale a los puños y despeja el balón.",
                    ]
                )
                self._speak(phrase % gk.name)
                self.state.possession, self.state.zone = def_idx, 1
                return "portero", 1.0
            if self.state.roll(0.30):
                self._speak(
                    f"¡El portero {gk.name} sale en falso y no llega! La portería queda desprotegida."
                )
                return "sigue", 1.7
            self._speak(f"El portero {gk.name} sale pero no alcanza el balón.")
            return "sigue", 1.15

        if def_idx == 0:
            self._speak("Marcas de cerca a los rematadores.")
        if self.state.roll(0.12 + diff * 0.3):
            self.defensive_header(def_idx, att_idx, from_corner)
            return "despeje", 1.0
        return "sigue", 0.75

    def rebound(
        self, att_idx: int, depth: int = 0, from_corner: bool = False, zone: int = 3
    ) -> None:
        def_idx = 1 - att_idx
        self._sfx("balon", self.state.rng.uniform(-0.8, 0.8))
        if zone >= 3:
            self._speak(
                self.state.rng.choice(
                    [
                        "¡Rebote! El balón queda suelto en el área.",
                        "¡Rebote! Todos van por el balón dentro del área.",
                        "¡Balón dividido! Queda muerto en el área.",
                    ]
                )
            )
        else:
            self._speak("¡Rebote! El balón queda suelto en la frontal del área.")

        win_prob = 0.52 + (
            self.state.rating(att_idx, "att") - self.state.rating(def_idx, "dfn")
        ) / 100.0 * 0.6
        if depth >= 2 or not self.state.roll(win_prob):
            defender = self.state.pick_player(
                def_idx, {"DEF": 6.0, "MED": 1.3, "DEL": 0.3, "POR": 0.0}
            )
            phrase = self.state.rng.choice(
                [
                    "%s se anticipa y despeja el rebote.",
                    "%s llega primero y manda el balón lejos.",
                    "%s se tira al suelo y despeja el rebote.",
                ]
            )
            self._speak(phrase % defender.name)
            if self.state.can_chain_corner(att_idx, from_corner) and self.state.roll(0.20):
                self._speak("El despeje sale por la línea de fondo.")
                self.corner(att_idx, consecutive=from_corner)
            else:
                self.state.possession, self.state.zone = def_idx, 1
            return

        shooter = self.state.pick_player(
            att_idx, {"DEL": 5.0, "MED": 2.2, "DEF": 0.8, "POR": 0.0}
        )
        if zone >= 3 and self.state.roll(0.10):
            self.shot(
                att_idx,
                3,
                mult=0.55,
                bicycle=True,
                rebound=True,
                depth=depth + 1,
                from_corner=from_corner,
            )
        else:
            self.shot(
                att_idx,
                zone,
                mult=0.85,
                rebound=True,
                depth=depth + 1,
                shooter=shooter,
                from_corner=from_corner,
            )

    # ------------------------------------------------------- Shots & Goals
    def olympic_goal(self, att_idx: int, taker: Player) -> str:
        def_idx = 1 - att_idx
        self.state.shots[att_idx] += 1
        self._speak(
            f"¡{taker.name} va a intentar el gol olímpico! Cobra el córner directo a la portería."
        )
        self._sfx("corner", self.state.rng.uniform(-0.8, 0.8))

        p = 0.03 * (taker.overall / 75.0) ** 2
        p *= 1 + (self.state.rating(att_idx, "att") - self.state.rating(def_idx, "dfn")) / 100.0
        p *= 1 - (self.state.rating(def_idx, "gk") - 70) / 100.0 * 1.2
        p *= self.state.goal_factor
        if self.state.roll(p):
            self.state.shots_on_target[att_idx] += 1
            self.score_goal(att_idx, taker, wonder_goal=True, goal_type="gol olímpico")
            self._announce_shots(att_idx)
            return "gol"

        roll = self.state.rng.random()
        if roll < 0.30:
            self.state.shots_on_target[att_idx] += 1
            self._sfx("parada")
            self._speak("El portero se estira y ataja el balón con esfuerzo.")
            self._announce_shots(att_idx)
            if self.state.roll(0.20):
                self.rebound(att_idx, 1, False)
            else:
                self.state.possession, self.state.zone = def_idx, 1
        elif roll < 0.42:
            self._sfx("poste")
            self._speak("¡Poste! El balón da en el poste, por centímetros.")
            self.state.add_highlight(
                f"Casi gol olímpico de {taker.name}, de {self.state.team_name(att_idx)}: el balón da en el poste."
            )
            self._announce_shots(att_idx)
            if self.state.roll(0.35):
                self.rebound(att_idx, 1, False)
            else:
                self.state.possession, self.state.zone = def_idx, 1
        elif roll < 0.60:
            self._sfx("casi")
            self._speak("El balón se cuela rozando el travesaño y sale por encima.")
            self._announce_shots(att_idx)
            self.goal_kick(def_idx)
        else:
            self._sfx("casi")
            self._speak("El balón se va directo por la línea de fondo.")
            self._announce_shots(att_idx)
            self.goal_kick(def_idx)
        return "fallo"

    def shot(
        self,
        att_idx: int,
        zone: int,
        mult: float = 1.0,
        header: bool = False,
        corner_ok: bool = True,
        free_kick: bool = False,
        rebound: bool = False,
        depth: int = 0,
        goal_type: str | None = None,
        bicycle: bool = False,
        shooter: Player | None = None,
        from_corner: bool = False,
    ) -> str:
        def_idx = 1 - att_idx
        gk_factor = 1.0
        if zone == 3 and not (header or free_kick or rebound or bicycle):
            shooter = shooter or self.state.pick_scorer(att_idx, header=False)
            ended, gk_factor = self.goalkeeper_duel(att_idx, shooter)
            if ended:
                return "fallo"

        taker = shooter or (
            self.state.pick_bicycle_taker(att_idx)
            if bicycle
            else self.state.pick_scorer(att_idx, header=header)
        )
        self.state.shots[att_idx] += 1
        base = {0: 0.005, 1: 0.015, 2: 0.095, 3: 0.36}[zone]

        # Meaningful individual finishing quality (non-linear: 90 OVR >> 70 OVR)
        eff_taker = self.state.effective_overall(taker)
        player_factor = max(0.62, min(1.55, (eff_taker / 74.0) ** 1.65))

        # Positional specialty bonus
        role = self.state.player_role(taker)
        if header:
            role_factor = 1.12 if role in ("DEF", "DEL") else 0.94
        elif zone >= 3:
            role_factor = {"DEL": 1.12, "MED": 1.00, "DEF": 0.84, "POR": 0.50}.get(role, 1.0)
        else:
            role_factor = {"MED": 1.10, "DEL": 1.04, "DEF": 0.85, "POR": 0.45}.get(role, 1.0)

        angle_factor = 1.0
        if not (header or free_kick or bicycle or rebound):
            angle_factor = 1.08 if self.state.lane == 0 else 0.90

        space_factor = 1.0
        if att_idx == 0 and self.state.space_bonus > 0:
            space_factor = 1.0 + self.state.space_bonus * 1.15
            self.state.space_bonus = 0.0

        # Real goalkeeper individual quality vs shot
        gk = self.state.goalkeeper(def_idx)
        gk_eff = (
            self.state.effective_overall(gk) + self.state.gk_penalty[def_idx]
            if gk is not None
            else 50.0 + self.state.gk_penalty[def_idx]
        )
        gk_resistance = max(0.55, min(1.38, 1.0 - (gk_eff - 74.0) / 68.0))

        team_edge = 1.0 + (
            self.state.rating(att_idx, "att") - self.state.rating(def_idx, "dfn")
        ) / 100.0 * 2.2

        p = (
            base
            * mult
            * player_factor
            * role_factor
            * angle_factor
            * space_factor
            * self.state.weather.shot_skid_mod
            * max(0.55, team_edge)
            * gk_resistance
            * self.state.goal_factor
            * gk_factor
        )

        sfx_kind = "chilena" if bicycle else ("cabezazo" if header else "balon")
        self._sfx(sfx_kind, self.state.stereo_pan(0.18))
        if bicycle:
            self._speak(f"¡{taker.name} se eleva de espaldas al arco e intenta una chilena!")
        elif rebound:
            phrase = self.state.rng.choice(
                [
                    "¡Contrarremate de %s!",
                    "%s aprovecha el rebote y dispara de nuevo.",
                ]
            )
            self._speak(phrase % taker.name)
        else:
            verb = "remata de cabeza" if header else "dispara"
            self._speak(f"{taker.name} {verb}.")

        if bicycle:
            resolved_type = "de chilena"
        elif goal_type:
            resolved_type = goal_type
        elif rebound:
            resolved_type = "de contrarremate"
        elif header:
            resolved_type = "de cabeza"
        else:
            resolved_type = None

        allow_corner = corner_ok and self.state.can_chain_corner(att_idx, from_corner)
        if self.state.roll(p):
            self.state.shots_on_target[att_idx] += 1
            wonder = bicycle or (
                (not header)
                and (
                    (zone <= 2 and self.state.roll(0.40))
                    or (zone == 3 and self.state.roll(0.10))
                )
            )
            self.score_goal(att_idx, taker, wonder_goal=wonder, goal_type=resolved_type)
            self._announce_shots(att_idx)
            return "gol"

        roll = self.state.rng.random()
        if roll < 0.38:
            self.state.shots_on_target[att_idx] += 1
            self.state.saves[def_idx] += 1
            self.state.shift_momentum(-1.4 if att_idx == 0 else 1.8)
            self._sfx("parada", self.state.stereo_pan(0.15))
            gk = self.state.goalkeeper(def_idx)
            self._speak(
                f"El portero {gk.name} ataja el disparo."
                if gk
                else "El portero ataja el disparo."
            )
            if zone >= 3:
                self.state.add_highlight(
                    f"Gran atajada del portero de {self.state.team_name(def_idx)} al remate de {taker.name}."
                )
            self._announce_shots(att_idx)
            spill_prob = (0.28 if header else 0.24) * self.state.weather.gk_spill_mod
            if zone >= 2 and depth < 2 and self.state.roll(spill_prob):
                self._speak("¡El portero no logra retener el balón!")
                self.rebound(att_idx, depth, from_corner, min(zone, 3))
            elif self.state.roll(0.22) and allow_corner:
                self.corner(att_idx, consecutive=from_corner)
            else:
                self.state.set_possession(def_idx, 1)
        elif roll < 0.55:
            self._sfx("poste", self.state.stereo_pan(0.15))
            self.state.shift_momentum(0.8 if att_idx == 0 else -0.8)
            self._speak("¡Poste!")
            self.state.add_highlight(
                f"Poste de {taker.name}, de {self.state.team_name(att_idx)}."
            )
            self._announce_shots(att_idx)
            if zone >= 2 and depth < 2 and self.state.roll(0.32):
                self._speak("¡El balón vuelve al área!")
                self.rebound(att_idx, depth, from_corner, min(zone, 3))
            elif self.state.roll(0.4) and allow_corner:
                self.corner(att_idx, consecutive=from_corner)
            else:
                self.state.set_possession(def_idx, 1)
        elif roll < 0.82:
            self._sfx("casi")
            q = self.state.rng.random()
            if q < 0.45:
                self._speak("¡El balón sale por encima del travesaño!")
            elif q < 0.70:
                self._speak("El disparo sale rozando el poste, por fuera.")
                if zone >= 2:
                    self.state.add_highlight(
                        f"Por poco: el disparo de {taker.name}, de {self.state.team_name(att_idx)}, roza el poste."
                    )
            else:
                self._speak("El disparo se va fuera.")
            self._announce_shots(att_idx)
            self.goal_kick(def_idx)
        else:
            self.state.blocks[def_idx] += 1
            blocker = self.state.pick_player(
                def_idx, {"DEF": 5.0, "MED": 2.0, "DEL": 0.2, "POR": 0.0}
            )
            self._speak(f"{blocker.name} se cruza a tiempo y bloquea el disparo.")
            self._announce_shots(att_idx)
            if zone >= 1 and self.state.roll(0.04):
                self._sfx("silbato")
                self.foul(def_idx, zone, "mano")
            elif (
                zone >= 3
                and self.state.settings.var_enabled
                and self.state.roll(0.035)
            ):
                self.var.review_missed_handball(att_idx, self.penalty)
            elif zone >= 2 and depth < 2 and self.state.roll(0.18):
                self._speak("El balón queda suelto tras el bloqueo.")
                self.rebound(att_idx, depth, from_corner, min(zone, 3))
            elif allow_corner:
                self.corner(att_idx, consecutive=from_corner)
            else:
                self.state.set_possession(def_idx, 1, carrier=blocker)
        return "fallo"

    def score_goal(
        self,
        team_idx: int,
        scorer: Player,
        wonder_goal: bool = False,
        goal_type: str | None = None,
    ) -> None:
        self.state.lost_seconds += 30
        self.state.goals[team_idx] += 1
        scorer.goals += 1
        self.state.scorers.append((team_idx, scorer, self.state.minute()))
        suffix = f", {goal_type}" if goal_type else ""
        label = "Golazo" if wonder_goal else "Gol"
        self.state.add_highlight(
            f"{label} de {scorer.name}, de {self.state.team_name(team_idx)}{suffix}. "
            f"{self.state.team_name(0)} {self.state.goals[0]}, "
            f"{self.state.team_name(1)} {self.state.goals[1]}."
        )
        self._sfx("gol")
        if wonder_goal and goal_type == "gol olímpico":
            self._speak(
                f"¡Goooool! ¡GOL OLÍMPICO de {scorer.name}, de {self.state.team_name(team_idx)}! "
                f"El balón entra directo desde el saque de esquina. Minuto {self.state.minute()}."
            )
            self._crowd_react("ovacion" if team_idx == 0 else "abucheo", 1.1)
        elif wonder_goal:
            self._speak(
                f"¡Goooool! ¡Qué golazo de {scorer.name}, de {self.state.team_name(team_idx)}{suffix}! "
                f"Minuto {self.state.minute()}."
            )
            self._crowd_react("ovacion" if team_idx == 0 else "abucheo", 0.95)
        else:
            self._speak(
                f"¡Goooool! {scorer.name}, de {self.state.team_name(team_idx)}{suffix}. "
                f"Minuto {self.state.minute()}."
            )
        self._announce_scoreboard()
        self.state.shift_momentum(3.5 if team_idx == 0 else -3.5)
        self.state.set_possession(1 - team_idx, 1, lane=0)
        if (
            goal_type != "de penal"
            and self.state.settings.var_enabled
            and not self.state.suspended_match
            and self.state.roll(0.12)
        ):
            self.var.review_goal(team_idx, scorer)

    # ------------------------------------------------------- Cross, Corner, Restarts
    def cross(
        self, att_idx: int, mult: float = 1.0, crosser: Player | None = None
    ) -> None:
        def_idx = 1 - att_idx
        player = crosser or self.state.pick_player(
            att_idx, {"MED": 3.0, "DEL": 2.0, "DEF": 1.0, "POR": 0.0}
        )
        wing_bonus = 1.12 if self.state.lane != 0 else 0.94
        if att_idx == 0 and self.state.space_bonus > 0:
            wing_bonus += self.state.space_bonus * 0.8
            self.state.space_bonus = 0.0
        self._sfx("balon", self.state.stereo_pan(0.10))
        self._speak(f"{player.name} manda un centro al área {self.state.lane_text()}.")
        outcome, factor = self.aerial_defense(att_idx, "Centro")
        if outcome != "sigue" or self.state.abandoned:
            return
        p = (
            (0.55 + (self.state.rating(att_idx, "att") - self.state.rating(def_idx, "dfn")) / 100.0 * 0.5)
            * wing_bonus
            * self.state.weather.cross_mod
        )
        if self.state.roll(p):
            self.state.lane = 0
            if self.state.roll(0.05):
                self.shot(att_idx, 3, mult=0.50 * mult, bicycle=True)
            else:
                self.shot(att_idx, 3, mult=0.62 * mult * factor, header=True)
        elif self.state.roll(0.28):
            self.corner(att_idx)
        else:
            self._speak("La defensa despeja el centro.")
            self.state.set_possession(def_idx, 1)

    def corner(self, att_idx: int, consecutive: bool = False) -> None:
        if consecutive and self.state.corner_streak[0] == att_idx:
            self.state.corner_streak[1] += 1
        else:
            self.state.corner_streak = [att_idx, 1]
        count = self.state.corner_streak[1]
        self.state.corners[att_idx] += 1
        self._sfx("corner")
        if count == 1:
            self._speak(f"Saque de esquina para {self.state.team_name(att_idx)}.")
        else:
            ordinal = {2: "Segundo", 3: "Tercer", 4: "Cuarto"}.get(count, "Otro")
            self._speak(
                f"¡{ordinal} saque de esquina consecutivo para {self.state.team_name(att_idx)}!"
            )
            if count == 3:
                self.state.add_highlight(
                    f"Tres saques de esquina seguidos para {self.state.team_name(att_idx)}."
                )

        taker = self.state.pick_player(
            att_idx, {"MED": 4.0, "DEL": 2.0, "DEF": 0.5, "POR": 0.0}
        )
        self._speak(f"Lo cobra {taker.name}.")
        if att_idx == 0:
            choice = self._choose(
                "¿Cómo sacas el córner?",
                [
                    "Centro al primer palo",
                    "Centro al segundo palo",
                    "Saque corto",
                    "Intentar el gol olímpico",
                ],
                [33, 42, 18, 7],
                None,
            )
            if self.state.abandoned:
                return
        else:
            choice = self.state.rng.choice((0, 1, 1, 2))
            if self.state.roll(0.006):
                choice = 3

        if choice == 3:
            self.olympic_goal(att_idx, taker)
            return
        if choice == 2:
            self._speak(
                f"Saque corto, {self.state.team_name(att_idx)} mantiene la posesión."
            )
            self.state.possession, self.state.zone = att_idx, 2
            return

        self._sfx("balon")
        post = "primer" if choice == 0 else "segundo"
        self._speak(f"{taker.name} manda el centro al {post} palo.")
        outcome, factor = self.aerial_defense(att_idx, "Córner", from_corner=True)
        if outcome != "sigue" or self.state.abandoned:
            return
        if self.state.roll(0.06):
            self.shot(att_idx, 3, mult=0.50, bicycle=True, from_corner=True)
        else:
            mult = (0.66 if choice == 1 else 0.60) * factor
            self.shot(att_idx, 3, mult=mult, header=True, from_corner=True)

    def throw_in(self) -> None:
        team_idx = self.state.rng.choice((0, 1))
        side = self.state.rng.choice((-1, 1))
        zone = self.state.rng.choice((1, 1, 2))
        taker = self.state.pick_player(
            team_idx, {"DEF": 3.0, "MED": 3.0, "DEL": 1.0, "POR": 0.0}
        )
        self._sfx("balon", side * 0.8)
        wing = "izquierda" if side < 0 else "derecha"
        self._speak(
            f"El balón sale por la banda {wing}. Saque de banda para "
            f"{self.state.team_name(team_idx)}, lo saca {taker.name}."
        )
        self.state.possession, self.state.zone = team_idx, zone
        if team_idx == 0:
            choice = self._choose(
                "¿Cómo sacas de banda?",
                [
                    "Saque corto al compañero más cercano",
                    "Saque largo hacia adelante",
                ],
                [70, 30],
                None,
            )
            if self.state.abandoned:
                return
            if choice == 0:
                rec = self.state.pick_receiver(0, "corto")
                self._sfx("balon")
                self._speak(f"Saque corto para {rec.name}, mantienes la posesión.")
            elif self.state.roll(0.45):
                self.state.zone = min(3, zone + 1)
                rec = self.state.pick_receiver(0, "corto")
                self._sfx("balon")
                self._speak(
                    f"El saque largo llega a {rec.name}. Ahora {ZONES_OWN[self.state.zone]}."
                )
            else:
                self._sfx("balon")
                self._speak("El rival despeja el saque largo.")
                self.state.possession, self.state.zone = 1, 1
        else:
            self._speak(
                f"Saque corto y {self.state.team_name(1)} conserva el balón."
            )

    def goal_kick(self, team_idx: int) -> None:
        gk = (
            self.state.xi[team_idx][0]
            if self.state.xi[team_idx] and self.state.xi[team_idx][0].position == "POR"
            else None
        )
        who = gk.name if gk else "el portero"
        self._sfx("balon")
        self._speak(f"Saque de meta para {self.state.team_name(team_idx)}.")
        if team_idx == 0:
            choice = self._choose(
                "¿Cómo sacas de meta?",
                ["Salida corta jugando desde atrás", "Saque largo al delantero"],
                [65, 35],
                None,
            )
            if self.state.abandoned:
                self.state.possession, self.state.zone = 0, 1
                return
            if choice == 0:
                self.state.possession, self.state.zone = 0, 0
                rec = self.state.pick_receiver(0, "corto")
                self._speak(f"{who} saca de meta y juega corto con {rec.name}.")
                self.state.zone = 1
            elif self.state.roll(0.5):
                self.state.possession, self.state.zone = 0, 2
                rec = self.state.pick_receiver(0, "filtrado")
                self._speak(
                    f"{who} saca largo y {rec.name} controla el balón en campo rival."
                )
            else:
                self._speak(
                    f"{who} saca largo, pero la defensa rival gana el balón."
                )
                self.state.possession, self.state.zone = 1, 1
        else:
            self.state.possession, self.state.zone = 1, 1
            name = self.state.team_name(1)
            self._speak(f"{name} saca de meta y {name} se queda con el balón.")

    # ------------------------------------------------------- Free Kicks & Penalties
    def free_kick(self, att_idx: int) -> None:
        def_idx = 1 - att_idx
        taker = self.state.pick_player(
            att_idx, {"MED": 3.0, "DEL": 3.0, "DEF": 0.5, "POR": 0.0}
        )
        self._speak(
            f"Tiro libre peligroso para {self.state.team_name(att_idx)}. Lo cobra {taker.name}."
        )
        dist = self.state.rng.randint(1, 14)
        side = self.state.rng.choice(
            ("por el centro", "ligeramente a la izquierda", "ligeramente a la derecha")
        )
        plural = "" if dist == 1 else "s"
        self._speak(
            f"La falta es a {dist} metro{plural} fuera del área, {side}, "
            f"a unos {dist + 16} metros del arco."
        )
        if att_idx == 1:
            self._crowd_react("abucheo", 1.5)
            wall_size = 3 + self._choose(
                "Tiro libre del rival. ¿Cuántos jugadores pones en la barrera?",
                ["Tres jugadores", "Cuatro jugadores", "Cinco jugadores"],
                [25, 50, 25],
                None,
            )
            if self.state.abandoned:
                return
        else:
            wall_size = self.state.rng.choices((3, 4, 5), weights=[25, 50, 25])[0]

        wall_label = {3: "Tres", 4: "Cuatro", 5: "Cinco"}[wall_size]
        self._speak(
            f"El árbitro marca los nueve metros. {wall_label} jugadores de "
            f"{self.state.team_name(def_idx)} se acomodan en la barrera."
        )
        if self.state.roll(0.20):
            self._speak(
                "La barrera avanza unos pasos y el árbitro la hace retroceder."
            )

        if att_idx == 0:
            choice = self._choose(
                "¿Qué haces con el tiro libre?",
                [
                    "Disparo por encima de la barrera",
                    "Disparo rasante por un costado",
                    "Centro al área",
                    "Pase corto",
                ],
                [16, 9, 45, 30],
                None,
            )
            if self.state.abandoned:
                return
        else:
            choice = self.state.rng.choices((0, 1, 2, 3), weights=[16, 9, 45, 30])[0]

        if choice in (0, 1):
            self._direct_free_kick(att_idx, taker, choice, wall_size, dist)
        elif choice == 2:
            self.cross(att_idx, crosser=taker)
        else:
            self._speak("Pase corto, se mantiene la posesión.")
            self.state.possession, self.state.zone = att_idx, 2

    def _direct_free_kick(
        self, att_idx: int, taker: Player, style: int, wall_size: int, dist: int = 8
    ) -> None:
        def_idx = 1 - att_idx
        self._sfx("silbato")
        self._sfx("balon", self.state.rng.uniform(-0.8, 0.8))
        if style == 0:
            self._speak(f"{taker.name} levanta el balón por encima de la barrera.")
            block_prob = 0.05 + 0.035 * wall_size
        else:
            self._speak(
                f"{taker.name} dispara rasante, buscando el hueco junto a la barrera."
            )
            block_prob = 0.10 + 0.05 * wall_size

        if self.state.roll(block_prob):
            self.state.shots[att_idx] += 1
            self._sfx("barrera")
            self._speak("¡El balón da en la barrera!")
            self._announce_shots(att_idx)
            if self.state.roll(0.35):
                self.rebound(att_idx, 1, False, 2)
            elif self.state.roll(0.22) and self.state.can_chain_corner(att_idx, False):
                self.corner(att_idx)
            else:
                self.state.possession, self.state.zone = def_idx, 1
            return

        mult = (
            1.4
            * {3: 1.08, 4: 1.0, 5: 0.93}[wall_size]
            * (1.12 if style == 0 else 0.92)
        )
        mult *= 1.12 if dist <= 4 else (0.88 if dist >= 10 else 1.0)
        self.shot(
            att_idx,
            2,
            mult=mult,
            free_kick=True,
            shooter=taker,
            goal_type="de tiro libre",
        )

    def penalty(self, att_idx: int) -> None:
        self._sfx("penal")
        self.state.lost_seconds += 40
        if att_idx == 1:
            self._crowd_react("abucheo", 2.0)
        pool = [p for p in self.state.xi[att_idx] if p.position in ("DEL", "MED")] or self.state.xi[att_idx]
        taker = max(pool, key=lambda p: p.overall + self.state.rng.random() * 3)
        self._speak(
            f"¡Penal para {self.state.team_name(att_idx)}! Va a lanzar {taker.name}."
        )
        outcome = self.take_penalty_kick(att_idx)
        if outcome == "gol":
            self.score_goal(att_idx, taker, goal_type="de penal")
        else:
            reason = {
                "parada": "lo detiene el portero",
                "poste": "da en el poste",
            }.get(outcome, "se va fuera")
            self.state.add_highlight(
                f"Penal fallado por {taker.name}, de {self.state.team_name(att_idx)}: {reason}."
            )
            self.state.possession, self.state.zone = 1 - att_idx, 1

    def take_penalty_kick(self, att_idx: int) -> str:
        if att_idx == 0:
            choice = self._choose(
                "¿Hacia dónde tiras? Izquierda y derecha son las de tus altavoces",
                ["A la izquierda", "Al centro", "A la derecha"],
                [44, 12, 44],
                None,
            )
            if self.state.abandoned:
                return "fuera"
            shot_dir = choice - 1
            dive_dir = self.state.rng.choices((-1, 0, 1), weights=[44, 12, 44])[0]
        else:
            choice = self._choose(
                "Eres el portero. ¿Hacia dónde te lanzas?",
                ["A la izquierda", "Te quedas en el centro", "A la derecha"],
                [44, 12, 44],
                None,
            )
            if self.state.abandoned:
                return "fuera"
            dive_dir = choice - 1
            shot_dir = self.state.rng.choices((-1, 0, 1), weights=[44, 12, 44])[0]

        self._sfx("silbato")
        self._sfx("balon", shot_dir * 0.9)
        def_idx = 1 - att_idx
        if self.state.roll(0.06):
            if self.state.roll(0.35):
                self._sfx("poste")
                self._speak("¡El balón da en el poste!")
                return "poste"
            self._sfx("casi")
            self._speak("El penal se va fuera.")
            return "fuera"
        if dive_dir == shot_dir and self.state.roll(
            0.60 + (self.state.rating(def_idx, "gk") - 70) / 100.0 * 0.4
        ):
            self._sfx("parada")
            self._speak(
                f"¡El portero adivina y detiene el penal {PENALTY_DIRECTIONS[shot_dir + 1]}!"
            )
            return "parada"
        return "gol"

    def penalty_shootout(self) -> None:
        self._speak("Se decide en la tanda de penales.")
        self._sfx("penal")
        if not self.state.muted:
            self.io.play_theme("musica_penales", loop=True, volume=0.4)

        score = [0, 0]
        taken = [0, 0]
        order: list[list[Player]] = []
        for idx in (0, 1):
            candidates = [p for p in self.state.xi[idx] if p.position != "POR"] + [
                p for p in self.state.xi[idx] if p.position == "POR"
            ]
            order.append(
                sorted(
                    candidates,
                    key=lambda p: -(p.overall + self.state.rng.random() * 4),
                )
            )
        first = self.state.rng.choice((0, 1))
        self._speak(f"Empieza lanzando {self.state.team_name(first)}.")
        round_num = 0
        decided = False

        while not decided and not self.state.abandoned:
            for idx in (first, 1 - first):
                taker = order[idx][round_num % len(order[idx])]
                self._speak(f"Lanza {taker.name}, de {self.state.team_name(idx)}.")
                outcome = self.take_penalty_kick(idx)
                if self.state.abandoned:
                    break
                taken[idx] += 1
                if outcome == "gol":
                    score[idx] += 1
                    self._sfx("gol")
                    self._speak(
                        f"¡Gol! {self.state.team_name(0)} {score[0]}, "
                        f"{self.state.team_name(1)} {score[1]}."
                    )
                else:
                    self._speak(
                        f"Penal fallado. {self.state.team_name(0)} {score[0]}, "
                        f"{self.state.team_name(1)} {score[1]}."
                    )
                rem0, rem1 = max(0, 5 - taken[0]), max(0, 5 - taken[1])
                if min(taken) < 5 or max(taken) <= 5:
                    if score[0] > score[1] + rem1 or score[1] > score[0] + rem0:
                        decided = True
                elif taken[0] == taken[1] and score[0] != score[1]:
                    decided = True
                if decided:
                    break
            round_num += 1

        if not self.state.muted:
            self.io.stop_music(800)
        self.state.penalties = (score[0], score[1])

    # ------------------------------------------------------- Fouls & Cards
    def foul(self, offender_team: int, zone_receiver: int, foul_type: str = "normal") -> None:
        receiver_team = 1 - offender_team
        self.state.fouls[offender_team] += 1
        offender = self.state.pick_player(
            offender_team, {"DEF": 3.0, "MED": 2.5, "DEL": 1.0, "POR": 0.02}
        )
        self._sfx("falta")
        self.state.lost_seconds += 5
        if offender_team == 0:
            self._crowd_react("abucheo", 1.8)
        else:
            self._crowd_react("ovacion", 1.5)

        offender_club = self.state.team_name(offender_team)
        if foul_type == "mano":
            phrase = self.state.rng.choice(
                [
                    "¡Mano de %s, de %s! El balón le golpea en el brazo y el árbitro lo sanciona.",
                    "El árbitro señala mano de %s, de %s. Tenía el brazo separado del cuerpo.",
                    "¡Mano de %s, de %s! Corta la jugada con el brazo y es falta.",
                ]
            )
            self._speak(phrase % (offender.name, offender_club))
        elif foul_type == "barrida":
            self._speak(f"¡Barrida de {offender.name}, de {offender_club}, y llega tarde! Falta.")
        elif foul_type == "tactica":
            self._speak(f"{offender.name}, de {offender_club}, corta la jugada con una falta táctica.")
        else:
            self._speak(f"Falta de {offender.name}, de {offender_club}.")

        prob_y, prob_r = {
            "normal": (0.28, 0.005),
            "barrida": (0.36, 0.05),
            "tactica": (0.62, 0.015),
            "mano": (0.30, 0.01),
        }[foul_type]
        if zone_receiver >= 3:
            prob_y, prob_r = prob_y + 0.12, prob_r + 0.03

        roll = self.state.rng.random()
        if roll < prob_r:
            self.give_card(
                offender_team,
                offender,
                self.var.review_red_card(offender_team, offender),
            )
        elif roll < prob_r + prob_y:
            self.give_card(offender_team, offender, "amarilla")

        self.state.possession, self.state.zone = receiver_team, zone_receiver
        if self.state.suspended_match:
            return
        if zone_receiver >= 3:
            self.var.review_penalty(offender_team, receiver_team, self.free_kick, self.penalty)
        elif zone_receiver == 2:
            self.free_kick(receiver_team)
        else:
            self._speak(f"Saque de falta para {self.state.team_name(receiver_team)}.")

    def give_card(
        self,
        team_idx: int,
        player: Player,
        level: str,
        reason: str | None = None,
    ) -> None:
        self._sfx("tarjeta" if level == "amarilla" else "tarjeta_roja")
        self.state.lost_seconds += 20
        club = self.state.team_name(team_idx)
        suffix = f" {reason}" if reason else ""
        if level == "amarilla":
            count = self.state.match_yellows.get(id(player), 0) + 1
            self.state.match_yellows[id(player)] = count
            if count >= 2:
                self.state.fair_play[team_idx] += 2
                self.state.cards_log.append(
                    (team_idx, player.name, "segunda amarilla", self.state.minute())
                )
                self.state.add_highlight(
                    f"Expulsado {player.name}, de {club}, por doble amarilla."
                )
                self._speak(
                    f"Segunda amarilla para {player.name}, de {club}{suffix}. ¡Es expulsado!"
                )
                self._send_off(team_idx, player)
                return

            self.state.fair_play[team_idx] += 1
            self.state.cards_log.append(
                (team_idx, player.name, "amarilla" + suffix, self.state.minute())
            )
            self.state.add_highlight(
                f"Tarjeta amarilla para {player.name}, de {club}{suffix}."
            )
            self._speak(f"Tarjeta amarilla para {player.name}, de {club}{suffix}.")
            player.yellow_cards += 1
            if player.yellow_cards >= 2:
                player.yellow_cards = 0
                player.suspended_matches += 1
                self._speak("Acumula dos amarillas y se perderá el próximo partido.")
        else:
            self.state.fair_play[team_idx] += 4
            self.state.cards_log.append(
                (team_idx, player.name, "roja", self.state.minute())
            )
            self.state.add_highlight(
                f"Tarjeta roja directa para {player.name}, de {club}."
            )
            self._speak(f"¡Tarjeta roja directa para {player.name}, de {club}!")
            self._send_off(team_idx, player)

    def _send_off(self, team_idx: int, player: Player) -> None:
        self._sfx("abucheo" if team_idx == 1 else "casi")
        player.suspended_matches = max(player.suspended_matches, 1)
        self.state.subbed_off[team_idx].add(id(player))
        if player in self.state.xi[team_idx]:
            self.state.xi[team_idx].remove(player)
        self.state.red_cards[team_idx] += 1
        if player.position == "POR":
            self.state.gk_penalty[team_idx] = -25
            self._speak(
                "El portero se va expulsado, un jugador de campo se pone los guantes."
            )
        self._speak(
            f"{self.state.team_name(team_idx)} se queda con {11 - self.state.red_cards[team_idx]} jugadores."
        )
        if self.state.red_cards[team_idx] >= 5 and not self.state.suspended_match:
            self.state.suspended_match = True
            winner = 1 - team_idx
            self.state.goals[winner] = max(
                self.state.goals[winner], self.state.goals[team_idx] + 3
            )
            self._sfx("silbato")
            self._speak(
                f"{self.state.team_name(team_idx)} tiene menos de siete jugadores. "
                f"El árbitro suspende el partido y da la victoria a {self.state.team_name(winner)}."
            )
