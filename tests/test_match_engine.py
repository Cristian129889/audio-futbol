"""Tests for the decoupled MatchEngine (friendly, knockout, aggregate, shortcuts, subs)."""

from __future__ import annotations

import random

from audiofutbol.data.csv_loader import create_generic_team
from audiofutbol.domain.match.engine import MatchEngine
from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchSettings


def test_auto_friendly_match_completes_cleanly() -> None:
    team_a = create_generic_team("Brasil", 85)
    team_b = create_generic_team("Alemania", 83)
    rng = random.Random(12345)
    engine = MatchEngine(
        team_a,
        team_b,
        io=HeadlessMatchIO(rng=rng),
        settings=MatchSettings(turn_seconds=60, var_enabled=True),
        knockout=False,
        auto=True,
        rng=rng,
    )
    res = engine.play()
    assert res.abandoned is False
    assert res.goals[0] >= 0 and res.goals[1] >= 0
    assert res.penalties is None
    assert len(engine.state.recap_lines()) >= 5


def test_interactive_knockout_match_with_seeded_decisions() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Francia", 86)
    rng = random.Random(777)
    io = HeadlessMatchIO(rng=random.Random(778))
    engine = MatchEngine(
        team_a,
        team_b,
        io=io,
        settings=MatchSettings(turn_seconds=120, var_enabled=True),
        knockout=True,
        auto=False,
        max_substitutions=5,
        stadium="Estadio Monumental, ciudad de Buenos Aires",
        attendance=("El estadio está lleno.", 1.15),
        rng=rng,
    )
    res = engine.play()
    assert res.abandoned is False
    assert res.goals[0] != res.goals[1] or (
        res.penalties is not None and res.penalties[0] != res.penalties[1]
    )
    assert len(io.messages) > 20
    assert "silbato" in io.sfx_events
    assert "silbato_final" in io.sfx_events


def test_match_shortcuts_and_time_wasting() -> None:
    team_a = create_generic_team("España", 85)
    team_b = create_generic_team("Italia", 82)
    io = HeadlessMatchIO(rng=random.Random(1))
    engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(2))
    shortcuts = engine.build_shortcuts()
    for key in ("m", "t", "p", "g", "a", "e", "s", "v", "f1", "h"):
        shortcuts[key]()
    assert len(io.messages) >= 10
    # Second time-waste in the same turn is rejected
    assert engine.subs.waste_time(0) is False


def test_two_legged_tie_away_goals_and_table_advantage() -> None:
    team_a = create_generic_team("América", 80)
    team_b = create_generic_team("Tigres UANL", 80)
    engine = MatchEngine(
        team_a,
        team_b,
        io=HeadlessMatchIO(),
        knockout=True,
        first_leg=(1, 1),
        home_index=0,
        away_goals_rule=False,
        table_advantage=0,
    )
    engine.state.goals = [1, 1]
    assert engine.state.aggregate() == [2, 2]
    assert engine.state.decided_winner() == 0
