"""MatchEngine orchestrator: 2D stereo field, individual duels, rich block defense, tactics & AI."""

from __future__ import annotations

import random
from typing import Callable

from audiofutbol.domain.match.narration import Narrator
from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchIO, MatchSettings
from audiofutbol.domain.match.set_pieces import SetPieceResolver
from audiofutbol.domain.match.state import (
    LANE_SIDE_NAME,
    ZONES_OWN,
    ZONES_RIVAL,
    MatchState,
)
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
        self.narrator = Narrator(self.state.rng)
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

    def _crowd_react(self, event: str, boost: float = 1.0) -> None:
        if self.state.muted or self.state.settings.crowd_volume <= 0:
            return
        self.io.play_sfx(
            event,
            pan=self.state.rng.uniform(-0.5, 0.5),
            volume=min(1.15, max(0.35, boost * 0.65)),
        )

    def _crowd_boo_rival(self, zone: int) -> None:
        if self.state.muted or self.state.settings.crowd_volume <= 0:
            return
        if self.state.roll((0.75 if zone >= 2 else 0.40) / self.state.factor):
            self.io.play_sfx(
                "abucheo",
                pan=self.state.stereo_pan(0.25),
                volume=0.75,
            )

    def _rival_aggression(self) -> float:
        """Adaptive AI urgency: pushes harder when trailing late, steadier when leading."""
        if self.state.total_turns <= 0:
            return 0.0
        progress = self.state.turn / float(self.state.total_turns)
        score = (
            self.state.aggregate()
            if self.state.first_leg is not None
            else self.state.goals
        )
        diff = score[1] - score[0]
        if progress >= 0.72 and diff < 0:
            return 0.14  # Rival trailing late -> throws numbers forward
        if progress >= 0.78 and diff > 0:
            return -0.08  # Rival protecting lead
        return 0.0

    # ------------------------------------------------------- In-match shortcuts
    def build_shortcuts(self) -> dict[str, Callable[[], str | None]]:
        def k_scoreboard() -> None:
            self.io.speak(self.state.scoreboard_text(), interrupt=True)

        def k_time() -> None:
            self.io.speak(f"Minuto {self.state.minute()}", interrupt=True)

        def k_possession() -> None:
            carrier = self.state.ensure_ball_carrier()
            if self.state.possession == 0:
                self.io.speak(
                    f"Tienes el balón {ZONES_OWN[self.state.zone]}, "
                    f"{self.state.lane_text()}. Lo lleva {carrier.name}.",
                    interrupt=True,
                )
            else:
                self.io.speak(
                    f"{self.state.team_name(1)} tiene el balón {ZONES_RIVAL[self.state.zone]}, "
                    f"{self.state.lane_text()}. Lo lleva {carrier.name}.",
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

        def k_tactic() -> None:
            desc = self.state.cycle_tactic()
            self.io.update_hud(self.state.hud_status())
            self.io.speak(f"Postura táctica cambiada. {desc}", interrupt=True)

        def k_context() -> None:
            tp = self.state.tactic_profile()
            sol = (
                f" Solidez del bloque nivel {self.state.block_solidity} de 3."
                if self.state.block_solidity > 0
                else ""
            )
            space = (
                " Tienes espacios abiertos tras descolocar al rival."
                if self.state.space_bonus > 0
                else ""
            )
            self.io.speak(
                f"Clima: {self.state.weather.name_es}. Postura: {tp['label']}. "
                f"El juego está {self.state.lane_text()}. "
                f"{self.state.momentum_summary()}{sol}{space}",
                interrupt=True,
            )

        def k_help() -> None:
            self.io.speak(
                "Atajos: M marcador. T tiempo. P posesión y carril. G goles. A tarjetas. "
                "E tiros y tiros a puerta. S estadísticas. X cambiar táctica en vivo. "
                "N clima, táctica e impulso del partido. H hacer tiempo. V VAR. "
                "C cambios. L alineación. "
                "En ataque: Q cambio de frente, F pase filtrado, R regate, D disparo, B conducir. "
                "En defensa: 1 presionar, 2 bloque, 3 marcar, 4 contraataque, 5 barrida, 6 falta táctica, 7 fuera de juego. "
                "Historial en cualquier pantalla: coma mensaje anterior, punto siguiente, Shift coma primero, Shift punto último. "
                "Control silencia, F2 repite, Escape pausa.",
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
            "x": k_tactic,
            "n": k_context,
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

    # ------------------------------------------------------- Attack Sub-actions
    def _lose_possession(self, can_foul: bool = True, interceptor: Player | None = None) -> None:
        z = self.state.zone
        self.state.space_bonus = 0.0
        if can_foul and z >= 1 and self.state.roll(0.16):
            self._speak("Te derriban.")
            self.set_pieces.foul(1, z, "normal")
        else:
            self.state.shift_momentum(-0.7)
            self.state.set_possession(1, min(2, 3 - z), lane=self.state.lane, carrier=interceptor)

    def _short_pass(self, z: int, carrier: Player, mid_diff: float) -> None:
        rec = self.state.pick_receiver(0, "corto", exclude=carrier)
        marker = self.state.pick_player(1, {"MED": 3.5, "DEF": 3.0, "DEL": 0.6, "POR": 0.0})
        duel = self.state.duel_advantage(carrier, marker)
        bonus = self.state.space_bonus * 0.5
        self.state.space_bonus = max(0.0, self.state.space_bonus - 0.05)

        if self.state.roll(0.78 + mid_diff * 0.75 + duel * 0.5 + bonus):
            self.state.zone = min(3, z + 1)
            # Subtle lateral ball movement
            if self.state.roll(0.45):
                self.state.lane = self.state.rng.choice((-1, 0, 1))
            self.state.ball_carrier = rec
            self._sfx("balon", self.state.stereo_pan())
            if self.state.roll(0.20):
                self.state.space_bonus = min(0.14, self.state.space_bonus + 0.06)
                self.state.shift_momentum(0.8)
                self._crowd_react("ovacion", 1.1)
                self._speak(
                    self.narrator.say(
                        "short_pass_one_two",
                        pas=carrier.name,
                        rec=rec.name,
                        zona=f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}",
                    )
                )
            else:
                self._speak(
                    self.narrator.say(
                        "short_pass_ok",
                        pas=carrier.name,
                        rec=rec.name,
                        zona=f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}",
                    )
                )
        else:
            self.state.interceptions[1] += 1
            self._speak(
                self.narrator.say(
                    "short_pass_cut",
                    def_=marker.name,
                    pas=carrier.name,
                    rec=rec.name,
                    rival=self.state.team_name(1),
                )
            )
            self._lose_possession(can_foul=True, interceptor=marker)

    def _switch_play(self, carrier: Player, mid_diff: float) -> None:
        """Cross-field switch to the opposite wing: stereo pan + disorganizes rival defense."""
        target_lane = -self.state.lane if self.state.lane != 0 else self.state.rng.choice((-1, 1))
        rec = self.state.pick_player(
            0, {"DEL": 3.5, "MED": 3.5, "DEF": 1.2, "POR": 0.0}, exclude=carrier
        )
        marker = self.state.pick_player(1, {"MED": 3.0, "DEF": 3.0, "DEL": 0.5, "POR": 0.0})
        p = (0.72 + mid_diff * 0.65 + self.state.duel_advantage(carrier, marker) * 0.4) * self.state.weather.long_pass_mod

        self._sfx("balon", target_lane * 0.85)
        if self.state.roll(p):
            self.state.lane = target_lane
            self.state.zone = min(3, max(self.state.zone, 2) if self.state.zone >= 1 else 1)
            self.state.ball_carrier = rec
            self.state.space_bonus = min(0.18, self.state.space_bonus + 0.12)
            self.state.shift_momentum(0.9)
            self._speak(
                self.narrator.say(
                    "switch_play_ok",
                    pas=carrier.name,
                    rec=rec.name,
                    banda=LANE_SIDE_NAME[target_lane],
                )
            )
        else:
            self.state.interceptions[1] += 1
            self._speak(
                self.narrator.say(
                    "switch_play_cut",
                    pas=carrier.name,
                    def_=marker.name,
                    rival=self.state.team_name(1),
                )
            )
            self._lose_possession(can_foul=False, interceptor=marker)

    def _through_ball(self, z: int, carrier: Player, mid_diff: float) -> None:
        rec = self.state.pick_receiver(0, "filtrado", exclude=carrier)
        marker = self.state.pick_player(1, {"DEF": 4.5, "MED": 2.0, "DEL": 0.1, "POR": 0.0})
        self._sfx("balon", self.state.stereo_pan())
        if z >= 1 and self.state.roll(0.13):
            self._sfx("silbato")
            self._speak(
                f"Pase filtrado de {carrier.name} para {rec.name}, pero estaba en fuera de juego."
            )
            self.state.space_bonus = 0.0
            self.state.set_possession(1, 1)
            return

        duel = self.state.duel_advantage(carrier, marker)
        bonus = self.state.space_bonus * 0.7
        self.state.space_bonus = 0.0
        if self.state.roll(0.38 + mid_diff * 0.75 + duel * 0.55 + bonus):
            self.state.zone = min(3, z + 2)
            self.state.lane = 0
            self.state.ball_carrier = rec
            self.state.shift_momentum(1.3)
            if self.state.roll(0.24):
                self._crowd_react("ovacion", 2.0)
                self._speak(
                    f"¡Súper pase de {carrier.name} para {rec.name}! Rompe toda la defensa. "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
                self.state.add_highlight(f"Súper pase de {carrier.name} para {rec.name}.")
                self.state.space_bonus = 0.10
            else:
                self._speak(
                    f"¡Pase filtrado de {carrier.name} para {rec.name}! "
                    f"Ahora {ZONES_OWN[self.state.zone]}."
                )
        else:
            self.state.interceptions[1] += 1
            self._speak(
                f"El pase filtrado de {carrier.name} hacia {rec.name} es cortado por {marker.name}."
            )
            self._lose_possession(can_foul=False, interceptor=marker)

    def _dribble(self, z: int, carrier: Player, att_diff: float) -> None:
        marker = self.state.pick_player(1, {"DEF": 4.0, "MED": 3.0, "DEL": 0.3, "POR": 0.0})
        self._sfx("regate", self.state.stereo_pan())
        duel = self.state.duel_advantage(carrier, marker)
        bonus = self.state.space_bonus * 0.6
        self.state.space_bonus = max(0.0, self.state.space_bonus - 0.06)

        if self.state.roll(0.47 + att_diff * 0.70 + duel * 0.75 + bonus):
            self.state.dribbles_won[0] += 1
            q = self.state.rng.random()
            if q < 0.15:
                self.state.zone = min(3, z + 2)
                self.state.shift_momentum(1.8)
                self._crowd_react("ovacion", 2.0)
                self._speak(
                    self.narrator.say(
                        "dribble_magic",
                        jug=carrier.name,
                        def_=marker.name,
                        zona=f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}",
                    )
                )
                self.state.add_highlight(f"Regate milagroso de {carrier.name}.")
            elif q < 0.35:
                self.state.zone = min(3, z + 1)
                self.state.shift_momentum(1.1)
                self._crowd_react("ovacion", 1.5)
                self._speak(
                    self.narrator.say(
                        "dribble_skill",
                        jug=carrier.name,
                        def_=marker.name,
                        zona=f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}",
                    )
                )
                self.state.add_highlight(f"Técnica depurada de {carrier.name}.")
            else:
                self.state.zone = min(3, z + 1)
                self.state.shift_momentum(0.6)
                self._speak(
                    self.narrator.say(
                        "dribble_ok",
                        jug=carrier.name,
                        def_=marker.name,
                        zona=f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}",
                    )
                )
            self.state.ball_carrier = carrier
        else:
            self._speak(
                self.narrator.say("dribble_lost", def_=marker.name, jug=carrier.name)
            )
            self._lose_possession(can_foul=True, interceptor=marker)

    def _carry_ball(self, z: int, carrier: Player, att_diff: float) -> None:
        marker = self.state.pick_player(1, {"DEF": 3.5, "MED": 3.5, "DEL": 0.4, "POR": 0.0})
        self._sfx("balon", self.state.stereo_pan())
        duel = self.state.duel_advantage(carrier, marker)
        if self.state.roll(0.68 + att_diff * 0.55 + duel * 0.45 + self.state.space_bonus * 0.5):
            self.state.zone = min(3, z + 1)
            self.state.ball_carrier = carrier
            self.state.shift_momentum(0.4)
            self._speak(
                self.narrator.say(
                    "carry_ok",
                    jug=carrier.name,
                    banda_txt=self.state.lane_text(),
                    zona=ZONES_OWN[self.state.zone],
                )
            )
            if self.state.roll(0.16):
                self._crowd_react("ovacion", 1.2)
        elif self.state.roll(0.32):
            self._speak(
                f"¡{carrier.name} es derribado por {marker.name} mientras conduce el balón!"
            )
            self.set_pieces.foul(1, z)
        else:
            self._speak(
                self.narrator.say("carry_lost", def_=marker.name, jug=carrier.name)
            )
            self._lose_possession(can_foul=True, interceptor=marker)

    def _long_pass_to_lone_striker(
        self, carrier: Player, striker: Player, mid_diff: float
    ) -> None:
        self._sfx("balon", self.state.stereo_pan())
        if self.state.roll(0.10):
            self._sfx("silbato")
            self._speak(
                f"El pase largo de {carrier.name} deja a {striker.name} en fuera de juego."
            )
            self.state.set_possession(1, 1)
        elif self.state.roll((0.68 + mid_diff * 0.6) * self.state.weather.long_pass_mod):
            self._crowd_react("ovacion", 1.6)
            self._speak(
                f"¡Gran pase largo de {carrier.name}! {striker.name} controla solo, dentro del área."
            )
            self.state.zone = 3
            self.state.lane = 0
            self.state.ball_carrier = striker
            self.state.add_highlight(
                f"Pase largo de {carrier.name} para {striker.name}, que quedó solo frente al portero."
            )
            self.set_pieces.shot(0, 3, mult=1.25, shooter=striker)
        else:
            self._speak(
                f"El pase largo de {carrier.name} para {striker.name} sale muy largo y el portero rival lo recoge."
            )
            self._lose_possession(False)

    def _quiet_attack(self, style: int, carrier: Player) -> None:
        z_txt = f"{ZONES_OWN[self.state.zone]}, {self.state.lane_text()}"
        rec = self.state.pick_receiver(0, "corto", exclude=carrier)
        marker = self.state.pick_player(1, {"DEF": 3.0, "MED": 3.0, "DEL": 0.4, "POR": 0.0})
        if style == 0:
            self._sfx("balon", self.state.stereo_pan())
            self.state.ball_carrier = rec
            self._speak(
                self.narrator.say("quiet_attack_short", pas=carrier.name, rec=rec.name, zona=z_txt)
            )
        elif style == 1:
            self.state.lane = self.state.rng.choice((-1, 0, 1))
            self.state.ball_carrier = rec
            self._sfx("balon", self.state.stereo_pan())
            self._speak(
                self.narrator.say("quiet_attack_long", pas=carrier.name, rec=rec.name, zona=z_txt)
            )
        elif style == 2:
            self._speak(
                self.narrator.say("quiet_attack_dribble", jug=carrier.name, def_=marker.name, zona=z_txt)
            )
        else:
            self._speak(
                self.narrator.say("quiet_attack_carry", jug=carrier.name, zona=z_txt)
            )

    def _attack_turn(self) -> None:
        z = self.state.zone
        carrier = self.state.ensure_ball_carrier(0)
        space_hint = " Tienes espacios abiertos." if self.state.space_bonus >= 0.10 else ""
        self._speak(
            f"Minuto {self.state.minute()}. Tienes el balón {ZONES_OWN[z]}, "
            f"{self.state.lane_text()}. Lo lleva {carrier.name}.{space_hint}"
        )
        lone_striker: Player | None = None
        if z in (1, 2) and not self.state.auto and self.state.roll(0.14 / self.state.factor):
            lone_striker = self.state.pick_player(
                0, {"DEL": 6.0, "MED": 1.5, "DEF": 0.0, "POR": 0.0}, exclude=carrier
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

        add("corto", "Pase corto", 34)
        add("cambio_frente", "Cambio de frente", 14)
        add("filtrado", "Pase filtrado", 17)
        add("largo", "Pase largo", 14)
        add("regate", "Regate", 19)
        add(
            "disparo",
            "Disparo a puerta" if z >= 2 else "Disparo de lejos",
            {0: 1, 1: 3, 2: 20, 3: 55}[z],
        )
        add("conducir", "Conducir el balón", 17)
        if z >= 2:
            add("centro", "Centro al área", 16 if self.state.lane != 0 else 11)
        if z >= 3:
            add("chilena", "Intentar una chilena", 2)
        if lone_striker is not None:
            add(
                "solo",
                f"Pase largo a {lone_striker.name}, que está solo frente al portero",
                45,
            )

        quick_keys = {
            "q": keys.index("cambio_frente"),
            "f": keys.index("filtrado"),
            "r": keys.index("regate"),
            "d": keys.index("disparo"),
            "b": keys.index("conducir"),
        }
        choice = self._choose("¿Qué haces?", ops, weights, quick_keys)
        if self.state.abandoned:
            return
        action = keys[choice]
        # Re-sync ball carrier in case the active player was substituted via 'C' during menu
        carrier = self.state.ensure_ball_carrier(0)
        if lone_striker is not None and (
            lone_striker not in self.state.xi[0] or lone_striker is carrier
        ):
            lone_striker = self.state.pick_player(
                0, {"DEL": 6.0, "MED": 1.5, "DEF": 0.0, "POR": 0.0}, exclude=carrier
            )

        if self.state.roll(0.004 / self.state.factor):
            self.set_pieces.foul(0, 3 - z, "mano")
            return
        if (
            self.state.factor > 1
            and action in ("corto", "largo", "regate", "conducir")
            and not self.state.roll(1.18 / self.state.factor)
        ):
            self._quiet_attack(
                {"corto": 0, "largo": 1, "regate": 2, "conducir": 3}[action],
                carrier,
            )
            return

        mid_diff = (self.state.rating(0, "mid") - self.state.rating(1, "mid")) / 100.0
        att_diff = (self.state.rating(0, "att") - self.state.rating(1, "dfn")) / 100.0

        if action == "corto":
            self._short_pass(z, carrier, mid_diff)
        elif action == "cambio_frente":
            self._switch_play(carrier, mid_diff)
        elif action == "filtrado":
            self._through_ball(z, carrier, mid_diff)
        elif action == "largo":
            if z >= 1 and self.state.roll(0.12):
                self._sfx("silbato")
                self._speak("El pase largo deja al delantero en fuera de juego.")
                self.state.set_possession(1, 1)
            elif self.state.roll(
                (0.50 + (self.state.rating(0, "mid") - self.state.rating(1, "dfn")) / 100.0 * 0.6)
                * self.state.weather.long_pass_mod
            ):
                rec = self.state.pick_receiver(0, "filtrado", exclude=carrier)
                self.state.zone = min(3, z + 2)
                self.state.ball_carrier = rec
                self._sfx("balon", self.state.stereo_pan())
                self._speak(
                    f"¡Gran pase largo de {carrier.name} para {rec.name}! "
                    f"Ahora {ZONES_OWN[self.state.zone]}, {self.state.lane_text()}."
                )
            else:
                self._speak(f"El pase largo de {carrier.name} se pierde.")
                self._lose_possession(False)
        elif action == "regate":
            self._dribble(z, carrier, att_diff)
        elif action == "conducir":
            self._carry_ball(z, carrier, att_diff)
        elif action == "disparo":
            self.set_pieces.shot(0, z, shooter=carrier if carrier.position != "POR" else None)
        elif action == "centro":
            self.set_pieces.cross(0, crosser=carrier)
        elif action == "solo" and lone_striker is not None:
            self._long_pass_to_lone_striker(carrier, lone_striker, mid_diff)
        else:
            self.set_pieces.shot(0, 3, mult=0.55, bicycle=True)

    # ------------------------------------------------------- Defense Sub-actions
    def _rival_dribble(self, carrier: Player) -> None:
        """Rival attempts an individual dribble/gambeta against one of your defenders."""
        defender = self.state.pick_player(
            0, {"DEF": 4.5, "MED": 3.0, "DEL": 0.2, "POR": 0.0}
        )
        self._sfx("regate", self.state.stereo_pan())
        duel = self.state.duel_advantage(carrier, defender)
        if self.state.roll(0.42 + duel * 0.75 + self._rival_aggression() * 0.4):
            self.state.dribbles_won[1] += 1
            self.state.zone = 3
            self.state.lane = 0 if self.state.roll(0.65) else self.state.lane
            self.state.shift_momentum(-1.2)
            self._speak(
                self.narrator.say(
                    "rival_dribble_win", riv=carrier.name, def_=defender.name
                )
            )
            self.set_pieces.shot(1, 3, mult=1.08, shooter=carrier)
        else:
            msg = self.narrator.say(
                "rival_dribble_fail", riv=carrier.name, def_=defender.name
            )
            self._recover_ball(msg, 1, player=defender, space_bonus=0.08)

    def _rival_through_ball(self, carrier: Player) -> None:
        """Rival attempts a incisive through ball behind your back line."""
        striker = self.state.pick_receiver(1, "filtrado", exclude=carrier)
        defender = self.state.pick_player(
            0, {"DEF": 5.0, "MED": 2.0, "DEL": 0.0, "POR": 0.0}
        )
        self._sfx("balon", self.state.stereo_pan())
        duel = self.state.duel_advantage(carrier, defender)
        if self.state.roll(0.14):
            self._sfx("silbato")
            self._speak(
                f"Pase filtrado de {carrier.name} para {striker.name}, pero el delantero rival estaba en fuera de juego."
            )
            self.state.set_possession(0, 1, carrier=defender)
            return
        if self.state.roll(0.42 + duel * 0.65):
            self.state.zone = 3
            self.state.lane = 0
            self.state.ball_carrier = striker
            self._speak(
                self.narrator.say(
                    "rival_through_ball_ok", pas=carrier.name, rec=striker.name
                )
            )
            self.set_pieces.shot(1, 3, mult=1.10, shooter=striker)
        else:
            msg = self.narrator.say(
                "rival_through_ball_cut",
                pas=carrier.name,
                rec=striker.name,
                def_=defender.name,
            )
            self._recover_ball(msg, 1, player=defender, space_bonus=0.06)

    def _rival_advance(self, step: int) -> None:
        """Proactive, varied rival offense: shoots, crosses, dribbles, or threads passes."""
        rz = self.state.zone
        rival = self.state.team_name(1)
        carrier = self.state.ensure_ball_carrier(1)
        urgency = self._rival_aggression()
        impatient = self.state.rival_attack_turns >= 2

        # Inside your box (zone 3): ALWAYS finishes — never endless passing!
        if rz >= 3 or rz + step > 3:
            if self.state.roll(0.28):
                self._rival_dribble(carrier)
            else:
                self._speak(f"{carrier.name}, de {rival}, se perfila dentro del área y busca el gol.")
                self.set_pieces.shot(1, 3, shooter=carrier)
            return

        # Dangerous zone 2 (edge of your box): decisive, varied attacking actions!
        if rz == 2:
            action_weights = [
                ("shot", 30 + (22 if impatient else 0) + int(urgency * 80)),
                ("cross", 28 if self.state.lane != 0 else 20),
                ("dribble", 22 + (10 if impatient else 0)),
                ("through", 18),
                ("one_two", 14 if not impatient else 6),
            ]
            names, wts = zip(*action_weights)
            move = self.state.rng.choices(names, weights=wts)[0]
            if move == "shot":
                self._speak(
                    self.narrator.say("rival_long_shot", riv=carrier.name, rival=rival)
                )
                self.set_pieces.shot(1, 2, mult=1.06, shooter=carrier)
            elif move == "cross":
                self.state.possession = 1
                if self.state.lane == 0:
                    self.state.lane = self.state.rng.choice((-1, 1))
                self._speak(f"{carrier.name} desborda {self.state.lane_text()} y busca el centro.")
                self.set_pieces.cross(1, crosser=carrier)
            elif move == "dribble":
                self._rival_dribble(carrier)
            elif move == "through":
                self._rival_through_ball(carrier)
            else:
                partner = self.state.pick_receiver(1, "filtrado", exclude=carrier)
                self.state.zone = 3
                self.state.lane = 0
                self.state.ball_carrier = partner
                self._sfx("balon", self.state.stereo_pan())
                self._speak(
                    self.narrator.say(
                        "rival_one_two", pas=carrier.name, rec=partner.name, rival=rival
                    )
                )
                self.set_pieces.shot(1, 3, shooter=partner)
            return

        # Zones 0-1: advance purposefully, switch wings, or attempt direct through ball
        if rz == 1 and self.state.roll(0.18 + urgency * 0.35):
            self._rival_through_ball(carrier)
            return

        self.state.zone = min(3, rz + step)
        if self.state.roll(0.55):
            self.state.lane = self.state.rng.choice((-1, 0, 1))
        next_c = self.state.pick_receiver(1, "corto", exclude=carrier)
        self.state.ball_carrier = next_c
        self._sfx("balon", self.state.stereo_pan())
        self._speak(
            f"{rival} progresa con {next_c.name}, ahora {ZONES_RIVAL[self.state.zone]}, "
            f"{self.state.lane_text()}."
        )

    def _recover_ball(
        self,
        text: str,
        zone: int,
        player: Player | None = None,
        space_bonus: float = 0.0,
    ) -> None:
        recoverer = player or self.state.pick_player(
            0, {"DEF": 3.0, "MED": 3.0, "DEL": 0.8, "POR": 0.0}
        )
        self.state.interceptions[0] += 1
        self.state.set_possession(0, zone, lane=self.state.lane, carrier=recoverer)
        if space_bonus > 0:
            self.state.space_bonus = max(self.state.space_bonus, space_bonus)
        if self._rival_aggression() > 0:
            self.state.space_bonus = max(self.state.space_bonus, 0.10)
        self.state.shift_momentum(1.0)
        self._sfx("balon", self.state.stereo_pan())
        if recoverer.name in text:
            self._speak(text)
        else:
            self._speak(f"{text} Lo recupera {recoverer.name}.")
        if self.state.roll(0.35):
            self._crowd_react("ovacion", 1.2)

    def _defend_in_block(self, rz: int, base: float, carrier: Player) -> None:
        """Dynamic 6-outcome block defense with stacking solidity, push-backs, and interceptions."""
        defender = self.state.pick_player(
            0, {"DEF": 4.2, "MED": 3.6, "DEL": 0.4, "POR": 0.0}
        )
        rival = self.state.team_name(1)
        tp = self.state.tactic_profile()
        dfn_edge = (self.state.rating(0, "dfn") - self.state.rating(1, "att")) / 100.0
        duel = self.state.duel_advantage(defender, carrier)
        solidity_bonus = self.state.block_solidity * 0.055
        strength = base + 0.07 + dfn_edge * 0.55 + duel * 0.45 + tp["block_bonus"] + solidity_bonus

        roll = self.state.rng.random()

        # 1) Clean interception between the lines
        if roll < max(0.14, min(0.55, strength)):
            target_z = 2 if (self.state.block_solidity >= 2 and self.state.roll(0.45)) else 1
            bonus = 0.08 if self.state.block_solidity >= 2 else 0.0
            msg = self.narrator.say(
                "block_intercept", def_=defender.name, riv=carrier.name
            )
            self._recover_ball(msg, target_z, player=defender, space_bonus=bonus)
            return

        # 2) Push rival backwards (real spatial rollback!)
        if rz >= 1 and roll < strength + 0.18:
            self.state.zone = max(0, rz - 1)
            self.state.block_solidity = min(3, self.state.block_solidity + 1)
            self.state.shift_momentum(0.7)
            self.state.fatigue[id(carrier)] = min(
                100.0, self.state.fatigue.get(id(carrier), 0.0) + 3.0
            )
            self._sfx("balon", self.state.stereo_pan())
            self._speak(
                self.narrator.say(
                    "block_push_back",
                    rival=rival,
                    riv=carrier.name,
                    def_=defender.name,
                    zona_r=ZONES_RIVAL[self.state.zone],
                )
            )
            return

        # 3) Trap rival against touchline -> win throw-in / turnover
        if roll < strength + 0.27:
            wing = self.state.lane if self.state.lane != 0 else self.state.rng.choice((-1, 1))
            self.state.lane = wing
            msg = self.narrator.say(
                "block_force_wide",
                riv=carrier.name,
                def_=defender.name,
                rival=rival,
                banda=LANE_SIDE_NAME[wing],
            )
            self._recover_ball(msg, 1, player=defender)
            return

        # 4) Force a desperate long shot or forced cross when rival is impatient / in zone 2+
        if rz >= 2 and (self.state.rival_attack_turns >= 2 or roll < strength + 0.40):
            if self.state.lane != 0 and self.state.roll(0.35):
                self._speak(
                    f"Sin hueco por el centro, {carrier.name} busca el centro forzado {self.state.lane_text()}."
                )
                self.set_pieces.cross(1, mult=0.88, crosser=carrier)
                return
            self._speak(self.narrator.say("block_desperate_shot", riv=carrier.name))
            if self.state.roll(0.50 + dfn_edge * 0.5):
                self.state.blocks[0] += 1
                self.state.shots[1] += 1
                self._sfx("barrera", self.state.stereo_pan())
                msg = self.narrator.say(
                    "block_shot_blocked", def_=defender.name, riv=carrier.name
                )
                self._recover_ball(msg, 1, player=defender, space_bonus=0.08)
            else:
                self.set_pieces.shot(1, 2, mult=0.78, shooter=carrier)
            return

        # 5) Hold the line and stack solidity (only on first turn in zone, never loops endlessly)
        if self.state.rival_attack_turns < 2 and roll < strength + 0.52:
            self.state.block_solidity = min(3, self.state.block_solidity + 1)
            self.state.shift_momentum(0.35)
            self._speak(
                self.narrator.say(
                    "block_hold_solid",
                    rival=rival,
                    riv=carrier.name,
                    def_=defender.name,
                )
            )
            return

        # 6) Rival creativity breaks through the block
        self.state.block_solidity = max(0, self.state.block_solidity - 1)
        self._speak(
            self.narrator.say("block_broken", riv=carrier.name, rival=rival)
        )
        self._rival_advance(1)

    def _offside_trap(self, carrier: Player) -> None:
        """High-risk/high-reward offside trap when rival builds in open field."""
        leader = self.state.pick_player(0, {"DEF": 6.0, "MED": 1.0, "DEL": 0.0, "POR": 0.0})
        dfn_edge = (self.state.rating(0, "dfn") - self.state.rating(1, "att")) / 100.0
        success_prob = 0.52 + dfn_edge * 0.65 + self.state.block_solidity * 0.04
        if self.state.roll(success_prob):
            self._sfx("silbato")
            self.state.shift_momentum(1.2)
            self._speak(
                self.narrator.say(
                    "offside_trap_win",
                    riv=carrier.name,
                    def_=leader.name,
                    rival=self.state.team_name(1),
                )
            )
            self.state.set_possession(0, 1, carrier=leader)
        else:
            striker = self.state.pick_scorer(1, header=False)
            self._speak(self.narrator.say("offside_trap_fail", riv=striker.name))
            self.state.zone = 3
            self.state.lane = 0
            self.set_pieces.shot(1, 3, mult=1.22, shooter=striker)

    def _defense_turn(self) -> None:
        rz = self.state.zone
        if rz >= 2:
            self.state.rival_attack_turns += 1
        else:
            self.state.rival_attack_turns = 0
        carrier = self.state.ensure_ball_carrier(1)
        rival = self.state.team_name(1)
        sol_txt = (
            f" Bloque firme, nivel {self.state.block_solidity}."
            if self.state.block_solidity > 0
            else ""
        )
        self._speak(
            f"Minuto {self.state.minute()}. {rival} tiene el balón "
            f"{ZONES_RIVAL[rz]}, {self.state.lane_text()}. Lo lleva {carrier.name}.{sol_txt}"
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
        weights = [26, 34, 15, 17, 5, 1]
        quick_keys = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
        if rz in (1, 2):
            options.append("Trampa del fuera de juego")
            weights.append(5)
            quick_keys["7"] = 6

        choice = self._choose("¿Cómo defiendes?", options, weights, quick_keys)
        if self.state.abandoned:
            return
        if self.state.roll(0.004 / self.state.factor):
            self.set_pieces.foul(1, 3 - rz, "mano")
            return

        # Prevent endless passive turns when rival is already near/in your box!
        if (
            self.state.factor > 1
            and rz <= 1
            and choice in (0, 2, 3)
            and not self.state.roll(1.30 / self.state.factor)
        ):
            defender = self.state.pick_player(0, {"DEF": 3.5, "MED": 3.5, "DEL": 0.4, "POR": 0.0})
            key_map = {
                0: "quiet_defense_press",
                2: "quiet_defense_mark",
                3: "quiet_defense_counter",
            }
            self._speak(
                self.narrator.say(
                    key_map[choice], def_=defender.name, riv=carrier.name, rival=rival
                )
            )
            return

        base = 0.25 + (self.state.rating(0, "mid") - self.state.rating(1, "mid")) / 100.0 * 0.6
        behind_risk = float(self.state.tactic_profile().get("behind_risk", 0.0))

        if choice == 0:
            defender = self.state.pick_player(0, {"DEL": 2.5, "MED": 4.0, "DEF": 2.0, "POR": 0.0})
            # High press burns a touch of extra stamina
            self.state.fatigue[id(defender)] = min(
                100.0, self.state.fatigue.get(id(defender), 0.0) + 2.0
            )
            if self.state.roll(base + 0.13 + self.state.duel_advantage(defender, carrier) * 0.45):
                msg = self.narrator.say("press_win", def_=defender.name, riv=carrier.name, rival=rival)
                self._recover_ball(msg, 2 if self.state.roll(0.38) else 1, player=defender, space_bonus=0.07)
            else:
                self._speak(self.narrator.say("press_fail", riv=carrier.name, rival=rival))
                self._rival_advance(2 if self.state.roll(0.38 + behind_risk) else 1)
        elif choice == 1:
            self._defend_in_block(rz, base, carrier)
        elif choice == 2:
            defender = self.state.pick_player(0, {"DEF": 4.0, "MED": 3.0, "DEL": 0.3, "POR": 0.0})
            duel = self.state.duel_advantage(defender, carrier)
            self._speak(f"Sales a marcar: {defender.name} va a por {carrier.name}.")
            if self.state.roll(base + 0.09 + duel * 0.65):
                msg = self.narrator.say("mark_win", def_=defender.name, riv=carrier.name)
                self._recover_ball(msg, 2 if self.state.roll(0.28) else 1, player=defender)
            elif self.state.roll(0.07 * self.state.weather.slide_foul_mod):
                self._speak(f"{defender.name} llega tarde al marcaje.")
                self.set_pieces.foul(0, rz, "normal")
            elif self.state.roll(0.42 - duel * 0.4):
                self._speak(f"{carrier.name} se quita la marca de {defender.name}.")
                self._rival_advance(1)
            else:
                self._speak(
                    self.narrator.say("mark_stall", def_=defender.name, riv=carrier.name)
                )
        elif choice == 3:
            defender = self.state.pick_player(0, {"DEF": 3.5, "MED": 3.5, "DEL": 0.5, "POR": 0.0})
            bonus_def = 0.06 if self.state.tactic == "defensivo" else 0.0
            if self.state.roll(base - 0.02 + bonus_def + self._rival_aggression() * 0.4):
                msg = self.narrator.say("counter_win", def_=defender.name, rival=rival)
                self._recover_ball(msg, 2, player=defender, space_bonus=0.16)
            else:
                self._rival_advance(1)
        elif choice == 4:
            self._sfx("barrida", self.state.stereo_pan())
            defender = self.state.pick_player(0, {"DEF": 4.5, "MED": 2.5, "DEL": 0.3, "POR": 0.0})
            duel = self.state.duel_advantage(defender, carrier)
            clean_prob = (
                0.42
                + (self.state.rating(0, "dfn") - self.state.rating(1, "att")) / 100.0 * 0.65
                + duel * 0.5
            ) / self.state.weather.slide_foul_mod
            if self.state.roll(clean_prob):
                self._recover_ball(
                    f"¡Barrida limpia de {defender.name}! Roba el balón a {carrier.name}.",
                    1,
                    player=defender,
                )
            elif self.state.roll(0.68 * self.state.weather.slide_foul_mod):
                self.set_pieces.foul(0, rz, "barrida")
            else:
                self._speak(f"{carrier.name} salta la barrida de {defender.name} y pasa de largo.")
                self._rival_advance(2)
        elif choice == 5:
            if self.state.roll(0.92):
                self.set_pieces.foul(0, rz, "tactica")
            else:
                self._speak("El árbitro deja seguir.")
                self._rival_advance(1)
        else:
            self._offside_trap(carrier)

    # ------------------------------------------------------- Match Flow & Dynamic Events
    def _ambient_foul(self) -> None:
        offender = self.state.rng.choice((0, 1))
        zr = self.state.rng.choices((0, 1, 2, 3), weights=[16, 47, 34, 3])[0]
        self._speak(f"Minuto {self.state.minute()}.")
        self.set_pieces.foul(offender, zr, "mano" if self.state.roll(0.05) else "normal")

    def _midfield_clash(self) -> None:
        p0 = self.state.pick_player(0, {"MED": 4.0, "DEF": 2.5, "DEL": 1.5, "POR": 0.0})
        p1 = self.state.pick_player(1, {"MED": 4.0, "DEF": 2.5, "DEL": 1.5, "POR": 0.0})
        self._sfx("choque", self.state.stereo_pan())
        winner_idx = 0 if self.state.roll(0.50 + self.state.duel_advantage(p0, p1)) else 1
        winner_player = p0 if winner_idx == 0 else p1
        self._speak(
            self.narrator.say(
                "midfield_clash",
                jug=p0.name,
                riv=p1.name,
                ganador=self.state.team_name(winner_idx),
            )
        )
        self.state.set_possession(winner_idx, 1, lane=0, carrier=winner_player)

    def _check_late_match_tension(self) -> None:
        if (
            self.state.final_stretch_announced
            or self.state.muted
            or self.state.minute() < 80
        ):
            return
        if abs(self.state.goals[0] - self.state.goals[1]) <= 1:
            self.state.final_stretch_announced = True
            self._crowd_react("ovacion", 1.4)
            self._speak(self.narrator.say("late_tension"))

    def _play_turn(self) -> None:
        self._check_late_match_tension()
        self.subs.check_injuries()
        self.subs.ai_waste_time()
        self.subs.ai_substitutions(1)
        self.subs.ai_substitutions(0)
        self.state.refresh_ratings()
        self.state.possession_turns[self.state.possession] += 1

        roll = self.state.rng.random()
        if roll < 0.11 / self.state.factor:
            self._ambient_foul()
        elif roll < 0.20 / self.state.factor:
            self.set_pieces.throw_in()
        elif roll < 0.24 / self.state.factor:
            self._midfield_clash()
        else:
            if self.state.possession == 0:
                self._attack_turn()
            else:
                self._defense_turn()

        self.state.turn += 1
        self.state.decay_momentum()
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
            "Pulsa F1 para ver los atajos. Con X cambias tu postura táctica y con C haces cambios."
        )
        if self.state.stadium:
            self._speak(f"Hoy se juega en {self.state.stadium}.")
        self._speak(self.state.weather.announcement)
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

        self.state.set_possession(self.state.rng.choice((0, 1)), 1, lane=0)

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
