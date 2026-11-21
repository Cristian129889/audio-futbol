"""Realistic home/away scheduling algorithms (Hungarian assignment, Haversine, groups)."""

from __future__ import annotations

import itertools
import math
import random
from typing import Callable

from audiofutbol.domain.models import Team
from audiofutbol.domain.text import normalize_text

NATIONAL_COORDS: dict[str, tuple[float, float]] = {
    "Canadá": (43.65, -79.38), "México": (19.43, -99.13), "Estados Unidos": (34.15, -118.14),
    "Australia": (-33.87, 151.21), "Irán": (35.69, 51.39), "Japón": (35.68, 139.69),
    "Jordania": (31.95, 35.93), "Corea del Sur": (37.57, 126.98), "Catar": (25.29, 51.53),
    "Arabia Saudita": (24.71, 46.68), "Uzbekistán": (41.30, 69.24), "Irak": (30.51, 47.78),
    "Argelia": (36.75, 3.06), "Cabo Verde": (14.93, -23.51), "Costa de Marfil": (5.36, -4.01),
    "Egipto": (30.04, 31.24), "Ghana": (6.69, -1.62), "Marruecos": (34.02, -6.84),
    "Senegal": (14.72, -17.18), "Sudáfrica": (-26.20, 28.05), "Túnez": (36.77, 10.28),
    "RD Congo": (-4.44, 15.27), "Curazao": (12.12, -68.93), "Haití": (18.54, -72.34),
    "Panamá": (8.98, -79.52), "Argentina": (-34.60, -58.38), "Brasil": (-22.91, -43.17),
    "Colombia": (10.96, -74.80), "Ecuador": (-0.18, -78.47), "Paraguay": (-25.26, -57.58),
    "Uruguay": (-34.90, -56.19), "Nueva Zelanda": (-36.85, 174.76), "Austria": (48.21, 16.37),
    "Bélgica": (50.85, 4.35), "Croacia": (45.81, 15.98), "Inglaterra": (51.51, -0.13),
    "Francia": (48.94, 2.36), "Alemania": (52.52, 13.40), "Países Bajos": (52.37, 4.90),
    "Noruega": (59.91, 10.75), "Portugal": (38.72, -9.14), "Escocia": (55.86, -4.25),
    "España": (37.39, -5.98), "Suiza": (47.56, 7.59), "Bosnia y Herzegovina": (44.20, 17.91),
    "Chequia": (50.08, 14.44), "Turquía": (41.01, 28.98), "Suecia": (59.36, 18.00),
}

_NORMALIZED_COORDS: dict[str, tuple[float, float]] = {
    normalize_text(k).strip(): v for k, v in NATIONAL_COORDS.items()
}

HOME_AWAY_PATTERN = "LVLLVV"

_GROUP_BASE_ROUNDS = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))
_GROUP_PATTERNS_CACHE: list[list[list[tuple[int, int]]]] = []


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 12742.0 * math.asin(min(1.0, math.sqrt(h)))


def national_team_coords(team: Team) -> tuple[float, float] | None:
    return _NORMALIZED_COORDS.get(normalize_text(team.name).strip())


