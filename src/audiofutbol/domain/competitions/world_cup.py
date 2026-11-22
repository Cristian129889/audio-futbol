"""FIFA World Cup competition model (8, 16, 32, or 48 national teams)."""

from __future__ import annotations

import random
from typing import Any

from audiofutbol.domain.match.engine import MatchEngine
from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchSettings
from audiofutbol.domain.models import MatchResult, Team, format_result_text


GROUP_LETTERS = "ABCDEFGHIJKLMNOP"


def fair_play_for_order(res: MatchResult, direct: bool) -> tuple[int, int]:
    fp = res.fair_play
    return (fp[0], fp[1]) if direct else (fp[1], fp[0])


class WorldCup:
    """World Cup tournament supporting 8, 16, 32, or 48 teams (with 8 best thirds)."""

    KEY = "mundial"
    TITLE = "Mundial"
    ROUND_NAMES: dict[int, str] = {
        32: "Dieciseisavos de final",
        16: "Octavos de final",
        8: "Cuartos de final",
        4: "Semifinales",
    }

    def __init__(self, teams: list[Team], user_team: Team, _skip_init: bool = False) -> None:
        self.teams: list[Team] = list(teams)
        self.user_team: Team = user_team
        self.user_eliminated: bool = False
        self.tiebreaker_rnd: dict[str, float] = {
            t.name: random.random() for t in self.teams
        }
        self.table: dict[str, dict[str, int]] = {
            t.name: {"pj": 0, "g": 0, "e": 0, "p": 0, "gf": 0, "gc": 0, "pts": 0, "fp": 0}
            for t in self.teams
        }
        self.history: list[tuple[str, Team, Team, int, int, tuple[int, int] | None]] = []
        self.aggregates: dict[int, tuple[Team, int, Team, int]] = {}
        self.draw_lines: list[str] = []
        self.groups: list[list[Team]] = []
        self.group_count: int = 0
        self.phases: list[tuple[str, str, int]] = []
        self.phase_idx: int = 0
        self.pairs: list[tuple[Team, Team]] = []
        self.completed: dict[int, dict[str, Any]] = {}
        self.alive: list[Team] = []
        self.semi_winners: list[Team] = []
        self.semi_losers: list[Team] = []
        self.third_place: Team | None = None
        self.champion: Team | None = None
        self.podium: tuple[Team, Team, Team] | None = None
        self.qualified_thirds: list[Team] = []
        if not _skip_init:
            self._run_draw()
            self._build_phases()
            self._prepare_phase()

    def _run_draw(self) -> None:
        ordered = sorted(self.teams, key=lambda t: -t.strength())
        g_count = len(ordered) // 4
        self.group_count = g_count
        pots = [ordered[k * g_count : (k + 1) * g_count] for k in range(4)]
        self.groups = [[] for _ in range(g_count)]
        for pot_idx, pot in enumerate(pots):
            shuffled = list(pot)
            random.shuffle(shuffled)
            for g_idx, team in enumerate(shuffled):
                self.groups[g_idx].append(team)
                self.draw_lines.append(
                    f"Bombo {pot_idx + 1}: sale {team.name}, grupo {GROUP_LETTERS[g_idx]}"
                )

    def _build_phases(self) -> None:
        self.phases = [(f"Fase de grupos, jornada {k + 1}", "g", k) for k in range(3)]
        t = 32 if self.group_count == 12 else self.group_count * 2
        while t >= 4:
            self.phases.append((self.ROUND_NAMES[t], "k", t))
            t //= 2
        self.phases.append(("Partido por el tercer lugar", "k3", 2))
        self.phases.append(("La gran final", "kf", 2))

    def phase_name(self) -> str:
        return self.phases[self.phase_idx][0]

    def is_knockout(self) -> bool:
        return self.phases[self.phase_idx][1] != "g"

    def _prepare_phase(self) -> None:
        _, kind, data = self.phases[self.phase_idx]
        self.completed = {}
        if kind == "g":
            schedule = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))[data]
            self.pairs = [
                (group[a], group[b]) for group in self.groups for a, b in schedule
            ]
        elif kind == "k":
            self.pairs = [
                (self.alive[k], self.alive[k + 1]) for k in range(0, len(self.alive), 2)
            ]
        elif kind == "k3":
            self.pairs = [(self.semi_losers[0], self.semi_losers[1])]
        else:
            self.pairs = [(self.semi_winners[0], self.semi_winners[1])]

    def _sort_key(self, team: Team) -> tuple[int, int, int, int, float]:
        row = self.table[team.name]
        return (
            -row["pts"],
            -(row["gf"] - row["gc"]),
            -row["gf"],
            row.get("fp", 0),
            self.tiebreaker_rnd[team.name],
        )

    def standings(self, group: list[Team]) -> list[Team]:
        return sorted(group, key=self._sort_key)

    def group_of(self, team: Team) -> list[Team] | None:
        for group in self.groups:
            if team in group:
                return group
        return None

    def _best_thirds(self, thirds: list[Team]) -> list[Team]:
        return sorted(thirds, key=self._sort_key)[:8]

    def best_thirds_lines(self) -> list[str]:
        thirds = [self.standings(g)[2] for g in self.groups]
        out: list[str] = []
        for idx, team in enumerate(sorted(thirds, key=self._sort_key)):
            row = self.table[team.name]
            grp = self.group_of(team)
            letter = GROUP_LETTERS[self.groups.index(grp)] if grp else "?"
            status = "Pasa" if idx < 8 else "No pasa"
            out.append(
                f"{idx + 1}. {team.name}, tercero del grupo {letter}, {row['pts']} puntos, "
                f"diferencia {row['gf'] - row['gc']:+d}, goles a favor {row['gf']}, "
                f"fair play {row.get('fp', 0)}. {status}"
            )
        return out

    def _pairings_48(
        self, firsts: list[Team], seconds: list[Team], thirds: list[Team]
    ) -> list[tuple[Team, Team]]:
        best: list[tuple[Team, Team]] = []
        for _ in range(300):
            f, sg, tr = list(firsts), list(seconds), list(thirds)
            random.shuffle(f)
            random.shuffle(sg)
            random.shuffle(tr)
            pairs = [(f[k], tr[k]) for k in range(8)]
            pairs += [(f[8 + k], sg[k]) for k in range(4)]
            pairs += [(sg[4 + 2 * k], sg[5 + 2 * k]) for k in range(4)]
            best = pairs
            if all(self.group_of(a) is not self.group_of(b) for a, b in pairs):
                break
        random.shuffle(best)
        return best

    def user_match_index(self) -> int | None:
        if self.user_eliminated:
            return None
        for idx, (a, b) in enumerate(self.pairs):
            if self.user_team in (a, b) and idx not in self.completed:
                return idx
        return None

    def register_result(
        self, idx: int, first_team: Team, res: MatchResult
    ) -> tuple[int, int, tuple[int, int] | None]:
        a, b = self.pairs[idx]
        g0, g1 = res.goals
        ga, gb = (g0, g1) if first_team is a else (g1, g0)
        fpa, fpb = fair_play_for_order(res, first_team is a)
        pen = res.penalties
        if pen and first_team is not a:
            pen = (pen[1], pen[0])
        self.completed[idx] = {"ga": ga, "gb": gb, "pen": pen}
        self.history.append((self.phase_name(), a, b, ga, gb, pen))
        if self.phases[self.phase_idx][1] == "g":
            for team, gf, gc, fp in ((a, ga, gb, fpa), (b, gb, ga, fpb)):
                row = self.table[team.name]
                row["fp"] = row.get("fp", 0) + fp
                row["pj"] += 1
                row["gf"] += gf
                row["gc"] += gc
                if gf > gc:
                    row["g"] += 1
                    row["pts"] += 3
                elif gf == gc:
                    row["e"] += 1
                    row["pts"] += 1
                else:
                    row["p"] += 1
        return ga, gb, pen

    def simulate_remaining(self, settings: MatchSettings | None = None) -> None:
        for idx, (a, b) in enumerate(self.pairs):
            if idx in self.completed:
                continue
            engine = MatchEngine(
                a,
                b,
                io=HeadlessMatchIO(),
                settings=settings or MatchSettings(),
                knockout=self.is_knockout(),
                auto=True,
            )
            self.register_result(idx, a, engine.play())

    def match_winner(self, idx: int) -> Team:
        rec = self.completed[idx]
        a, b = self.pairs[idx]
        if rec["ga"] != rec["gb"]:
            return a if rec["ga"] > rec["gb"] else b
        if rec["pen"]:
            return a if rec["pen"][0] > rec["pen"][1] else b
        return random.choice((a, b))

    def match_loser(self, idx: int) -> Team:
        a, b = self.pairs[idx]
        return b if self.match_winner(idx) is a else a

    def close_phase(self) -> str:
        _, kind, data = self.phases[self.phase_idx]
        notice = ""
        if kind == "g" and data == 2:
            firsts, seconds, thirds = [], [], []
            for g in self.groups:
                ranked = self.standings(g)
                firsts.append(ranked[0])
                seconds.append(ranked[1])
                thirds.append(ranked[2])
            if self.group_count == 12:
                self.qualified_thirds = self._best_thirds(thirds)
                pairs = self._pairings_48(firsts, seconds, self.qualified_thirds)
            else:
                half1 = [(firsts[k], seconds[k + 1]) for k in range(0, self.group_count, 2)]
                half2 = [(firsts[k + 1], seconds[k]) for k in range(0, self.group_count, 2)]
                pairs = half1 + half2
            self.alive = [team for pair in pairs for team in pair]
            if self.user_team not in self.alive:
                self.user_eliminated = True
                notice = f"{self.user_team.name} queda eliminada en la fase de grupos."
                if self.group_count == 12 and self.user_team in thirds:
                    notice += " Fue tercera de su grupo y no entró entre los ocho mejores terceros."
            else:
                notice = f"{self.user_team.name} se clasifica para la siguiente fase."
                if self.group_count == 12 and self.user_team in self.qualified_thirds:
                    notice = (
                        f"{self.user_team.name} se clasifica como una de las ocho mejores terceras."
                    )
        elif kind == "k":
            winners = [self.match_winner(i) for i in range(len(self.pairs))]
            losers = [self.match_loser(i) for i in range(len(self.pairs))]
            if data == 4:
                self.semi_winners, self.semi_losers = winners, losers
            self.alive = winners
            if self.user_team in losers:
                if data == 4:
                    notice = (
                        f"{self.user_team.name} cae en semifinales, "
                        "pero aún puede jugar por el tercer lugar."
                    )
                else:
                    self.user_eliminated = True
                    notice = f"{self.user_team.name} queda eliminada."
            else:
                if data == 4:
                    notice = f"¡{self.user_team.name} pasa a la gran final!"
                else:
                    notice = f"{self.user_team.name} pasa a la siguiente ronda."
        elif kind == "k3":
            self.third_place = self.match_winner(0)
            self.user_eliminated = self.user_team not in self.semi_winners
        elif kind == "kf":
            champ = self.match_winner(0)
            runner_up = self.match_loser(0)
            self.champion = champ
            assert self.third_place is not None
            self.podium = (champ, runner_up, self.third_place)
            return f"{champ.name} es campeón del mundo."

        self.phase_idx += 1
        self._prepare_phase()
        next_kind = self.phases[self.phase_idx][1]
        if next_kind == "k3":
            self.user_eliminated = (
                self.user_team not in self.semi_losers
                and self.user_team not in self.semi_winners
            )
        elif next_kind == "kf":
            self.user_eliminated = self.user_team not in self.semi_winners
        return notice

    def table_lines(self, group: list[Team]) -> list[str]:
        out: list[str] = []
        for idx, team in enumerate(self.standings(group)):
            row = self.table[team.name]
            out.append(
                f"{idx + 1}. {team.name}, {row['pts']} puntos, {row['pj']} jugados, "
                f"{row['g']} ganados, {row['e']} empatados, {row['p']} perdidos, "
                f"goles {row['gf']} a {row['gc']}, diferencia {row['gf'] - row['gc']:+d}, "
                f"fair play {row.get('fp', 0)}"
            )
        return out

    def results_lines(self, phase: str | None = None) -> list[str]:
        target = phase or self.phase_name()
        out: list[str] = []
        for idx, (ph, a, b, ga, gb, pen) in enumerate(self.history):
            if ph == target:
                line = format_result_text(a, b, ga, gb, pen)
                if idx in self.aggregates:
                    ea, gea, eb, geb = self.aggregates[idx]
                    line += f". Global de la llave: {ea.name} {gea}, {eb.name} {geb}"
                out.append(line)
        return out

    def top_scorers(self, limit: int = 15) -> list[str]:
        entries = [
            (p.goals, p.name, t.name)
            for t in self.teams
            for p in t.players
            if p.goals > 0
        ]
        entries.sort(key=lambda item: (-item[0], item[1]))
        return [
            f"{idx + 1}. {name}, de {team}, {goals} gol{'' if goals == 1 else 'es'}"
            for idx, (goals, name, team) in enumerate(entries[:limit])
        ]

    # ------------------------------------------------------- Safe JSON serialization
    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.KEY,
            "teams": [t.to_dict() for t in self.teams],
            "user_team": self.user_team.name,
            "user_eliminated": self.user_eliminated,
            "tiebreaker_rnd": self.tiebreaker_rnd,
            "table": self.table,
            "history": [
                [ph, a.name, b.name, ga, gb, list(pen) if pen else None]
                for ph, a, b, ga, gb, pen in self.history
            ],
            "draw_lines": self.draw_lines,
            "groups": [[t.name for t in g] for g in self.groups],
            "group_count": self.group_count,
            "phases": [list(p) for p in self.phases],
            "phase_idx": self.phase_idx,
            "pairs": [[a.name, b.name] for a, b in self.pairs],
            "completed": {
                str(k): {
                    "ga": v["ga"],
                    "gb": v["gb"],
                    "pen": list(v["pen"]) if v["pen"] else None,
                }
                for k, v in self.completed.items()
            },
            "alive": [t.name for t in self.alive],
            "semi_winners": [t.name for t in self.semi_winners],
            "semi_losers": [t.name for t in self.semi_losers],
            "third_place": self.third_place.name if self.third_place else None,
            "champion": self.champion.name if self.champion else None,
            "podium": [t.name for t in self.podium] if self.podium else None,
            "qualified_thirds": [t.name for t in self.qualified_thirds],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldCup:
        teams = [Team.from_dict(t) for t in data["teams"]]
        by_name = {t.name: t for t in teams}
        wc = cls(teams, by_name[data["user_team"]], _skip_init=True)
        wc.user_eliminated = bool(data["user_eliminated"])
        wc.tiebreaker_rnd = {k: float(v) for k, v in data["tiebreaker_rnd"].items()}
        wc.table = data["table"]
        wc.history = [
            (ph, by_name[a], by_name[b], int(ga), int(gb), tuple(pen) if pen else None)
            for ph, a, b, ga, gb, pen in data["history"]
        ]
        wc.draw_lines = list(data["draw_lines"])
        wc.groups = [[by_name[n] for n in g] for g in data["groups"]]
        wc.group_count = int(data["group_count"])
        wc.phases = [(str(a), str(b), int(c)) for a, b, c in data["phases"]]
        wc.phase_idx = int(data["phase_idx"])
        wc.pairs = [(by_name[a], by_name[b]) for a, b in data["pairs"]]
        wc.completed = {
            int(k): {
                "ga": int(v["ga"]),
                "gb": int(v["gb"]),
                "pen": tuple(v["pen"]) if v["pen"] else None,
            }
            for k, v in data["completed"].items()
        }
        wc.alive = [by_name[n] for n in data["alive"]]
        wc.semi_winners = [by_name[n] for n in data["semi_winners"]]
        wc.semi_losers = [by_name[n] for n in data["semi_losers"]]
        wc.third_place = by_name[data["third_place"]] if data.get("third_place") else None
        wc.champion = by_name[data["champion"]] if data.get("champion") else None
        wc.podium = (
            tuple(by_name[n] for n in data["podium"]) if data.get("podium") else None  # type: ignore[assignment]
        )
        wc.qualified_thirds = [by_name[n] for n in data.get("qualified_thirds", [])]
        return wc
