"""Tests for tactical formations, player roles, and persistent team setup."""

from __future__ import annotations

import random

from audiofutbol.data.csv_loader import create_generic_team
from audiofutbol.domain.formations import (
    ROLES,
    SCHEME_ORDER,
    SCHEMES,
    assign_roles,
    fit,
    preferred_role,
    scheme_rows,
)
from audiofutbol.domain.match.board import build_prematch_board_rows, format_quality, slot_pan
from audiofutbol.domain.match.engine import MatchEngine
from audiofutbol.domain.match.ports import HeadlessMatchIO, MatchSettings
from audiofutbol.domain.models import POSITION_ORDER, Player, Team


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


def test_all_schemes_play_complete_matches() -> None:
    for i, scheme in enumerate(SCHEME_ORDER):
        a = create_generic_team("Alemania", 82)
        a.scheme = scheme
        b = create_generic_team("Italia", 82)
        rng = random.Random(i)
        engine = MatchEngine(a, b, io=HeadlessMatchIO(rng=rng), auto=True, rng=rng)
        assert engine.state.scheme[0] == scheme
        res = engine.play()
        assert res.goals[0] >= 0 and not res.abandoned


def test_fixed_takers_are_used() -> None:
    a = create_generic_team("Argentina", 84)
    b = create_generic_team("Brasil", 84)
    chosen = next(p for p in a.lineup() if p.position == "DEF")
    a.takers = {"penal": chosen.name, "corner": chosen.name}
    engine = MatchEngine(a, b, io=HeadlessMatchIO(), auto=False, prematch_menu=False)
    taker, notice = engine.state.designated_taker(0, "penal")
    assert taker is chosen and notice is None
    engine.state.injured.add(id(chosen))
    taker2, notice2 = engine.state.designated_taker(0, "penal")
    assert taker2 is not chosen and notice2 is not None
    assert engine.state.designated_taker(0, "penal")[0] is taker2


def test_shootout_is_one_by_one_and_ends() -> None:
    for seed in range(20):
        a = create_generic_team("España", 80)
        b = create_generic_team("Portugal", 80)
        rng = random.Random(seed)
        io = HeadlessMatchIO(rng=random.Random(seed))
        engine = MatchEngine(a, b, io=io, auto=False, prematch_menu=False, rng=rng)
        engine.set_pieces.penalty_shootout()
        pen = engine.state.penalties
        assert pen is not None and pen[0] != pen[1]
        names = [ln.split(",")[0] for ln in engine.state.shootout_lines[:-1]]
        per_team: dict[int, list[str]] = {0: [], 1: []}
        for ln in engine.state.shootout_lines[:-1]:
            who = ln.split(", de ")[0]
            team_name = ln.split(", de ")[1].split(":")[0]
            per_team[0 if team_name == a.name else 1].append(who)
        for lst in per_team.values():
            head = lst[:11]
            assert len(head) == len(set(head)), names


def test_assists_counted_and_serialized() -> None:
    total_goals = total_assists = 0
    for seed in range(15):
        a = create_generic_team("Japón", 80)
        b = create_generic_team("Senegal", 80)
        rng = random.Random(seed)
        engine = MatchEngine(a, b, io=HeadlessMatchIO(rng=rng), auto=True, rng=rng)
        res = engine.play()
        total_goals += sum(res.goals)
        total_assists += len(res.assists)
        assert sum(p.assists for p in a.players + b.players) == len(res.assists)
        for team, player, _minute in res.assists:
            assert player in team.players
    assert total_goals > 0 and 0 < total_assists <= total_goals


def test_interactive_match_with_prematch_and_live_tactics() -> None:
    a = create_generic_team("México", 80)
    b = create_generic_team("Colombia", 80)
    io = HeadlessMatchIO(rng=random.Random(5))
    engine = MatchEngine(
        a,
        b,
        io=io,
        auto=False,
        max_substitutions=5,
        settings=MatchSettings(turn_seconds=120),
        rng=random.Random(6),
    )
    res = engine.play()
    assert not res.abandoned
    assert any("juega con" in m for m in io.messages)
    engine.tactics.in_match_menu()
    engine.tactics.apply_scheme(0, "5-4-1")
    assert engine.state.scheme[0] == "5-4-1"
    assert len(engine.state.xi[0]) == 11 or engine.state.red_cards[0] > 0


