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
    for key in ("m", "t", "p", "g", "a", "e", "s", "v", "x", "n", "f1", "h"):
        shortcuts[key]()
    assert len(io.messages) >= 12
    assert engine.state.tactic in (
        "muy_defensivo",
        "defensivo",
        "equilibrado",
        "ofensivo",
        "muy_ofensivo",
    )
    # Second time-waste in the same turn is rejected
    assert engine.subs.waste_time(0) is False


def test_block_defense_variability_and_solidity() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Uruguay", 82)
    outcomes: set[str] = set()
    solidity_seen = False
    for seed in range(40):
        io = HeadlessMatchIO(rng=random.Random(seed))
        engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(seed))
        engine.state.set_possession(1, 2, lane=0)
        carrier = engine.state.ensure_ball_carrier(1)
        engine._defend_in_block(2, 0.28, carrier)
        if io.messages:
            outcomes.add(io.messages[0])
        if engine.state.block_solidity > 0:
            solidity_seen = True
    assert len(outcomes) >= 10
    assert solidity_seen is True


def test_switch_play_changes_lane_and_grants_space_bonus() -> None:
    team_a = create_generic_team("España", 90)
    team_b = create_generic_team("Gales", 70)
    for seed in range(30):
        io = HeadlessMatchIO(rng=random.Random(seed))
        engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(seed))
        engine.state.set_possession(0, 1, lane=-1)
        carrier = engine.state.ensure_ball_carrier(0)
        engine._switch_play(carrier, 0.15)
        if engine.state.possession == 0:
            assert engine.state.lane == 1
            assert engine.state.space_bonus > 0
            return
    raise AssertionError("Switch of play never succeeded across 30 seeds")


def test_narrator_prevents_back_to_back_repeats() -> None:
    from audiofutbol.domain.match.narration import Narrator

    narrator = Narrator(random.Random(99))
    prev = None
    for _ in range(25):
        line = narrator.say("block_hold_solid", rival="Brasil", riv="Vinicius", def_="Romero")
        assert line != prev
        prev = line


def test_substitution_blocks_gk_for_outfield_and_supports_role_choice() -> None:
    team_a = create_generic_team("Argentina", 84)
    team_b = create_generic_team("Brasil", 84)
    engine = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), auto=False)

    defender = next(p for p in engine.state.xi[0] if p.position == "DEF")
    replacements = engine.subs.eligible_replacements(0, defender)
    assert all(p.position != "POR" for p in replacements)

    bench_striker = next(p for p in replacements if p.position == "DEL")
    base_att = engine.state.compute_ratings(0)["att"]
    base_dfn = engine.state.compute_ratings(0)["dfn"]

    # Option 1: Natural position -> changes formation to 3-3-4 and boosts attack / lowers defense
    engine.subs.apply_substitution(0, defender, bench_striker, play_as_position="DEL")
    assert engine.state.formation_code(0) == "3-3-4"
    assert engine.state.compute_ratings(0)["att"] > base_att
    assert engine.state.compute_ratings(0)["dfn"] < base_dfn

    # Option 2: Cover outgoing position -> keeps formation but applies out-of-position factor
    engine2 = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), auto=False)
    def2 = next(p for p in engine2.state.xi[0] if p.position == "DEF")
    st2 = next(p for p in engine2.subs.eligible_replacements(0, def2) if p.position == "DEL")
    engine2.subs.apply_substitution(0, def2, st2, play_as_position="DEF")
    assert engine2.state.formation_code(0) == "4-3-3"
    assert engine2.state.player_role(st2) == "DEF"
    assert engine2.state.out_of_position_factor(st2) < 1.0


def test_player_overall_stat_meaningfully_impacts_finishing() -> None:
    from audiofutbol.domain.models import Player

    elite_striker = Player("Crack 93", "DEL", 93, 9)
    weak_striker = Player("Novato 60", "DEL", 60, 19)
    team_a = create_generic_team("Equipo A", 78)
    team_b = create_generic_team("Equipo B", 78)

    def simulate_shots(shooter: Player) -> int:
        goals = 0
        for seed in range(250):
            rng = random.Random(seed)
            eng = MatchEngine(
                team_a, team_b, io=HeadlessMatchIO(rng=rng),
                settings=MatchSettings(var_enabled=False), auto=True, rng=rng,
            )
            eng.state.lane = 0
            if eng.set_pieces.shot(0, 3, shooter=shooter, corner_ok=False, free_kick=True) == "gol":
                goals += 1
        return goals

    elite_goals = simulate_shots(elite_striker)
    weak_goals = simulate_shots(weak_striker)
    assert elite_goals > weak_goals * 1.5


