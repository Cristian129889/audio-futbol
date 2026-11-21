"""Mutable state, probability helpers, ratings, and statistics for a match."""

from __future__ import annotations

import random
from typing import Any

from audiofutbol.domain.match.ports import MatchSettings
from audiofutbol.domain.models import MatchResult, Player, Team

ZONES_OWN: dict[int, str] = {
    0: "en tu área",
    1: "en tu campo",
    2: "en campo rival",
    3: "en el área rival",
}

ZONES_RIVAL: dict[int, str] = {
    0: "en su propia área",
    1: "en su campo",
    2: "en tu campo, cerca de tu área",
    3: "¡dentro de tu área!",
}

PENALTY_DIRECTIONS: tuple[str, ...] = ("a la izquierda", "al centro", "a la derecha")


def prob(p: float, rng: random.Random | None = None) -> bool:
    """Clamped Bernoulli trial matching original AudioFútbol tuning (0.0 .. 0.97)."""
    r = rng.random() if rng is not None else random.random()
    return r < max(0.0, min(0.97, p))


class MatchState:
    """Encapsulates all runtime state of a single football match."""

    def __init__(
        self,
        user_team: Team,
        rival_team: Team,
        settings: MatchSettings,
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
        self.rng = rng or random.Random()
        self.settings = settings
        self.teams: list[Team] = [user_team, rival_team]
        self.attendance = attendance
        self.table_advantage = table_advantage
        self.first_leg = first_leg
        self.home_index = home_index
        self.away_goals_rule = away_goals_rule
        self.no_extra_time = no_extra_time
        self.stadium = stadium
        self.title = title

        self.highlights: list[tuple[int, str]] = []
        self.max_substitutions = max_substitutions
        self.substitutions_used: list[int] = [0, 0]
        self.subbed_off: list[set[int]] = [set(), set()]
        self.fatigue: dict[int, float] = {}
        self.injured: set[int] = set()
        self.fatigue_warned: set[int] = set()
        self.last_fatigue_warn_turn: int = -9
        self.excluded_suspended: list[set[int]] = [
            {id(p) for p in team.players if p.suspended_matches > 0}
            for team in self.teams
        ]
        self.xi: list[list[Player]] = [team.starting_xi() for team in self.teams]
        self.ratings: list[dict[str, float]] = [
            self.compute_ratings(0),
            self.compute_ratings(1),
        ]

        self.goals: list[int] = [0, 0]
        self.red_cards: list[int] = [0, 0]
        self.gk_penalty: list[int] = [0, 0]
        self.shots: list[int] = [0, 0]
        self.shots_on_target: list[int] = [0, 0]
        self.corner_streak: list[Any] = [None, 0]
        self.match_yellows: dict[int, int] = {}
        self.scorers: list[tuple[int, Player, int]] = []
        self.cards_log: list[tuple[int, str, str, int]] = []

        self.turn: int = 0
        self.possession: int = 0
        self.zone: int = 1
        self.knockout = knockout
        self.auto = auto
        self.muted = auto
        self.abandoned = False
        self.suspended_match = False
        self.penalties: tuple[int, int] | None = None

        seg = 120 if auto else settings.turn_seconds
        self.turn_seconds = seg if seg in (120, 60, 30) else 60
        self.factor = 120 // self.turn_seconds
        self.goal_factor = {1: 1.0, 2: 0.85, 4: 0.68}[self.factor]
        self.total_turns = 45 * self.factor
        self.halftime_turn = 22 * self.factor

        self.lost_seconds: float = 0.0
        self.wasted_seconds: float = 0.0
        self.time_waste_by_player: dict[int, int] = {}
        self.time_waste_by_team: list[int] = [0, 0]
        self.last_time_waste_turn: dict[int, int] = {}
        self.var_log: list[tuple[int, str]] = []
        self.corners: list[int] = [0, 0]
        self.fouls: list[int] = [0, 0]
        self.possession_turns: list[int] = [0, 0]
        self.fair_play: list[int] = [0, 0]
        self.halftime_snapshot: dict[str, Any] | None = None
        self.last_bicycle_taker: dict[int, Player] = {}

    def roll(self, p: float) -> bool:
        return prob(p, self.rng)

    def team_name(self, team_idx: int) -> str:
        return self.teams[team_idx].name

    def minute(self) -> int:
        return int(self.turn * self.turn_seconds / 60)

    def effective_overall(self, player: Player) -> float:
        val = player.overall * (1.0 - 0.12 * self.fatigue.get(id(player), 0.0) / 100.0)
        if id(player) in self.injured:
            val *= 0.6
        return val

    def compute_ratings(self, team_idx: int) -> dict[str, float]:
        xi = self.xi[team_idx]

        def avg(pos: str) -> float:
            vals = [self.effective_overall(p) for p in xi if p.position == pos]
            if vals:
                return sum(vals) / len(vals)
            return sum(self.effective_overall(p) for p in xi) / max(1, len(xi))

        d, m, f, gk = avg("DEF"), avg("MED"), avg("DEL"), avg("POR")
        return {
            "att": 0.6 * f + 0.4 * m,
            "mid": 0.6 * m + 0.2 * f + 0.2 * d,
            "dfn": 0.7 * d + 0.3 * m,
            "gk": gk,
        }

    def refresh_ratings(self) -> None:
        self.ratings = [self.compute_ratings(0), self.compute_ratings(1)]

    def rating(self, team_idx: int, key: str) -> float:
        val = self.ratings[team_idx][key]
        if key == "gk":
            return val + self.gk_penalty[team_idx]
        return val * (1.0 - 0.07 * self.red_cards[team_idx])

    def goalkeeper(self, team_idx: int) -> Player | None:
        for player in self.xi[team_idx]:
            if player.position == "POR":
                return player
        return None

    def pick_player(self, team_idx: int, weights_by_pos: dict[str, float]) -> Player:
        xi = self.xi[team_idx]
        weights = [
            weights_by_pos.get(p.position, 1.0) * (p.overall / 70.0) ** 2 for p in xi
        ]
        if sum(weights) <= 0:
            weights = [1.0] * len(xi)
        return self.rng.choices(xi, weights=weights)[0]

    def pick_scorer(self, team_idx: int, header: bool = False) -> Player:
        weights = (
            {"DEL": 3.0, "MED": 1.2, "DEF": 2.0, "POR": 0.0}
            if header
            else {"DEL": 5.0, "MED": 2.2, "DEF": 0.7, "POR": 0.0}
        )
        return self.pick_player(team_idx, weights)

    def pick_receiver(self, team_idx: int, pass_type: str) -> Player:
        z = self.zone
        if pass_type == "filtrado":
            weights = {"DEL": 4.0, "MED": 2.5, "DEF": 0.1, "POR": 0.0}
        elif z <= 1:
            weights = {"DEF": 3.0, "MED": 3.0, "DEL": 0.6, "POR": 0.3}
        elif z == 2:
            weights = {"MED": 3.0, "DEL": 2.5, "DEF": 0.6, "POR": 0.0}
        else:
            weights = {"DEL": 3.5, "MED": 2.5, "DEF": 0.4, "POR": 0.0}
        return self.pick_player(team_idx, weights)

    def pick_bicycle_taker(self, team_idx: int) -> Player:
        outfield = [p for p in self.xi[team_idx] if p.position != "POR"] or list(
            self.xi[team_idx]
        )
        last = self.last_bicycle_taker.get(team_idx)
        weights = []
        for p in outfield:
            w = {"DEL": 3.0, "MED": 2.5, "DEF": 1.6}.get(p.position, 1.0)
            if p is last:
                w *= 0.25
            weights.append(w)
        chosen = self.rng.choices(outfield, weights=weights)[0]
        self.last_bicycle_taker[team_idx] = chosen
        return chosen

    def add_highlight(self, text: str) -> None:
        self.highlights.append((self.minute(), text))

    def aggregate(self) -> list[int]:
        assert self.first_leg is not None
        return [self.first_leg[0] + self.goals[0], self.first_leg[1] + self.goals[1]]

    def decided_winner(self) -> int | None:
        if self.first_leg is None:
            if self.goals[0] != self.goals[1]:
                return 0 if self.goals[0] > self.goals[1] else 1
            return None
        agg = self.aggregate()
        if agg[0] != agg[1]:
            return 0 if agg[0] > agg[1] else 1
        if self.table_advantage is not None:
            return self.table_advantage
        if self.away_goals_rule and self.home_index is not None:
            away = [0, 0]
            away[self.home_index] = self.first_leg[self.home_index]
            away[1 - self.home_index] = self.goals[1 - self.home_index]
            if away[0] != away[1]:
                return 0 if away[0] > away[1] else 1
        return None

    def scoreboard_text(self) -> str:
        text = f"{self.team_name(0)} {self.goals[0]}, {self.team_name(1)} {self.goals[1]}"
        if self.first_leg is not None:
            agg = self.aggregate()
            text += f". Global: {self.team_name(0)} {agg[0]}, {self.team_name(1)} {agg[1]}"
        return text

    def hud_status(self) -> str:
        base = (
            f"Min {self.minute()}' | {self.team_name(0)} {self.goals[0]} - "
            f"{self.goals[1]} {self.team_name(1)}"
        )
        if self.first_leg is not None:
            agg = self.aggregate()
            base += f" (Global {agg[0]}-{agg[1]})"
        return base

    @staticmethod
    def format_on_target(count: int) -> str:
        if count == 0:
            return "ninguno a puerta"
        if count == 1:
            return "uno a puerta"
        return f"{count} a puerta"

    def shots_text(self, team_idx: int) -> str:
        return (
            f"Tiros de {self.team_name(team_idx)}: {self.shots[team_idx]}, "
            f"{self.format_on_target(self.shots_on_target[team_idx])}."
        )

    def can_chain_corner(self, team_idx: int, consecutive: bool) -> bool:
        return not (
            consecutive
            and self.corner_streak[0] == team_idx
            and self.corner_streak[1] >= 4
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "goles": list(self.scorers),
            "tarjetas": list(self.cards_log),
            "tiros": list(self.shots),
            "tiros_p": list(self.shots_on_target),
            "corners": list(self.corners),
            "faltas": list(self.fouls),
            "pos": list(self.possession_turns),
            "fp": list(self.fair_play),
            "marcador": list(self.goals),
        }

    @staticmethod
    def possession_pct(pos: list[int]) -> tuple[int, int]:
        total = pos[0] + pos[1]
        if total <= 0:
            return 50, 50
        pct0 = int(round(100.0 * pos[0] / total))
        return pct0, 100 - pct0

    def stats_lines(self, snap: dict[str, Any]) -> list[str]:
        n0, n1 = self.team_name(0), self.team_name(1)
        pa, pb = self.possession_pct(snap["pos"])
        out = [
            f"Posesión del balón: {n0} {pa} por ciento, {n1} {pb} por ciento.",
            (
                f"Tiros: {n0} {snap['tiros'][0]}, {self.format_on_target(snap['tiros_p'][0])}. "
                f"{n1} {snap['tiros'][1]}, {self.format_on_target(snap['tiros_p'][1])}."
            ),
            f"Tiros de esquina: {n0} {snap['corners'][0]}, {n1} {snap['corners'][1]}. Total {sum(snap['corners'])}.",
            f"Faltas: {n0} {snap['faltas'][0]}, {n1} {snap['faltas'][1]}. Total {sum(snap['faltas'])}.",
        ]
        yellows = [
            t
            for t in snap["tarjetas"]
            if t[2].startswith("amarilla") or t[2] == "segunda amarilla"
        ]
        reds = [
            t for t in snap["tarjetas"] if t[2] in ("roja", "segunda amarilla")
        ]
        y0 = sum(1 for t in yellows if t[0] == 0)
        y1 = sum(1 for t in yellows if t[0] == 1)
        out.append(
            f"Tarjetas amarillas: {n0} {y0}, {n1} {y1}. Total {len(yellows)}."
        )
        if yellows:
            out.append(
                "Amonestados: "
                + "; ".join(
                    f"{name}, de {self.team_name(i)}, minuto {m}"
                    for i, name, _, m in yellows
                )
                + "."
            )
        else:
            out.append("Nadie fue amonestado.")

        r0 = sum(1 for t in reds if t[0] == 0)
        r1 = sum(1 for t in reds if t[0] == 1)
        out.append(f"Expulsados: {n0} {r0}, {n1} {r1}.")
        for i, name, card_type, m in reds:
            reason = "doble amarilla" if card_type == "segunda amarilla" else "roja directa"
            out.append(f"Expulsado {name}, de {self.team_name(i)}, minuto {m}, por {reason}.")

        out.append(
            f"Puntos de fair play, menos es mejor: {n0} {snap['fp'][0]}, {n1} {snap['fp'][1]}."
        )
        return out

    def summary_lines(self, title: str, snap: dict[str, Any]) -> list[str]:
        out = [
            f"{title}. {self.team_name(0)} {snap['marcador'][0]}, "
            f"{self.team_name(1)} {snap['marcador'][1]}."
        ]
        if snap["goles"]:
            out.append(
                "Goles: "
                + "; ".join(
                    f"{p.name}, de {self.team_name(i)}, minuto {m}"
                    for i, p, m in snap["goles"]
                )
                + "."
            )
        else:
            out.append("No hubo goles.")
        return out + self.stats_lines(snap)

    def recap_lines(self) -> list[str]:
        out = []
        line = f"Resultado final: {self.team_name(0)} {self.goals[0]}, {self.team_name(1)} {self.goals[1]}"
        if self.penalties:
            line += f", penales {self.penalties[0]} a {self.penalties[1]}"
        out.append(line)
        if self.first_leg is not None:
            agg = self.aggregate()
            out.append(f"Global: {self.team_name(0)} {agg[0]}, {self.team_name(1)} {agg[1]}")
        if not self.highlights:
            out.append("Fue un partido con pocas emociones.")
        for m, txt in self.highlights:
            out.append(f"Minuto {m}. {txt}")
        out.append("Estadísticas del partido.")
        out.extend(self.stats_lines(self.snapshot()))
        return out

    def build_result(self) -> MatchResult:
        goals = (0, 3) if self.abandoned else (self.goals[0], self.goals[1])
        return MatchResult(
            goals=goals,
            penalties=self.penalties,
            abandoned=self.abandoned,
            fair_play=(self.fair_play[0], self.fair_play[1]),
            scorers=[(self.teams[i], p, m) for i, p, m in self.scorers],
            highlights=list(self.highlights),
        )