def hungarian_assignment(cost_matrix: list[list[float]]) -> list[int]:
    """Solve minimum-cost square assignment problem in O(n^3)."""
    n = len(cost_matrix)
    inf = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost_matrix[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    res = [0] * n
    for j in range(1, n + 1):
        res[p[j] - 1] = j - 1
    return res


def build_realistic_home_away_schedule(
    teams: list[Team],
    rounds: int,
    coord_fn: Callable[[Team], tuple[float, float] | None] = national_team_coords,
    rng: random.Random | None = None,
) -> list[list[tuple[Team, Team]]] | None:
    """Build a geographically-aware schedule alternating LVLLVV home/away."""
    r = rng or random.Random()
    n = len(teams)
    if n % 2 or n < 8:
        return None
    half = n // 2
    comp = {"L": "V", "V": "L"}
    for _ in range(40):
        ordered = list(teams)
        r.shuffle(ordered)
        ordered.sort(key=lambda t: (coord_fn(t) or (0.0, 0.0))[1] // 40)
        phase: dict[str, int] = {}
        pattern: dict[str, str] = {}
        for k, team in enumerate(ordered):
            phase[team.name] = (k % half) % 6
            pattern[team.name] = (
                HOME_AWAY_PATTERN
                if k < half
                else "".join(comp[ch] for ch in HOME_AWAY_PATTERN)
            )
        played: set[frozenset[str]] = set()
        prev_away_host: dict[str, Team] = {}
        schedule: list[list[tuple[Team, Team]]] = []
        ok = True
        for rnd_idx in range(rounds):
            home = [
                t
                for t in teams
                if pattern[t.name][(rnd_idx + phase[t.name]) % 6] == "L"
            ]
            away = [
                t
                for t in teams
                if pattern[t.name][(rnd_idx + phase[t.name]) % 6] == "V"
            ]
            if len(home) != len(away):
                ok = False
                break
            cost: list[list[float]] = []
            for h in home:
                row: list[float] = []
                ch = coord_fn(h)
                for a in away:
                    c = r.random() * 0.8
                    ca = coord_fn(a)
                    if frozenset((h.name, a.name)) in played:
                        c += 1e6
                    elif ch and ca:
                        c += 0.25 * distance_km(ch, ca) / 1000.0
                        prev_host = prev_away_host.get(a.name)
                        if prev_host is not None:
                            cp = coord_fn(prev_host)
                            if cp:
                                c += 1.6 * distance_km(ch, cp) / 1000.0
                    row.append(c)
                cost.append(row)
            assign = hungarian_assignment(cost)
            pairs: list[tuple[Team, Team]] = []
            next_prev: dict[str, Team] = {}
            for i, h in enumerate(home):
                a = away[assign[i]]
                if cost[i][assign[i]] >= 1e5:
                    ok = False
                pairs.append((h, a))
                played.add(frozenset((h.name, a.name)))
                next_prev[a.name] = h
            if not ok:
                break
            prev_away_host = next_prev
            r.shuffle(pairs)
            schedule.append(pairs)
        if ok:
            return schedule
    return None


def build_circle_schedule(
    teams: list[Team], rounds: int, rng: random.Random | None = None
) -> list[list[tuple[Team, Team]]]:
    """Standard circle-method round-robin schedule fallback."""
    r = rng or random.Random()
    eq = list(teams)
    r.shuffle(eq)
    n = len(eq)
    rot = eq[1:]
    out: list[list[tuple[Team, Team]]] = []
    for rnd_idx in range(rounds):
        row = [eq[0]] + rot
        pairs: list[tuple[Team, Team]] = []
        for k in range(n // 2):
            a, b = row[k], row[n - 1 - k]
            if (rnd_idx + k) % 2 == 1:
                a, b = b, a
            pairs.append((a, b))
        out.append(pairs)
        rot = [rot[-1]] + rot[:-1]
    return out


def group_schedule_patterns() -> list[list[list[tuple[int, int]]]]:
    """Precompute optimal 6-round home/away schedules for a 4-team group."""
    if _GROUP_PATTERNS_CACHE:
        return _GROUP_PATTERNS_CACHE
    best_cost: int | None = None
    best_list: list[list[list[tuple[int, int]]]] = []
    for order in itertools.permutations(range(3)):
        for bits in range(64):
            rounds: list[list[tuple[int, int]]] = []
            k = 0
            for r in range(3):
                pair_list: list[tuple[int, int]] = []
                for x, y in _GROUP_BASE_ROUNDS[r]:
                    pair_list.append((y, x) if (bits >> k) & 1 else (x, y))
                    k += 1
                rounds.append(pair_list)
            for r in order:
                rounds.append([(y, x) for x, y in rounds[r]])
            seq = ["", "", "", ""]
            for pair_list in rounds:
                for h, v in pair_list:
                    seq[h] += "L"
                    seq[v] += "V"
            cost = 0
            for s in seq:
                runs = [len(list(g)) for _, g in itertools.groupby(s)]
                cost += sum(100 for run in runs if run >= 3) + sum(run - 1 for run in runs)
            if order[0] == 2:
                cost += 3
            if best_cost is None or cost < best_cost:
                best_cost, best_list = cost, [rounds]
            elif cost == best_cost:
                best_list.append(rounds)
    _GROUP_PATTERNS_CACHE.extend(best_list)
    return _GROUP_PATTERNS_CACHE
