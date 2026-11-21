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