def test_ai_balance_between_schemes_is_reasonable() -> None:
    """No tactical scheme should dominate or collapse against 4-4-2 at equal strength."""
    for scheme in SCHEME_ORDER:
        points = goals_for = goals_against = 0
        n = 40
        for seed in range(n):
            a = create_generic_team("Croacia", 80)
            a.scheme = scheme
            b = create_generic_team("Croacia", 80)
            b.scheme = "4-4-2"
            rng = random.Random(1000 + seed)
            res = MatchEngine(
                a, b, io=HeadlessMatchIO(rng=rng), auto=True, rng=rng
            ).play()
            goals_for += res.goals[0]
            goals_against += res.goals[1]
            points += 3 if res.goals[0] > res.goals[1] else 1 if res.goals[0] == res.goals[1] else 0
        avg = points / n
        total = (goals_for + goals_against) / n
        assert 0.7 <= avg <= 2.3, (scheme, avg)
        assert 1.2 <= total <= 5.0, (scheme, total)


def test_prematch_tactics_harmonizes_with_fourth_official_subs_and_gk_red_card() -> None:
    def decide(title: str, options: list[str]) -> int:
        if "¿Qué jugador de campo sale" in title:
            return len(options) - 1
        if "¿Quién sale?" in title:
            return len(options) - 1
        return 0

    a = create_generic_team("México", 80)
    b = create_generic_team("Argentina", 84)
    io = HeadlessMatchIO(decision_callback=decide, rng=random.Random(12))
    engine = MatchEngine(
        a, b, io=io, max_substitutions=5, auto=False, prematch_menu=False, rng=random.Random(12)
    )

    # Apply prematch-style scheme & mentality change
    engine.tactics.apply_scheme(0, "3-5-2", persist=True)
    engine.state.set_mentality(0, 1)
    assert engine.state.scheme[0] == "3-5-2"
    roles_order = [POSITION_ORDER[engine.state.player_role(p)] for p in engine.state.xi[0]]
    assert roles_order == sorted(roles_order)
    assert all(id(p) in engine.state.slot for p in engine.state.xi[0])

    # Queue 4th-official substitution during live play, then trigger stoppage
    assert engine.build_shortcuts()["c"]() is None
    assert engine.state.pending_substitution is True
    engine.set_pieces.throw_in()
    assert engine.state.pending_substitution is False
    assert engine.state.substitutions_used[0] == 1
    roles_after_sub = [POSITION_ORDER[engine.state.player_role(p)] for p in engine.state.xi[0]]
    assert roles_after_sub == sorted(roles_after_sub)

    # Send off GK -> prompts backup GK and keeps XI valid & sorted
    gk = engine.state.goalkeeper(0)
    assert gk is not None
    engine.set_pieces.give_card(0, gk, "roja")
    new_gk = engine.state.goalkeeper(0)
    assert new_gk is not None and new_gk is not gk
    assert engine.state.xi[0][0] is new_gk
    assert engine.state.slot.get(id(new_gk)) == "POR"
    assert engine.state.gk_penalty[0] == 0


def test_scheme_rows_match_schemes_and_split_four_line_midfields() -> None:
    for scheme in SCHEME_ORDER:
        rows = scheme_rows(scheme)
        assert rows[0] == ("Portería", ["POR"])
        outfield = [code for _name, codes in rows[1:] for code in codes]
        assert sorted(outfield) == sorted(SCHEMES[scheme])
    rows_4231 = scheme_rows("4-2-3-1")
    assert [name for name, _ in rows_4231] == [
        "Portería",
        "Zona defensiva",
        "Mediocampo defensivo",
        "Mediocampo ofensivo",
        "Delantera",
    ]
    rows_4141 = scheme_rows("4-1-4-1")
    assert [name for name, _ in rows_4141] == [
        "Portería",
        "Zona defensiva",
        "Mediocentro defensivo",
        "Mediocampo",
        "Delantera",
    ]
    assert slot_pan(0, 4) == -1.0
    assert slot_pan(3, 4) == 1.0
    assert slot_pan(0, 1) == 0.0