def test_proactive_rival_finishes_instead_of_endless_passing() -> None:
    team_a = create_generic_team("Argentina", 80)
    team_b = create_generic_team("Francia", 85)
    decisive_plays = 0
    for seed in range(30):
        rng = random.Random(seed)
        io = HeadlessMatchIO(rng=rng)
        eng = MatchEngine(team_a, team_b, io=io, auto=False, rng=rng)
        eng.state.set_possession(1, 2, lane=0)
        eng.state.rival_attack_turns = 2
        eng._rival_advance(1)
        if (
            eng.state.shots[1] > 0
            or eng.state.corners[1] > 0
            or eng.state.possession == 0
        ):
            decisive_plays += 1
    assert decisive_plays == 30


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


def test_substituting_active_ball_carrier_updates_carrier_and_removes_subbed_off_player() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Uruguay", 82)
    io = HeadlessMatchIO(rng=random.Random(42))
    engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(42))

    out_player = next(p for p in engine.state.xi[0] if p.position == "DEF")
    in_player = next(
        p for p in engine.subs.eligible_replacements(0, out_player) if p.position == "MED"
    )
    engine.state.set_possession(0, 1, lane=0, carrier=out_player)
    assert engine.state.ball_carrier is out_player

    # Simulate user pressing 'C' during attack turn and subbing out the active ball carrier
    engine.subs.apply_substitution(0, out_player, in_player, play_as_position="DEF")

    assert out_player not in engine.state.xi[0]
    assert in_player in engine.state.xi[0]
    assert engine.state.ball_carrier is in_player

    # Execute a pass right after the substitution: out_player must never be mentioned
    io.messages.clear()
    carrier = engine.state.ensure_ball_carrier(0)
    assert carrier is in_player
    engine._short_pass(1, carrier, 0.25)
    assert all(out_player.name not in msg for msg in io.messages)


def test_goal_sfx_distinguishes_user_goal_from_rival_goal() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Corea del Sur", 76)
    io = HeadlessMatchIO(rng=random.Random(10))
    engine = MatchEngine(
        team_a,
        team_b,
        io=io,
        settings=MatchSettings(var_enabled=False, crowd_volume=0.05),
        auto=False,
        rng=random.Random(10),
    )

    scorer_a = next(p for p in engine.state.xi[0] if p.position == "DEL")
    engine.set_pieces.score_goal(0, scorer_a)
    assert "gol" in io.sfx_events
    assert "ovacion" in io.sfx_events

    io.sfx_events.clear()
    scorer_b = next(p for p in engine.state.xi[1] if p.position == "DEL")
    engine.set_pieces.score_goal(1, scorer_b)
    assert "abucheo" in io.sfx_events
    assert "ovacion" not in io.sfx_events
    assert "gol" not in io.sfx_events


def test_goal_kick_short_pass_stays_in_own_box_zone() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Brasil", 86)
    io = HeadlessMatchIO(decision_callback=lambda _t, _o: 0)
    engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(7))

    engine.set_pieces.goal_kick(0)
    assert engine.state.possession == 0
    assert engine.state.zone == 0


def test_halftime_plays_and_stops_menu_music() -> None:
    class _SpyMatchIO(HeadlessMatchIO):
        def __init__(self) -> None:
            super().__init__(decision_callback=lambda _t, _o: 1)
            self.music_events: list[str] = []

        def play_menu_music(self) -> None:
            self.music_events.append("play_menu_music")

        def stop_music(self, fade_ms: int = 1200) -> None:
            self.music_events.append(f"stop_music:{fade_ms}")

    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Francia", 86)
    io = _SpyMatchIO()
    engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(3))
    engine._halftime("primer tiempo", "segundo tiempo")
    assert io.music_events == ["play_menu_music", "stop_music:800"]


