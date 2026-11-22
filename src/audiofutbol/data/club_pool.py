"""Global pool of every club the game knows (competition JSONs + any club found in CSVs)."""

from __future__ import annotations

import json
from typing import Any

from audiofutbol.data.competition_loader import team_from_entry
from audiofutbol.data.csv_loader import load_club_players, read_squad_csv
from audiofutbol.domain.models import Team
from audiofutbol.domain.text import normalize_text
from audiofutbol.engine.paths import AppPaths


def build_club_pool(paths: AppPaths) -> list[Team]:
    """Return one reference `Team` per distinct club, sorted by name.

    Sources: every `competiciones/*.json` with an `equipos` list, plus every club that has
    >= 11 players in `datos/clubes/*.csv` even if it is in no competition (so adding a club
    to a CSV is enough to make it selectable anywhere). Real squads win over generated ones.
    """
    real = load_club_players(paths)
    pool: dict[str, Team] = {}

    def add(team: Team) -> None:
        key = normalize_text(team.name).strip()
        old = pool.get(key)
        if old is None or (team.is_real_squad and not old.is_real_squad):
            pool[key] = team

    if paths.competitions_dir.is_dir():
        for path in sorted(paths.competitions_dir.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data: dict[str, Any] = json.load(fh)
            except Exception:
                continue
            for entry in data.get("equipos", []) if isinstance(data, dict) else []:
                if isinstance(entry, dict) and "nombre" in entry and "id" in entry:
                    add(team_from_entry(entry, real))

    if paths.clubs_dir.is_dir():
        display: dict[str, str] = {}
        for entry in sorted(paths.clubs_dir.iterdir()):
            if entry.is_file() and entry.suffix.lower() in (".csv", ".tsv"):
                try:
                    for eq, *_ in read_squad_csv(entry):
                        display.setdefault(normalize_text(eq).strip(), eq)
                except Exception:
                    continue
        for key, squad_map in real.items():
            if key in pool or len(squad_map) < 11:
                continue
            squad = sorted(squad_map.values(), key=lambda p: -p.overall)[:30]
            top = [p.overall for p in squad[:11]]
            team = Team(
                name=display.get(key, key.title()),
                players=squad,
                team_id=key.replace(" ", "_"),
                level=int(round(sum(top) / len(top))),
                is_real_squad=True,
            )
            add(team)

    return sorted(pool.values(), key=lambda t: normalize_text(t.name))


def fresh_copy(team: Team) -> Team:
    """Independent copy (so tournaments never mutate the shared pool)."""
    return Team.from_dict(team.to_dict())