def test_scheme_change_clear_requires_minimum_eight_players() -> None:
    team_a = create_generic_team("Argentina", 84)
    team_b = create_generic_team("Brasil", 84)
    board_steps: list[tuple[str, list]] = []

    class _ClearFlowIO(HeadlessMatchIO):
        def __init__(self) -> None:
            super().__init__()
            self.confirm_questions: list[str] = []
            self.menu_step = 0
            self.board_step = 0

        def confirm(self, question: str) -> bool:
            self.confirm_questions.append(question)
            return True

        def choose(self, title, options, shortcuts=None, quick_keys=None, initial_index=0):
            if title == "Preparación del partido":
                # 0: Start (when >=8), else Scheme
                self.menu_step += 1
                if self.menu_step == 1:
                    return next(i for i, o in enumerate(options) if "Cambiar el esquema" in o)
                return 0  # Start match
            if title.startswith("Esquemas."):
                return SCHEME_ORDER.index("4-2-3-1")
            if "¿A quién colocas aquí?" in title:
                return 1  # pick best eligible player
            return 0

        def choose_formation(
            self,
            title,
            rows,
            status_line="",
            shortcuts=None,
            initial_slot_id=None,
            allow_remove=False,
            allow_swap_key=False,
        ):
            board_steps.append((title, rows))
            self.board_step += 1
            # First try accepting immediately with 0 players -> must be rejected!
            if self.board_step == 1:
                return ("select", "action:accept")
            # Fill slots 0..7 (8 players), then accept
            for row in rows:
                if row.is_action_row:
                    continue
                for slot in row.slots:
                    if not slot.occupied:
                        return ("select", slot.slot_id)
            return ("select", "action:accept")

    io = _ClearFlowIO()
    engine = MatchEngine(team_a, team_b, io=io, auto=False, prematch_menu=False)
    engine.tactics.prematch_menu()

    assert any("limpiar tu alineación" in q.lower() for q in io.confirm_questions)
    assert any("al menos 8 jugadores" in m.lower() for m in io.messages)
    assert engine.state.scheme[0] == "4-2-3-1"
    assert len(engine.state.xi[0]) >= 8
    assert board_steps[0][1][1].slots[0].occupied is False


def test_scheme_change_no_clear_keeps_and_shifts_starters() -> None:
    team_a = create_generic_team("España", 84)
    team_b = create_generic_team("Francia", 84)

    class _KeepFlowIO(HeadlessMatchIO):
        def __init__(self) -> None:
            super().__init__()
            self.step = 0

        def confirm(self, question: str) -> bool:
            return False

        def choose(self, title, options, shortcuts=None, quick_keys=None, initial_index=0):
            if title == "Preparación del partido":
                self.step += 1
                if self.step == 1:
                    return next(i for i, o in enumerate(options) if "Cambiar el esquema" in o)
                return 0
            if title.startswith("Esquemas."):
                return SCHEME_ORDER.index("3-5-2")
            return 0

    io = _KeepFlowIO()
    engine = MatchEngine(team_a, team_b, io=io, auto=False, prematch_menu=False)
    original_names = {p.name for p in engine.state.xi[0]}
    engine.tactics.prematch_menu()

    assert engine.state.scheme[0] == "3-5-2"
    assert {p.name for p in engine.state.xi[0]} == original_names
    assert len(io.formation_calls) == 0


def test_prematch_board_displays_yellow_cards_and_updates_average_quality() -> None:
    team_a = create_generic_team("Uruguay", 82)
    team_b = create_generic_team("Chile", 80)
    engine = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), auto=False, prematch_menu=False)

    starter = engine.state.xi[0][1]
    starter.yellow_cards = 1
    rows = build_prematch_board_rows(engine.state, 0)
    def_labels = [s.label for s in rows[1].slots]
    assert any(starter.name in lbl and "1 amarilla" in lbl for lbl in def_labels)

    avg_before = engine.state.lineup_average_quality(0, include_fatigue=False)
    slots = list(engine.state.board_slots[0])
    slots[1] = None  # remove defender
    engine.state.apply_board_slots(0, slots, persist=True)
    rows_after = build_prematch_board_rows(engine.state, 0)
    accept_lbl = rows_after[-1].slots[0].label
    expected_avg = format_quality(engine.state.lineup_average_quality(0, include_fatigue=False))
    assert f"calidad media {expected_avg}" in accept_lbl
    assert "10 de 11 jugadores" in accept_lbl
    assert avg_before > 0