def test_accumulated_fouls_increase_tension_and_aggression() -> None:
    team_a = create_generic_team("Argentina", 84)
    team_b = create_generic_team("Uruguay", 84)
    io = HeadlessMatchIO(rng=random.Random(11))
    engine = MatchEngine(
        team_a,
        team_b,
        io=io,
        settings=MatchSettings(var_enabled=False),
        auto=False,
        rng=random.Random(11),
    )
    assert engine.state.tension == 0.0
    assert engine.state.aggression_level() == 0.0

    for _ in range(6):
        engine.set_pieces.foul(1, 1, "barrida")
        engine._update_rival_context_and_tension()

    assert engine.state.tension >= 65.0
    assert engine.state.aggression_level() >= 0.65
    assert engine.state.tension_stage_announced == 2
    assert any("caliente" in m or "caldera" in m or "tensión" in m for m in io.messages)


def test_rival_context_adapts_tactic_and_emotional_mood() -> None:
    team_a = create_generic_team("Brasil", 85)
    team_b = create_generic_team("Colombia", 83)
    io = HeadlessMatchIO(rng=random.Random(21))
    engine = MatchEngine(team_a, team_b, io=io, auto=False, rng=random.Random(21))

    # Rival trailing late -> switches to offensive posture and anxious/frustrated mood
    engine.state.goals = [2, 0]
    engine.state.turn = int(engine.state.total_turns * 0.75)
    engine.state.shift_morale(0, 5.0)
    engine.state.shift_morale(1, -4.0)
    engine._update_rival_context_and_tension()

    assert engine.state.rival_tactic == "ofensivo"
    assert engine.state.mood_state[0] == "euforico"
    assert engine.state.mood_state[1] == "ansioso"
    assert engine._rival_aggression() > 0.15

    # Rival leading late -> switches to defensive posture to protect the result
    engine.state.goals = [0, 1]
    engine.state.turn = int(engine.state.total_turns * 0.80)
    engine.state.shift_morale(1, 6.0)
    engine._update_rival_context_and_tension()
    assert engine.state.rival_tactic == "defensivo"


def test_natural_position_substitution_sorts_lineup_by_role() -> None:
    from audiofutbol.domain.models import POSITION_ORDER

    class _SpyReaderIO(HeadlessMatchIO):
        def __init__(self) -> None:
            super().__init__()
            self.reader_lines: list[str] = []

        def read_lines(self, title: str, lines: list[str]) -> None:
            self.reader_lines = list(lines)

    team_a = create_generic_team("México", 80)
    team_b = create_generic_team("Brasil", 84)
    io = _SpyReaderIO()
    engine = MatchEngine(team_a, team_b, io=io, auto=False)

    striker = next(p for p in engine.state.xi[0] if p.position == "DEL")
    bench_def = next(
        p for p in engine.subs.eligible_replacements(0, striker) if p.position == "DEF"
    )

    engine.subs.apply_substitution(0, striker, bench_def, play_as_position="DEF")
    roles = [POSITION_ORDER[engine.state.player_role(p)] for p in engine.state.xi[0]]
    assert roles == sorted(roles)
    # New defender is grouped with defenders, not at the bottom with strikers
    def_indices = [i for i, p in enumerate(engine.state.xi[0]) if engine.state.player_role(p) == "DEF"]
    assert engine.state.xi[0].index(bench_def) in def_indices
    assert engine.state.xi[0][-1].position == "DEL"

    engine.subs.read_lineup()
    player_lines = io.reader_lines[1:-1]
    assert len(player_lines) == 11
    bench_def_line_idx = next(
        i for i, line in enumerate(player_lines) if line.startswith(bench_def.name + ",")
    )
    assert bench_def_line_idx in def_indices

    # Cover position keeps incoming player in the outgoing player's role slot
    engine2 = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), auto=False)
    striker2 = next(p for p in engine2.state.xi[0] if p.position == "DEL")
    bench_def2 = next(
        p for p in engine2.subs.eligible_replacements(0, striker2) if p.position == "DEF"
    )
    engine2.subs.apply_substitution(0, striker2, bench_def2, play_as_position="DEL")
    assert engine2.state.player_role(bench_def2) == "DEL"
    assert engine2.state.xi[0].index(bench_def2) >= 8


