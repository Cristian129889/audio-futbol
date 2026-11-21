"""End-to-end simulation and JSON save/load roundtrip tests for all 7 competitions."""

from __future__ import annotations

from pathlib import Path

from audiofutbol.data.competition_loader import StadiumDirectory, load_competition_data
from audiofutbol.data.csv_loader import create_generic_team, load_national_teams
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.domain.competitions.attendance import calculate_attendance
from audiofutbol.domain.competitions.club_tournaments import (
    ChampionsLeague,
    CopaInteramericana,
    CopaLibertadores,
    CopaSudamericana,
    LigaMX,
)
from audiofutbol.domain.competitions.nations_league import NationsLeague
from audiofutbol.domain.competitions.world_cup import WorldCup
from audiofutbol.engine.paths import AppPaths


def test_world_cup_full_simulation_and_save_roundtrip(tmp_path: Path) -> None:
    paths = AppPaths.detect()
    teams, _ = load_national_teams(paths)
    wc = WorldCup(teams[:48], teams[0])
    assert wc.group_count == 12
    assert len(wc.draw_lines) == 48

    # Play 1 phase, save, reload, finish tournament
    wc.simulate_remaining()
    wc.close_phase()

    saves = SaveManager(AppPaths(tmp_path))
    assert saves.save(WorldCup.KEY, wc) is True
    assert saves.exists(WorldCup.KEY) is True
    loaded = saves.load(WorldCup.KEY)
    assert isinstance(loaded, WorldCup)
    assert loaded.phase_idx == wc.phase_idx
    assert loaded.user_team.name == wc.user_team.name

    while loaded.champion is None:
        loaded.simulate_remaining()
        loaded.close_phase()

    assert loaded.podium is not None
    assert len(loaded.podium) == 3
    assert len(loaded.top_scorers(5)) > 0


def test_nations_league_full_simulation_and_save_roundtrip(tmp_path: Path) -> None:
    teams = [create_generic_team(f"Sel {i}", 70 + (i % 15)) for i in range(16)]
    league = NationsLeague(teams, teams[0], away_goals_rule=True)
    assert league.rounds_count == 8
    assert league.qualify_count == 8

    league.simulate_remaining()
    league.close_step()

    saves = SaveManager(AppPaths(tmp_path))
    assert saves.save(NationsLeague.KEY, league) is True
    loaded = saves.load(NationsLeague.KEY)
    assert isinstance(loaded, NationsLeague)
    assert loaded.step_idx == 1

    stadiums = StadiumDirectory(AppPaths.detect())
    assert len(loaded.calendar_lines(stadiums.venue_for)) == 8
    assert calculate_attendance(loaded, 0) is not None

    while loaded.champion is None:
        loaded.simulate_remaining()
        loaded.close_step()
    assert loaded.runner_up is not None


def test_all_club_competitions_simulate_and_serialize(tmp_path: Path) -> None:
    paths = AppPaths.detect()
    saves = SaveManager(AppPaths(tmp_path))

    cases = [
        ("champions_2026_27.json", ChampionsLeague),
        ("libertadores_mx_2026.json", CopaLibertadores),
        ("sudamericana_2026.json", CopaSudamericana),
        ("interamericana_2026.json", CopaInteramericana),
        ("ligamx_apertura_2026.json", LigaMX),
    ]
    for json_file, cls in cases:
        data, eqs = load_competition_data(paths, json_file)
        assert data is not None and eqs is not None
        first_team = next(iter(eqs.values()))
        tourney = cls(data, eqs, first_team)

        # Advance 1 step, test JSON save & load
        tourney.simulate_remaining()
        tourney.close_step()
        assert saves.save(tourney.KEY, tourney) is True
        restored = saves.load(tourney.KEY)
        assert isinstance(restored, cls)
        assert restored.step_idx == tourney.step_idx

        # Finish entire tournament to champion
        while restored.champion is None:
            restored.simulate_remaining()
            restored.close_step()
        assert restored.champion is not None
        assert restored.runner_up is not None
        assert len(restored.table_lines()) >= len(eqs)
