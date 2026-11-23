"""Tests for domain models, CSV/JSON loaders, and stadium directory."""

from __future__ import annotations

from audiofutbol.data.club_pool import build_club_pool
from audiofutbol.data.competition_loader import StadiumDirectory, load_competition_data
from audiofutbol.data.csv_loader import (
    create_generic_team,
    load_club_players,
    load_extra_national_teams,
    load_national_teams,
    parse_position,
)
from audiofutbol.domain.models import Team
from audiofutbol.domain.text import normalize_text, tokenize
from audiofutbol.engine.paths import AppPaths


def test_text_normalization_and_tokens() -> None:
    assert normalize_text("Fútbol Épico Ñandú") == "futbol epico nandu"
    assert tokenize("balón_tiro-2.wav") == ["balon", "tiro", "wav"]


def test_parse_position_variants() -> None:
    assert parse_position("GK") == "POR"
    assert parse_position("CB, RB") == "DEF"
    assert parse_position("CM") == "MED"
    assert parse_position("delantero") == "DEL"
    assert parse_position("desconocido") == "MED"


def test_team_starting_xi_and_roundtrip() -> None:
    team = create_generic_team("Argentina", 85)
    xi = team.starting_xi()
    assert len(xi) == 11
    assert sum(1 for p in xi if p.position == "POR") == 1
    assert 75.0 <= team.strength() <= 95.0

    restored = Team.from_dict(team.to_dict())
    assert restored.name == team.name
    assert len(restored.players) == len(team.players)
    assert restored.players[0].name == team.players[0].name


def test_load_real_national_teams_and_clubs() -> None:
    paths = AppPaths.detect()
    teams, from_csv = load_national_teams(paths)
    assert from_csv is True
    assert len(teams) >= 48

    clubs = load_club_players(paths)
    assert len(clubs) >= 60

    stadiums = StadiumDirectory(paths)
    arg = next(t for t in teams if t.name == "Argentina")
    assert stadiums.venue_for(arg) is not None


def test_load_all_competition_jsons() -> None:
    paths = AppPaths.detect()
    for filename, expected_min in (
        ("champions_2026_27.json", 36),
        ("libertadores_2026.json", 32),
        ("libertadores_mx_2026.json", 32),
        ("sudamericana_2026.json", 32),
        ("interamericana_2026.json", 64),
        ("ligamx_apertura_2026.json", 18),
        ("premier_league_2026_27.json", 20),
        ("liga1_peru_2026.json", 18),
        ("clubes_europa_extra.json", 27),
    ):
        data, eqs = load_competition_data(paths, filename)
        assert data is not None
        assert eqs is not None
        assert len(eqs) == expected_min


def test_club_pool_and_extra_national_teams() -> None:
    paths = AppPaths.detect()
    extra = load_extra_national_teams(paths)
    extra_names = {t.name for t in extra}
    assert {"Perú", "Italia"}.issubset(extra_names)

    pool = build_club_pool(paths)
    assert len(pool) >= 183
    real_clubs = [t for t in pool if t.is_real_squad]
    assert len(real_clubs) >= 170
    for club in pool:
        xi = club.starting_xi()
        assert len(xi) == 11
        assert sum(1 for p in xi if p.position == "POR") == 1