def test_goalkeeper_red_card_prompts_backup_gk_and_outfield_sacrifice() -> None:
    prompts: list[str] = []

    def decide(title: str, options: list[str]) -> int:
        prompts.append(title)
        if "¿Qué jugador de campo sale" in title:
            return len(options) - 1  # sacrifice last striker
        return 0  # pick best backup GK

    team_a = create_generic_team("México", 80)
    team_b = create_generic_team("Argentina", 85)
    io = HeadlessMatchIO(decision_callback=decide)
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False)
    starting_gk = engine.state.goalkeeper(0)
    assert starting_gk is not None

    engine.set_pieces.give_card(0, starting_gk, "roja")

    assert any("¿Qué jugador de campo sale" in p for p in prompts)
    assert any("¿Qué portero suplente entra?" in p for p in prompts)
    new_gk = engine.state.goalkeeper(0)
    assert new_gk is not None and new_gk is not starting_gk
    assert new_gk.position == "POR"
    assert engine.state.xi[0][0] is new_gk
    assert engine.state.gk_penalty[0] == 0
    assert engine.state.substitutions_used[0] == 1
    assert len(engine.state.xi[0]) == 10


def test_goalkeeper_red_card_falls_back_when_no_subs_or_no_backup_gk() -> None:
    # Case (a): no substitutions remaining
    team_a = create_generic_team("México", 80)
    team_b = create_generic_team("Argentina", 85)
    io_no_subs = HeadlessMatchIO()
    engine_no_subs = MatchEngine(team_a, team_b, io=io_no_subs, max_substitutions=5, auto=False)
    engine_no_subs.state.substitutions_used[0] = 5
    gk_a = engine_no_subs.state.goalkeeper(0)
    assert gk_a is not None
    engine_no_subs.set_pieces.give_card(0, gk_a, "roja")
    assert engine_no_subs.state.gk_penalty[0] == -25
    assert any("un jugador de campo se pone los guantes" in m.lower() for m in io_no_subs.messages)

    # Case (b): no backup goalkeepers available on the bench
    io_no_gk = HeadlessMatchIO()
    engine_no_gk = MatchEngine(team_a, team_b, io=io_no_gk, max_substitutions=5, auto=False)
    gk_b = engine_no_gk.state.goalkeeper(0)
    assert gk_b is not None
    for p in engine_no_gk.subs.bench(0):
        if p.position == "POR":
            engine_no_gk.state.subbed_off[0].add(id(p))
    engine_no_gk.set_pieces.give_card(0, gk_b, "roja")
    assert engine_no_gk.state.gk_penalty[0] == -25
    assert any("un jugador de campo se pone los guantes" in m.lower() for m in io_no_gk.messages)


def test_ai_goalkeeper_red_card_subs_in_backup_gk() -> None:
    team_a = create_generic_team("Brasil", 84)
    team_b = create_generic_team("México", 80)
    io = HeadlessMatchIO()
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False)
    rival_gk = engine.state.goalkeeper(1)
    assert rival_gk is not None

    engine.set_pieces.give_card(1, rival_gk, "roja")

    new_rival_gk = engine.state.goalkeeper(1)
    assert new_rival_gk is not None and new_rival_gk is not rival_gk
    assert new_rival_gk.position == "POR"
    assert engine.state.gk_penalty[1] == 0
    assert engine.state.substitutions_used[1] == 1
    assert len(engine.state.xi[1]) == 10


def test_c_during_live_play_notifies_fourth_official_and_applies_at_stoppage() -> None:
    choose_calls: list[str] = []

    def decide(title: str, options: list[str]) -> int:
        choose_calls.append(title)
        if "¿Quién sale?" in title:
            return len(options) - 1
        return 0

    team_a = create_generic_team("México", 82)
    team_b = create_generic_team("Chile", 80)
    io = HeadlessMatchIO(decision_callback=decide, rng=random.Random(5))
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False, rng=random.Random(5))
    shortcuts = engine.build_shortcuts()

    assert engine.state.ball_in_play is True
    res = shortcuts["c"]()
    assert res is None  # Must not return "releer" so menu doesn't cut off announcement
    assert engine.state.pending_substitution is True
    assert choose_calls == []
    assert "Cambio pedido" in engine.state.hud_status()
    assert any("cuarto árbitro" in m.lower() for m in io.messages)

    # Stoppage occurs (throw-in) -> pending substitution opens automatically before play resumes
    engine.set_pieces.throw_in()
    assert engine.state.pending_substitution is False
    assert engine.state.substitutions_used[0] == 1
    assert any("¿Quién sale?" in t for t in choose_calls)
    assert any("tablero electrónico" in m.lower() for m in io.messages)


