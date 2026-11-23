"""Tests for accessible UI widgets, headless GameWindow, and zero-console-dependency guarantee."""

from __future__ import annotations

import ast
from pathlib import Path

import pygame

from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.base import NullSpeechBackend
from audiofutbol.engine.tts.service import SpeechService
from audiofutbol.engine.window import GameWindow
from audiofutbol.ui.widgets import AudioUI


def test_no_print_or_input_calls_in_package() -> None:
    """Guarantee zero console stdout/stdin dependency across the entire package."""
    src_root = AppPaths.detect().base_dir / "src" / "audiofutbol"
    offenders: list[str] = []
    for py_file in src_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("print", "input"):
                    offenders.append(f"{py_file.name}:{node.lineno} calls {node.func.id}()")
    assert offenders == []


def test_audio_menu_navigation_and_selection() -> None:
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(AppPaths.detect(), GameConfig(), init_mixer=False)
    ui = AudioUI(speech, audio, window)

    keys = iter([(pygame.K_DOWN, ""), (pygame.K_RETURN, "\r")])
    window.wait_key = lambda: next(keys)  # type: ignore[method-assign]

    selected = ui.menu("Menú de prueba", ["Uno", "Dos", "Tres"])
    assert selected == 1
    assert any("Menú de prueba" in msg for msg, _ in backend.spoken)
    assert any("Dos, 2 de 3" in msg for msg, _ in backend.spoken)
    assert window.hud.selected_index == 1


def test_audio_reader_navigation_and_escape() -> None:
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(AppPaths.detect(), GameConfig(), init_mixer=False)
    ui = AudioUI(speech, audio, window)

    keys = iter([(pygame.K_DOWN, ""), (pygame.K_ESCAPE, "")])
    window.wait_key = lambda: next(keys)  # type: ignore[method-assign]

    ui.reader("Tabla", ["Linea 1", "Linea 2"])
    assert any("Linea 2, 2 de 2" in msg for msg, _ in backend.spoken)


def test_global_history_shortcuts_in_game_window() -> None:
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)

    speech.speak("Mensaje 1")
    speech.speak("Mensaje 2")
    speech.speak("Mensaje 3")

    ev_prev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_COMMA, mod=0, unicode=",")
    ev_next = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PERIOD, mod=0, unicode=".")
    ev_first = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_COMMA, mod=pygame.KMOD_SHIFT, unicode=";")
    ev_last = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_PERIOD, mod=pygame.KMOD_SHIFT, unicode=":")

    assert window._handle_global_keydown(ev_prev) is True
    assert backend.spoken[-1] == ("Mensaje 2", True)

    assert window._handle_global_keydown(ev_first) is True
    assert backend.spoken[-1] == ("Primer mensaje. Mensaje 1", True)

    assert window._handle_global_keydown(ev_next) is True
    assert backend.spoken[-1] == ("Mensaje 2", True)

    assert window._handle_global_keydown(ev_last) is True
    assert backend.spoken[-1] == ("Último mensaje. Mensaje 3", True)


def test_recap_space_skips_between_sections_and_always_opens_menu() -> None:
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(AppPaths.detect(), GameConfig(), init_mixer=False)
    ui = AudioUI(speech, audio, window)

    lines = [
        "Resultado final: Argentina 2, Francia 1",
        "Global: Argentina 3, Francia 2",
        "Minuto 14. Gol de Messi, de Argentina.",
        "Minuto 52. Poste de Mbappé, de Francia.",
        "Estadísticas del partido.",
        "Posesión del balón: Argentina 55 por ciento, Francia 45 por ciento.",
        "Tiros: Argentina 8, 5 a puerta. Francia 6, 3 a puerta.",
    ]

    # Wait sequence:
    # 1) intro -> None (let goals section start)
    # 2) first line of goals -> 'space' (jump to plays section)
    # 3) first line of plays -> 'space' (jump to stats section)
    # 4) first line of stats -> 'space' (finish stream and open menu)
    actions = iter([None, "space", "space", "space"])
    window.wait_or_skip_action = lambda _ms: next(actions, None)  # type: ignore[method-assign]
    window.wait_key = lambda: (pygame.K_RETURN, "\r")  # type: ignore[method-assign]

    ui.recap(lines)
    spoken_msgs = [msg for msg, _ in backend.spoken]

    # First line of each section was narrated, while skipped lines inside sections were omitted
    assert "Resultado final: Argentina 2, Francia 1" in spoken_msgs
    assert "Global: Argentina 3, Francia 2" not in spoken_msgs
    assert "Minuto 14. Gol de Messi, de Argentina." in spoken_msgs
    assert "Minuto 52. Poste de Mbappé, de Francia." not in spoken_msgs
    assert "Estadísticas del partido." in spoken_msgs
    assert "Posesión del balón: Argentina 55 por ciento, Francia 45 por ciento." not in spoken_msgs
    # Menu "Fin del recuento" is always presented
    assert any("Fin del recuento" in msg for msg in spoken_msgs)


def test_recap_escape_cancels_narration_but_still_shows_post_recap_menu() -> None:
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(AppPaths.detect(), GameConfig(), init_mixer=False)
    ui = AudioUI(speech, audio, window)

    lines = [
        "Resultado final: España 1, Italia 0",
        "Minuto 70. Gol de Morata, de España.",
        "Estadísticas del partido.",
        "Posesión del balón: España 60 por ciento, Italia 40 por ciento.",
    ]

    # Press Escape immediately on the first line
    actions = iter([None, "escape"])
    window.wait_or_skip_action = lambda _ms: next(actions, None)  # type: ignore[method-assign]
    window.wait_key = lambda: (pygame.K_RETURN, "\r")  # type: ignore[method-assign]

    ui.recap(lines)
    spoken_msgs = [msg for msg, _ in backend.spoken]

    assert "Recuento cancelado." in spoken_msgs
    assert "Minuto 70. Gol de Morata, de España." not in spoken_msgs
    assert any("Fin del recuento" in msg for msg in spoken_msgs)

