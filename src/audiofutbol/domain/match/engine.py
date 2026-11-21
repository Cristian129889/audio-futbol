"""MatchEngine orchestrator: coordinates turns, halves, VAR, set pieces, and substitutions."""

from __future__ import annotations

import random
from typing import Callable

from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchIO, MatchSettings
from audiofutbol.domain.match.set_pieces import SetPieceResolver
from audiofutbol.domain.match.state import ZONES_OWN, ZONES_RIVAL, MatchState
from audiofutbol.domain.match.substitutions import SubstitutionManager
from audiofutbol.domain.match.var import VarSystem
from audiofutbol.domain.models import MatchResult, Player, Team


class MatchEngine:
    """Pure football match orchestrator decoupled from Pygame/TTS through MatchIO."""

    def __init__(
        self,
        user_team: Team,
        rival_team: Team,
        io: MatchIO | None = None,
        settings: MatchSettings | None = None,
        knockout: bool = False,
        auto: bool = False,
        title: str = "Partido",
        max_substitutions: int | None = None,
        first_leg: tuple[int, int] | None = None,
        home_index: int | None = None,
        away_goals_rule: bool = True,
        no_extra_time: bool = False,
        stadium: str | None = None,
        table_advantage: int | None = None,
        attendance: tuple[str, float] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.io: MatchIO = io or HeadlessMatchIO(rng=rng)
        self.state = MatchState(
            user_team=user_team,
            rival_team=rival_team,
            settings=settings or MatchSettings(),
            knockout=knockout,
            auto=auto,
            title=title,
            max_substitutions=max_substitutions,
            first_leg=first_leg,
            home_index=home_index,
            away_goals_rule=away_goals_rule,
            no_extra_time=no_extra_time,
            stadium=stadium,
            table_advantage=table_advantage,
            attendance=attendance,
            rng=rng,
        )
        self.var = VarSystem(self.state, self.io, self._crowd_react)
        self.set_pieces = SetPieceResolver(
            self.state, self.io, self.var, self._choose, self._crowd_react
        )
        self.subs = SubstitutionManager(
            self.state, self.io, self._crowd_react, self.set_pieces.give_card
        )

    def _speak(self, text: str, interrupt: bool = False) -> None:
        if not self.state.muted:
            self.io.speak(text, interrupt=interrupt)

    def _sfx(self, event: str, pan: float | None = None) -> None:
        if not self.state.muted:
            self.io.play_sfx(event, pan=pan)

    def _crowd_react(self, event: str, boost: float = 1.5) -> None:
        amb = self.state.settings.crowd_volume
        if self.state.muted or amb <= 0:
            return
        self.io.play_sfx(
            event,
            pan=self.state.rng.uniform(-0.5, 0.5),
            volume=min(1.0, amb * boost),
        )

    def _crowd_boo_rival(self, zone: int) -> None:
        amb = self.state.settings.crowd_volume
        if self.state.muted or amb <= 0:
            return
        if self.state.roll((0.8 if zone >= 2 else 0.45) / self.state.factor):
            self.io.play_sfx(
                "abucheo",
                pan=self.state.rng.uniform(-0.5, 0.5),
                volume=min(1.0, amb * 1.2),
            )

    # ------------------------------------------------------- In-match shortcuts
    def build_shortcuts(self) -> dict[str, Callable[[], str | None]]:
        def k_scoreboard() -> None:
            self.io.speak(self.state.scoreboard_text(), interrupt=True)

        def k_time() -> None:
            self.io.speak(f"Minuto {self.state.minute()}", interrupt=True)

        def k_possession() -> None:
            if self.state.possession == 0:
                self.io.speak(
                    f"Tienes el balón {ZONES_OWN[self.state.zone]}", interrupt=True
                )
            else:
                self.io.speak(
                    f"{self.state.team_name(1)} tiene el balón {ZONES_RIVAL[self.state.zone]}",
                    interrupt=True,
                )

        def k_goals() -> None:
            if not self.state.scorers:
                self.io.speak("Todavía no hay goles", interrupt=True)
            else:
                text = ". ".join(
                    f"{p.name}, de {self.state.team_name(i)}, minuto {m}"
                    for i, p, m in self.state.scorers
                )
                self.io.speak(text, interrupt=True)

        def k_cards() -> None:
            if not self.state.cards_log:
                self.io.speak("No hay tarjetas todavía", interrupt=True)
            else:
                text = ". ".join(
                    f"{t} para {n}, de {self.state.team_name(i)}, minuto {m}"
                    for i, n, t, m in self.state.cards_log
                )
                self.io.speak(text, interrupt=True)

        def k_shots() -> None:
            self.io.speak(
                f"{self.state.shots_text(0)} {self.state.shots_text(1)}",
                interrupt=True,
            )

        def k_stats() -> None:
            self.io.speak(
                " ".join(self.state.stats_lines(self.state.snapshot())),
                interrupt=True,
            )

        def k_subs() -> str:
            self.subs.open_substitution_menu()
            return "releer"

        def k_lineup() -> str:
            self.subs.read_lineup()
            return "releer"

        def k_time_waste() -> None:
            self.subs.waste_time(0)

        def k_var() -> None:
            if not self.state.var_log:
                self.io.speak("Todavía no hubo revisiones del VAR.", interrupt=True)
            else:
                text = ". ".join(f"Minuto {m}: {t}" for m, t in self.state.var_log)
                self.io.speak(text, interrupt=True)

        def k_help() -> None:
            self.io.speak(
                "Atajos: M marcador. T tiempo. P posesión. G goles. A tarjetas. "
                "E tiros y tiros a puerta. S estadísticas del partido. H hacer tiempo. "
                "V decisiones del VAR. C cambios de jugadores. L alineación en el campo. "
                "Cuando atacas: F pase filtrado, R regate, D disparo, B conducir el balón. "
                "Cerca del área también puedes centrar o intentar una chilena. "
                "Control silencia la voz, F2 repite el último mensaje. "
                "Flechas para elegir, Enter para confirmar, Escape para pausa.",
                interrupt=True,
            )

        return {
            "m": k_scoreboard,
            "t": k_time,
            "p": k_possession,
            "g": k_goals,
            "a": k_cards,
            "e": k_shots,
            "s": k_stats,
            "c": k_subs,
            "l": k_lineup,
            "h": k_time_waste,
            "v": k_var,
            "f1": k_help,
        }

    def _choose(
        self,
        title: str,
        options: list[str],
        weights: list[int] | None = None,
        quick_keys: dict[str, int] | None = None,
    ) -> int:
        if self.state.auto:
            if weights:
                return self.state.rng.choices(range(len(options)), weights=weights)[0]
            return self.state.rng.randrange(len(options))

        self.io.update_hud(self.state.hud_status())
        while True:
            idx = self.io.choose(
                title, options, shortcuts=self.build_shortcuts(), quick_keys=quick_keys
            )
            if idx is not None:
                return idx
            pause_choice = self.io.choose(
                "Pausa",
                [
                    "Continuar",
                    "Simular el resto del partido",
                    "Abandonar el partido",
                ],
            )
            if pause_choice == 1:
                self.io.speak("Simulando el resto del partido.")
                self.state.auto = True
                self.state.muted = True
                return self.state.rng.randrange(len(options))
            if pause_choice == 2 and self.io.confirm(
                "¿Seguro que quieres abandonar? Contará como derrota 0 a 3"
            ):
                self.state.abandoned = True
                return 0

    # ------------------------------------------------------- Attack / Defense Turns
    def _lose_possession(self, can_foul: bool = True) -> None:
        z = self.state.zone
        if can_foul and z >= 1 and self.state.roll(0.16):
            self._speak("Te derriban.")
            self.set_pieces.foul(1, z, "normal")
        else:
            self.state.possession, self.state.zone = 1, min(2, 3 - z)

    def _through_ball(self, z: int, mid_diff: float) -> None:
        rec = self.state.pick_receiver(0, "filtrado")
        passer = self.state.pick_player(
            0, {"MED": 4.0, "DEL": 1.0, "DEF": 0.8, "POR": 0.0}
        )
        self._sfx("balon")
        if z >= 1 and self.state.roll(0.14):
            self._sfx("silbato")
            self._speak(
                f"Pase filtrado de {passer.name} para {rec.name}, pero estaba en fuera de juego."
            )
            self.state.possession, self.state.zone = 1, 1
            return
        if self.state.roll(0.36 + mid_diff * 0.8):
            self.state.zone = min(3, z + 2)
            if self.state.roll(0.22):
                self._crowd_react("ovacion", 2.0)
                self._speak(
                    f"¡Súper pase de {passer.name} para {rec.name}! Rompe toda la defensa. "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
                self.state.add_highlight(f"Súper pase de {passer.name} para {rec.name}.")
            else:
                self._speak(
                    f"¡Pase filtrado de {passer.name} para {rec.name}! "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
        else:
            self._speak(
                f"El pase filtrado de {passer.name} para {rec.name} es interceptado."
            )
            self._lose_possession(False)

    def _dribble(self, z: int, att_diff: float) -> None:
        player = self.state.pick_player(
            0, {"DEL": 4.0, "MED": 3.0, "DEF": 0.5, "POR": 0.0}
        )
        self._sfx("regate")
        if self.state.roll(0.48 + att_diff * 0.8):
            q = self.state.rng.random()
            if q < 0.14:
                self.state.zone = min(3, z + 2)
                self._crowd_react("ovacion", 2.0)
                self._speak(
                    f"¡Regate milagroso de {player.name}! Deja atrás a dos rivales. "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
                self.state.add_highlight(f"Regate milagroso de {player.name}.")
            elif q < 0.32:
                self.state.zone = min(3, z + 1)
                self._crowd_react("ovacion", 1.5)
                self._speak(
                    f"¡Técnica depurada de {player.name}! Se saca al marcador de encima. "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
                self.state.add_highlight(f"Técnica depurada de {player.name}.")
            else:
                self.state.zone = min(3, z + 1)
                self._speak(
                    f"{player.name} regatea a su marcador. Ahora {ZONES_OWN[self.state.zone]}."
                )
        else:
            self._speak(f"{player.name} pierde el balón en el regate.")
            self._lose_possession()

    def _carry_ball(self, z: int, att_diff: float) -> None:
        player = self.state.pick_player(
            0, {"MED": 3.0, "DEL": 2.5, "DEF": 1.0, "POR": 0.0}
        )
        self._sfx("balon")
        if self.state.roll(0.70 + att_diff * 0.6):
            self.state.zone = min(3, z + 1)
            self._speak(
                f"{player.name} conduce el balón con la cabeza levantada y avanza. "
                f"Ahora {ZONES_OWN[self.state.zone]}."
            )
            if self.state.roll(0.15):
                self._crowd_react("ovacion", 1.2)
        elif self.state.roll(0.30):
            self._speak(
                f"¡{player.name} es derribado por el rival mientras conduce el balón!"
            )
            self.set_pieces.foul(1, z)
        else:
            self._speak(
                f"El rival le quita el balón a {player.name} mientras lo conduce."
            )
            self._lose_possession()

    def _long_pass_to_lone_striker(self, striker: Player, mid_diff: float) -> None:
        passer = self.state.pick_player(
            0, {"MED": 4.0, "DEL": 1.0, "DEF": 1.5, "POR": 0.0}
        )
        self._sfx("balon")
        if self.state.roll(0.10):
            self._sfx("silbato")
            self._speak(
                f"El pase largo de {passer.name} deja a {striker.name} en fuera de juego."
            )
            self.state.possession, self.state.zone = 1, 1
        elif self.state.roll(0.68 + mid_diff * 0.6):
            self._crowd_react("ovacion", 1.6)
            self._speak(
                f"¡Gran pase largo de {passer.name}! {striker.name} controla solo, dentro del área."
            )
            self.state.zone = 3
            self.state.add_highlight(
                f"Pase largo de {passer.name} para {striker.name}, que quedó solo frente al portero."
            )
            self.set_pieces.shot(0, 3, mult=1.25, shooter=striker)
        else:
            self._speak(
                f"El pase largo de {passer.name} para {striker.name} sale muy largo y el portero rival lo recoge."
            )
            self._lose_possession(False)

    def _quiet_attack(self, style: int) -> None:
        z_txt = ZONES_OWN[self.state.zone]
        if style == 0:
            self._sfx("balon")
            rec = self.state.pick_receiver(0, "corto")
            self._speak(
                f"Pase corto para {rec.name} y mantienes la posesión, todavía {z_txt}."
            )
        elif style == 1:
            self._sfx("balon")
            self._speak(
                f"Cambias de lado con un pase largo, sin ganar terreno. Sigues {z_txt}."
            )
        elif style == 2:
            self._speak(
                f"Te quedas con el balón y esperas el momento. Sigues {z_txt}."
            )
        else:
            self._speak(
                f"Conduces el balón unos metros, sin ganar terreno. Sigues {z_txt}."
            )

    def _quiet_defense(self, style: int) -> None:
        rival = self.state.team_name(1)
        if style == 0:
            self._speak(f"Presionas y {rival} se deshace del balón sin avanzar.")
        elif style == 1:
            self._speak(
                f"Mantienes el bloque y {rival} mueve el balón sin encontrar espacios."
            )
        elif style == 2:
            self._speak(
                f"Sales a marcar y {rival} no encuentra con quién combinar. El balón no avanza."
            )
        else:
            self._speak(
                f"Esperas para salir al contraataque, pero {rival} conserva el balón."
            )

    def _attack_turn(self) -> None:
        z = self.state.zone
        self._speak(f"Minuto {self.state.minute()}. Tienes el balón {ZONES_OWN[z]}.")
        lone_striker: Player | None = None
        if z in (1, 2) and not self.state.auto and self.state.roll(0.13 / self.state.factor):
            lone_striker = self.state.pick_player(
                0, {"DEL": 6.0, "MED": 1.5, "DEF": 0.0, "POR": 0.0}
            )
            self._speak(
                f"¡{lone_striker.name} está solo en el área, frente al portero! "
                "Puedes habilitarlo con un pase largo."
            )

        ops: list[str] = []
        keys: list[str] = []
        weights: list[int] = []

        def add(key: str, label: str, weight: int) -> None:
            keys.append(key)
            ops.append(label)
            weights.append(weight)

        add("corto", "Pase corto", 40)
        add("filtrado", "Pase filtrado", 17)
        add("largo", "Pase largo", 15)
        add("regate", "Regate", 20)
        add(
            "disparo",
            "Disparo a puerta" if z >= 2 else "Disparo de lejos",
            {0: 1, 1: 3, 2: 20, 3: 55}[z],
        )
        add("conducir", "Conducir el balón", 18)
        if z >= 2:
            add("centro", "Centro al área", 14)
        if z >= 3:
            add("chilena", "Intentar una chilena", 2)
        if lone_striker is not None:
            add(
                "solo",
                f"Pase largo a {lone_striker.name}, que está solo frente al portero",
                45,
            )

        quick_keys = {
            "f": keys.index("filtrado"),
            "r": keys.index("regate"),
            "d": keys.index("disparo"),
            "b": keys.index("conducir"),
        }
        choice = self._choose("¿Qué haces?", ops, weights, quick_keys)
        if self.state.abandoned:
            return
        action = keys[choice]

        if self.state.roll(0.004 / self.state.factor):
            self.set_pieces.foul(0, 3 - z, "mano")
            return
        if (
            self.state.factor > 1
            and action in ("corto", "largo", "regate", "conducir")
            and not self.state.roll(1.0 / self.state.factor)
        ):
            self._quiet_attack(
                {"corto": 0, "largo": 1, "regate": 2, "conducir": 3}[action]
            )
            return

        mid_diff = (self.state.rating(0, "mid") - self.state.rating(1, "mid")) / 100.0
        att_diff = (self.state.rating(0, "att") - self.state.rating(1, "dfn")) / 100.0

        if action == "corto":
            rec = self.state.pick_receiver(0, "corto")
            if self.state.roll(0.80 + mid_diff * 0.8):
                self.state.zone = min(3, z + 1)
                self._sfx("balon")
                self._speak(
                    f"Pase corto para {rec.name}. Avanzas, ahora {ZONES_OWN[self.state.zone]}."
                )
            else:
                self._speak(f"Pase interceptado, iba para {rec.name}.")
                self._lose_possession()
        elif action == "filtrado":
            self._through_ball(z, mid_diff)
        elif action == "largo":
            if z >= 1 and self.state.roll(0.12):
                self._sfx("silbato")
                self._speak("El pase largo deja al delantero en fuera de juego.")
                self.state.possession, self.state.zone = 1, 1
            elif self.state.roll(
                0.50 + (self.state.rating(0, "mid") - self.state.rating(1, "dfn")) / 100.0 * 0.6
            ):
                self.state.zone = min(3, z + 2)
                self._sfx("balon")
                self._speak(f"¡Gran pase largo! Ahora {ZONES_OWN[self.state.zone]}.")
            else:
                self._speak("El pase largo se pierde.")
                self._lose_possession(False)
        elif action == "regate":
            self._dribble(z, att_diff)
        elif action == "conducir":
            self._carry_ball(z, att_diff)
        elif action == "disparo":
            self.set_pieces.shot(0, z)
        elif action == "centro":
            self.set_pieces.cross(0)
        elif action == "solo" and lone_striker is not None:
            self._long_pass_to_lone_striker(lone_striker, mid_diff)
        else:
            self.set_pieces.shot(0, 3, mult=0.55, bicycle=True)

    def _rival_advance(self, step: int) -> None:
        rz = self.state.zone
        rival = self.state.team_name(1)
        if rz == 2 and self.state.roll(0.30):
            self._speak(f"{rival} prueba suerte desde lejos.")
            self.set_pieces.shot(1, 2)
        elif rz >= 2 and self.state.roll(0.22):
            self.state.possession = 1
            self._speak(f"{rival} llega por la banda.")
            self.set_pieces.cross(1)
        elif rz >= 3 or rz + step > 3:
            self._speak(f"{rival} se planta ante tu portero.")
            self.set_pieces.shot(1, 3)
        else:
            self.state.zone = rz + step
            self._speak(f"{rival} avanza, ahora {ZONES_RIVAL[self.state.zone]}.")

    def _recover_ball(self, text: str, zone: int) -> None:
        self.state.possession, self.state.zone = 0, zone
        self._sfx("balon")
        player = self.state.pick_player(
            0, {"DEF": 3.0, "MED": 3.0, "DEL": 0.8, "POR": 0.0}
        )
        self._speak(f"{text} Lo recupera {player.name}.")
        if self.state.roll(0.35):
            self._crowd_react("ovacion", 1.2)

    def _defense_turn(self) -> None:
        rz = self.state.zone
        self._speak(
            f"Minuto {self.state.minute()}. {self.state.team_name(1)} "
            f"tiene el balón {ZONES_RIVAL[rz]}."
        )
        self._crowd_boo_rival(rz)
        if rz >= 3 and not self.state.auto:
            self._speak("Cuidado: una falta ahora sería penal.")
        options = [
            "Presionar arriba",
            "Defender en bloque",
            "Salir a marcar",
            "Preparar contraataque",
            "Barrida",
            "Falta táctica",
        ]
        choice = self._choose("¿Cómo defiendes?", options, [28, 34, 14, 18, 5, 1], None)
        if self.state.abandoned:
            return
        if self.state.roll(0.004 / self.state.factor):
            self.set_pieces.foul(1, 3 - rz, "mano")
            return
        if (
            self.state.factor > 1
            and choice in (0, 1, 2, 3)
            and not self.state.roll(1.0 / self.state.factor)
        ):
            self._quiet_defense(choice)
            return

        base = 0.24 + (self.state.rating(0, "mid") - self.state.rating(1, "mid")) / 100.0 * 0.6
        if choice == 0:
            if self.state.roll(base + 0.12):
                self._recover_ball(
                    "Presionas arriba y recuperas el balón.",
                    2 if self.state.roll(0.3) else 1,
                )
            else:
                self._speak("La presión no sale bien.")
                self._rival_advance(2 if self.state.roll(0.4) else 1)
        elif choice == 1:
            if self.state.roll(base + 0.06):
                self._recover_ball("Cierras bien y recuperas el balón.", 1)
            else:
                self._rival_advance(1)
        elif choice == 2:
            defender = self.state.pick_player(
                0, {"DEF": 4.0, "MED": 3.0, "DEL": 0.3, "POR": 0.0}
            )
            carrier = self.state.pick_player(
                1, {"DEF": 1.0, "MED": 3.0, "DEL": 2.0, "POR": 0.0}
            )
            self._speak(
                f"Sales a marcar: {defender.name} va a por {carrier.name}, que lleva el balón."
            )
            if self.state.roll(base + 0.08):
                self._recover_ball(
                    f"Marcaje cerrado de {defender.name} y le quita el balón a {carrier.name}.",
                    2 if self.state.roll(0.25) else 1,
                )
            elif self.state.roll(0.07):
                self._speak(f"{defender.name} llega tarde al marcaje.")
                self.set_pieces.foul(0, rz, "normal")
            elif self.state.roll(0.45):
                self._speak(f"{carrier.name} se quita la marca de {defender.name}.")
                self._rival_advance(1)
            else:
                self._speak(
                    f"El marcaje de {defender.name} funciona: {carrier.name} "
                    "no puede avanzar y tiene que pasar atrás."
                )
        elif choice == 3:
            if self.state.roll(base - 0.04):
                self._recover_ball("¡Recuperas y sales al contraataque!", 2)
            else:
                self._rival_advance(1)
        elif choice == 4:
            self._sfx("barrida")
            if self.state.roll(
                0.42 + (self.state.rating(0, "dfn") - self.state.rating(1, "att")) / 100.0 * 0.7
            ):
                self._recover_ball("¡Barrida limpia! Robas el balón.", 1)
            elif self.state.roll(0.70):
                self.set_pieces.foul(0, rz, "barrida")
            else:
                self._speak("El rival te regatea y te pasa de largo.")
                self._rival_advance(2)
        else:
            if self.state.roll(0.92):
                self.set_pieces.foul(0, rz, "tactica")
            else:
                self._speak("El árbitro deja seguir.")
                self._rival_advance(1)

    # ------------------------------------------------------- Match Flow
    def _ambient_foul(self) -> None:
        offender = self.state.rng.choice((0, 1))
        zr = self.state.rng.choices((0, 1, 2, 3), weights=[16, 47, 34, 3])[0]
        self._speak(f"Minuto {self.state.minute()}.")
        self.set_pieces.foul(offender, zr, "mano" if self.state.roll(0.05) else "normal")

    def _play_turn(self) -> None:
        self.subs.check_injuries()
        self.subs.ai_waste_time()
        self.subs.ai_substitutions(1)
        self.subs.ai_substitutions(0)
        self.state.refresh_ratings()
        self.state.possession_turns[self.state.possession] += 1

        roll = self.state.rng.random()
        if roll < 0.12 / self.state.factor:
            self._ambient_foul()
        elif roll < 0.22 / self.state.factor:
            self.set_pieces.throw_in()
        else:
            if self.state.possession == 0:
                self._attack_turn()
            else:
                self._defense_turn()

        self.state.turn += 1
        self.subs.apply_turn_fatigue()

    def _added_time(self) -> int:
        if self.state.suspended_match:
            return 0
        mins = max(1, min(6, int(round(self.state.lost_seconds / 60.0))))
        self.state.lost_seconds = 0.0
        extra_turns = max(1, int(round(mins * 60.0 / self.state.turn_seconds)))
        extra_note = (
            " Se repone también el tiempo que se perdió a propósito."
            if self.state.wasted_seconds >= 30
            else ""
        )
        self.state.wasted_seconds = 0.0
        plural = "" if mins == 1 else "s"
        self._sfx("cambio")
        self._speak(
            f"El cuarto árbitro indica {mins} minuto{plural} de tiempo añadido.{extra_note}"
        )
        for _ in range(extra_turns):
            if self.state.abandoned or self.state.suspended_match:
                break
            self._play_turn()
        return extra_turns

    def _halftime_menu(
        self, next_half: str, summary: tuple[str, list[str]] | None = None
    ) -> None:
        while True:
            options = [
                "Sí, modificar mi alineación",
                f"No, comenzar el {next_half}",
                "Leer mi alineación en el campo",
            ]
            if summary:
                options.append("Repetir el resumen")
            choice = self.io.choose(
                f"Descanso. {self.subs.remaining_subs_text(0)}. "
                f"¿Quieres modificar tu alineación antes del {next_half}?",
                options,
                shortcuts=self.build_shortcuts(),
            )
            if choice is None:
                continue
            if choice == 1:
                return
            if choice == 0:
                self.subs.open_substitution_menu()
            elif choice == 2:
                self.subs.read_lineup()
            elif choice == 3 and summary:
                self.io.read_summary_stream(summary[0], summary[1])

    def _halftime(self, ended_half: str, next_half: str) -> None:
        for pid in self.state.fatigue:
            self.state.fatigue[pid] = max(0.0, self.state.fatigue[pid] - 15.0)
        self._sfx("silbato_descanso")
        self._speak(f"Final del {ended_half}.")
        self._speak(self.state.scoreboard_text())
        summary: tuple[str, list[str]] | None = None
        if ended_half == "primer tiempo":
            self.state.halftime_snapshot = self.state.snapshot()
            summary = (
                "Resumen del primer tiempo",
                self.state.summary_lines(
                    "Resumen del primer tiempo", self.state.halftime_snapshot
                ),
            )
        if not self.state.auto:
            if summary:
                self.io.read_summary_stream(summary[0], summary[1])
            self._halftime_menu(next_half, summary)
        self._sfx("silbato")
        self._speak(f"Comienza el {next_half}.")

    def _announce_aggregate(self) -> None:
        agg = self.state.aggregate()
        if self.state.penalties:
            winner = self.state.team_name(
                0 if self.state.penalties[0] > self.state.penalties[1] else 1
            )
            self._speak(
                f"Global {agg[0]} a {agg[1]}. Pasa {winner} en los penales."
            )
            return
        winner_idx = self.state.decided_winner()
        if winner_idx is None:
            return
        if agg[0] == agg[1] and self.state.table_advantage is not None:
            self._speak(
                f"Global empatado {agg[0]} a {agg[1]}. Pasa "
                f"{self.state.team_name(winner_idx)} por estar mejor ubicado en la tabla."
            )
        elif agg[0] == agg[1]:
            self._speak(
                f"Global empatado {agg[0]} a {agg[1]}. Pasa "
                f"{self.state.team_name(winner_idx)} por tener más goles de visitante."
            )
        else:
            self._speak(
                f"Global {self.state.team_name(0)} {agg[0]}, "
                f"{self.state.team_name(1)} {agg[1]}. Pasa {self.state.team_name(winner_idx)}."
            )

    def _cleanup(self) -> None:
        self.io.stop_crowd()
        for team_idx in (0, 1):
            for player in self.state.teams[team_idx].players:
                if (
                    player.suspended_matches > 0
                    and id(player) in self.state.excluded_suspended[team_idx]
                ):
                    player.suspended_matches -= 1

    def play(self) -> MatchResult:
        """Run the match from kickoff to final whistle and return MatchResult."""
        self._sfx("silbato")
        self.io.update_hud(self.state.hud_status())
        if not self.state.muted:
            self.io.play_theme("musica_versus", volume=0.7)
        self._speak(
            f"{self.state.title}. {self.state.team_name(0)} contra {self.state.team_name(1)}. "
            "Pulsa F1 para ver los atajos. Con la tecla C haces cambios de jugadores."
        )
        if self.state.stadium:
            self._speak(f"Hoy se juega en {self.state.stadium}.")
        if self.state.attendance:
            self._speak(self.state.attendance[0])
        if self.state.first_leg is not None:
            home_msg = (
                "Hoy juegas de local"
                if self.state.home_index == 0
                else "Hoy juegas de visitante"
            )
            self._speak(
                f"Partido de vuelta. En la ida, {self.state.team_name(0)} "
                f"{self.state.first_leg[0]}, {self.state.team_name(1)} "
                f"{self.state.first_leg[1]}. {home_msg}."
            )
            if self.state.table_advantage is not None:
                self._speak(
                    f"Si el global queda empatado, pasa "
                    f"{self.state.team_name(self.state.table_advantage)} "
                    "por estar mejor ubicado en la tabla, sin tiempo extra."
                )

        if not self.state.muted:
            self.io.pause(4500)
            self.io.stop_music(1200)
            factor = self.state.attendance[1] if self.state.attendance else 1.0
            self.io.start_crowd(factor)

        self.state.possession = self.state.rng.choice((0, 1))
        self.state.zone = 1

        try:
            while (
                self.state.turn < self.state.total_turns
                and not self.state.abandoned
                and not self.state.suspended_match
            ):
                if self.state.turn == self.state.halftime_turn:
                    self.state.halftime_turn = -1
                    self.state.total_turns += self._added_time()
                    if self.state.abandoned or self.state.suspended_match:
                        break
                    self._halftime("primer tiempo", "segundo tiempo")
                self._play_turn()

            if not self.state.abandoned:
                self._added_time()

            if (
                not self.state.abandoned
                and not self.state.suspended_match
                and self.state.knockout
                and self.state.decided_winner() is None
            ):
                skip_et = self.state.no_extra_time and self.state.first_leg is not None
                if skip_et:
                    self._speak(
                        "El global sigue igualado. En esta competición no hay tiempo extra: "
                        "se define en los penales."
                    )
                elif self.state.first_leg is not None:
                    msg = (
                        "El global sigue igualado, también en goles de visitante. Habrá tiempo extra."
                        if self.state.away_goals_rule
                        else "El global sigue igualado. Habrá tiempo extra."
                    )
                    self._speak(msg)
                else:
                    self._speak(
                        "Empate al final del tiempo reglamentario. Habrá tiempo extra."
                    )
                self.state.add_highlight(
                    "Se va directo a los penales." if skip_et else "Se va al tiempo extra."
                )
                self._sfx("silbato")
                base_turn = self.state.turn
                self.state.total_turns = base_turn + (0 if skip_et else 15 * self.state.factor)
                self.state.halftime_turn = base_turn + 7 * self.state.factor
                while (
                    self.state.turn < self.state.total_turns
                    and not self.state.abandoned
                    and not self.state.suspended_match
                ):
                    if self.state.turn == self.state.halftime_turn:
                        self.state.halftime_turn = -1
                        self._halftime(
                            "primera parte del tiempo extra",
                            "segunda parte del tiempo extra",
                        )
                    self._play_turn()
                if not self.state.abandoned and self.state.decided_winner() is None:
                    self.set_pieces.penalty_shootout()
                    assert self.state.penalties is not None
                    self.state.add_highlight(
                        f"Se decide en los penales: {self.state.team_name(0)} "
                        f"{self.state.penalties[0]}, {self.state.team_name(1)} "
                        f"{self.state.penalties[1]}."
                    )

            self._sfx("silbato_final")
            if not self.state.abandoned:
                self._speak("¡Final del partido!")
                self._speak(self.state.scoreboard_text())
                if self.state.penalties:
                    self._speak(
                        f"Penales: {self.state.team_name(0)} {self.state.penalties[0]}, "
                        f"{self.state.team_name(1)} {self.state.penalties[1]}."
                    )
                if self.state.first_leg is not None:
                    self._announce_aggregate()
        finally:
            self._cleanup()

        if not self.state.muted and not self.state.abandoned:
            g0, g1 = self.state.goals
            if self.state.penalties:
                winner = 0 if self.state.penalties[0] > self.state.penalties[1] else 1
            elif self.state.first_leg is not None:
                winner = self.state.decided_winner()
            elif g0 != g1:
                winner = 0 if g0 > g1 else 1
            else:
                winner = None
            if winner == 0:
                self.io.play_jingle("musica_victoria", 6000)
            elif winner == 1:
                self.io.play_jingle("musica_derrota", 5000)

        return self.state.build_result()