def test_c_twice_during_live_play_cancels_pending_request() -> None:
    team_a = create_generic_team("México", 82)
    team_b = create_generic_team("Chile", 80)
    io = HeadlessMatchIO(rng=random.Random(9))
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False, rng=random.Random(9))
    shortcuts = engine.build_shortcuts()

    assert shortcuts["c"]() is None
    assert engine.state.pending_substitution is True

    # HeadlessMatchIO.confirm returns True -> cancels the pending fourth-official notice
    res2 = shortcuts["c"]()
    assert res2 == "releer"
    assert engine.state.pending_substitution is False
    assert any("cancelado" in m.lower() for m in io.messages)


def test_c_during_dead_ball_opens_substitution_immediately() -> None:
    choose_calls: list[str] = []

    def decide(title: str, options: list[str]) -> int:
        choose_calls.append(title)
        return 0

    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Colombia", 82)
    io = HeadlessMatchIO(decision_callback=decide)
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False)
    engine.state.ball_in_play = False

    res = engine.build_shortcuts()["c"]()
    assert res == "releer"
    assert engine.state.pending_substitution is False
    assert engine.state.substitutions_used[0] == 1
    assert any("¿Quién sale?" in t for t in choose_calls)


def test_c_blocked_during_penalty_shootout() -> None:
    team_a = create_generic_team("Argentina", 86)
    team_b = create_generic_team("Francia", 86)
    io = HeadlessMatchIO()
    engine = MatchEngine(team_a, team_b, io=io, max_substitutions=5, auto=False)
    engine.state.in_penalty_shootout = True

    res = engine.build_shortcuts()["c"]()
    assert res is None
    assert engine.state.pending_substitution is False
    assert engine.state.substitutions_used[0] == 0
    assert any("tanda de penales" in m.lower() for m in io.messages)


def test_extra_time_grants_one_additional_substitution() -> None:
    team_a = create_generic_team("Argentina", 84)
    team_b = create_generic_team("Francia", 84)
    engine = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), max_substitutions=5, auto=False, prematch_menu=False)
    engine.state.substitutions_used = [5, 5]
    assert engine.subs.remaining_subs(0, human=True) == 0
    assert engine.subs.remaining_subs(1, human=False) == 0

    assert engine.state.grant_extra_time_substitution() is True
    assert engine.state.max_substitutions == 6
    assert engine.subs.remaining_subs(0, human=True) == 1
    assert engine.subs.remaining_subs(1, human=False) == 1

    # Idempotent if called again
    assert engine.state.grant_extra_time_substitution() is False
    assert engine.state.max_substitutions == 6

    # Friendly (unlimited subs) keeps max_substitutions=None
    friendly = MatchEngine(team_a, team_b, io=HeadlessMatchIO(), max_substitutions=None, auto=True)
    assert friendly.state.grant_extra_time_substitution() is True
    assert friendly.state.max_substitutions is None
    assert friendly.subs.remaining_subs(0, human=True) is None
    assert friendly.subs.remaining_subs(1, human=False) == 6

    # Actual tied knockout match entering extra time grants bonus sub (while skip_et does not)
    ko_rng = random.Random(31)
    ko = MatchEngine(
        team_a, team_b, io=HeadlessMatchIO(rng=ko_rng), knockout=True, max_substitutions=5, auto=True, rng=ko_rng
    )
    ko.state.total_turns = 0
    ko.state.halftime_turn = -1
    ko.play()
    assert ko.state.extra_time_sub_bonus == 1
    assert ko.state.max_substitutions == 6

    skip_rng = random.Random(32)
    skip_ko = MatchEngine(
        team_a,
        team_b,
        io=HeadlessMatchIO(rng=skip_rng),
        knockout=True,
        first_leg=(1, 1),
        away_goals_rule=False,
        no_extra_time=True,
        max_substitutions=5,
        auto=True,
        rng=skip_rng,
    )
    skip_ko.state.total_turns = 0
    skip_ko.state.halftime_turn = -1
    skip_ko.play()
    assert skip_ko.state.extra_time_sub_bonus == 0
    assert skip_ko.state.max_substitutions == 5






