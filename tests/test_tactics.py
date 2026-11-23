"""Tests for tactical formations, player roles, and persistent team setup."""

from __future__ import annotations

from audiofutbol.data.csv_loader import create_generic_team
from audiofutbol.domain.formations import (
    ROLES,
    SCHEME_ORDER,
    SCHEMES,
    assign_roles,
    fit,
    preferred_role,
)
from audiofutbol.domain.models import Player, Team


def test_every_scheme_builds_a_valid_eleven() -> None:
    team = create_generic_team("Brasil", 84)
    for scheme in SCHEME_ORDER:
        xi = team.lineup(scheme)
        assert len(xi) == 11 and len({id(p) for p in xi}) == 11
        assert sum(1 for p in xi if p.position == "POR") == 1
        roles, fits = assign_roles(xi, scheme)
        assert len(roles) == 11
        field = sorted(r for r in roles.values() if r != "POR")
        assert field == sorted(SCHEMES[scheme])
        assert all(0.0 < f <= 1.0 for f in fits.values())


def test_preferred_role_is_stable_and_in_group() -> None:
    team = create_generic_team("Francia", 85)
    for p in team.players:
        first = preferred_role(p)
        assert first == preferred_role(p)
        assert ROLES[first][1] == p.position
    assert fit(team.players[5], preferred_role(team.players[5])) == 1.0


def test_team_preferences_roundtrip() -> None:
    team = create_generic_team("Uruguay", 80)
    team.scheme = "3-5-2"
    team.lineup_names = [p.name for p in team.lineup("3-5-2")]
    team.takers = {"penal": team.players[10].name}
    team.mentality = 1
    team.players[9].assists = 2
    back = Team.from_dict(team.to_dict())
    assert back.scheme == "3-5-2" and back.mentality == 1
    assert back.takers == team.takers and back.lineup_names == team.lineup_names
    assert back.players[9].assists == 2


def test_lineup_never_pulls_backup_gk_into_outfield_slot() -> None:
    players = [
        Player("GK Titular", "POR", 85, 1),
        Player("GK Suplente", "POR", 82, 12),
    ]
    for i in range(4):
        players.append(Player(f"Def {i}", "DEF", 75, i + 2))
    for i in range(5):
        players.append(Player(f"Med {i}", "MED", 74, i + 10))
    # Only 2 strikers, but 3-4-3 asks for 3 -> must pull a MED/DEF, NOT GK Suplente
    players.append(Player("Del 0", "DEL", 76, 9))
    players.append(Player("Del 1", "DEL", 75, 11))

    team = Team(name="Test", players=players)
    xi = team.lineup("3-4-3")
    assert len(xi) == 11
    assert sum(1 for p in xi if p.position == "POR") == 1
    assert all(p.name != "GK Suplente" for p in xi)
