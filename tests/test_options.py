"""Tests for the categorized Options menu and config persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pygame

from audiofutbol.data.competition_loader import StadiumDirectory
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.base import NullSpeechBackend
from audiofutbol.engine.tts.service import SpeechService
from audiofutbol.engine.window import GameWindow
from audiofutbol.scenes.base import GameContext
from audiofutbol.scenes.options import run_options_menu
from audiofutbol.ui.rules_text import REGLAS
from audiofutbol.ui.widgets import AudioUI


def _make_options_context(
    tmp_path: Path,
    keys: list[tuple[int, str]],
    config: GameConfig | None = None,
) -> tuple[GameContext, NullSpeechBackend, list[tuple[str, list[str]]]]:
    paths = AppPaths(tmp_path)
    cfg = config or GameConfig()
    backend = NullSpeechBackend()
    speech = SpeechService([backend], braille_enabled=cfg.braille)
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(paths, cfg, init_mixer=False)
    ui = AudioUI(speech, audio, window)
    saves = SaveManager(paths)
    stadiums = StadiumDirectory(paths)

    key_iter = iter(keys)
    window.wait_key = lambda: next(key_iter)  # type: ignore[method-assign]

    presented_menus: list[tuple[str, list[str]]] = []
    orig_menu = ui.menu

    def _spy_menu(title: str, options: list[str], **kwargs):  # type: ignore[no-untyped-def]
        presented_menus.append((title, list(options)))
        return orig_menu(title, options, **kwargs)

    ui.menu = _spy_menu  # type: ignore[method-assign]

    ctx = GameContext(
        paths=paths,
        config=cfg,
        speech=speech,
        audio=audio,
        window=window,
        ui=ui,
        saves=saves,
        stadiums=stadiums,
        national_teams=[],
        loaded_from_csv=True,
    )
    return ctx, backend, presented_menus


def test_options_top_menu_categories_and_no_legacy_debug_entries(tmp_path: Path) -> None:
    keys = [(pygame.K_ESCAPE, "")]
    ctx, _backend, menus = _make_options_context(tmp_path, keys)

    run_options_menu(ctx)

    assert len(menus) == 1
    title, items = menus[0]
    assert title == "Opciones"
    assert items == [
        "Audio",
        "Partido y reglas",
        "Voz y accesibilidad",
        "Actualizaciones",
        "Leer las reglas del juego",
        "Volver",
    ]
    joined = " | ".join(items)
    assert "Revisar qué sonidos" not in joined
    assert "Crear lista de archivos" not in joined


def test_options_audio_submenu_adjusts_volumes_toggles_music_and_avoids_double_attenuation(
    tmp_path: Path,
) -> None:
    keys = [
        # Enter "Audio" (index 0)
        (pygame.K_RETURN, "\r"),
        # Slider 0: Volumen maestro -> Left (-0.05), then Enter (speaks slider hint)
        (pygame.K_LEFT, ""),
        (pygame.K_RETURN, "\r"),
        # Item 1: Música -> Down, Enter (toggles musica False)
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Item 2: Volumen de música -> Down, Right (+0.05)
        (pygame.K_DOWN, ""),
        (pygame.K_RIGHT, ""),
        # Item 3: Volumen de efectos -> Down, Right (+0.05)
        (pygame.K_DOWN, ""),
        (pygame.K_RIGHT, ""),
        # Item 4: Volumen del ambiente de tribuna -> Down, Right (+0.05)
        (pygame.K_DOWN, ""),
        (pygame.K_RIGHT, ""),
        # Exit Audio submenu & top-level Options
        (pygame.K_ESCAPE, ""),
        (pygame.K_ESCAPE, ""),
    ]
    cfg = GameConfig(
        vol_maestro=0.65, vol_musica=0.35, vol_efectos=0.50, vol_ambiente=0.05, musica=True
    )
    ctx, backend, menus = _make_options_context(tmp_path, keys, config=cfg)
    play_sfx_spy = MagicMock()
    ctx.audio.play_sfx = play_sfx_spy  # type: ignore[method-assign]

    run_options_menu(ctx)

    audio_title, audio_items = menus[1]
    assert "Audio" in audio_title
    assert audio_items[1] == "Música: activada"
    assert not any("Música del menú" in item for item in audio_items)

    assert ctx.config.vol_maestro == 0.60
    assert ctx.config.musica is False
    assert ctx.config.vol_musica == 0.40
    assert ctx.config.vol_efectos == 0.55
    assert ctx.config.vol_ambiente == 0.10

    assert any(
        "Usa flecha izquierda o derecha para ajustar este volumen." in msg
        for msg, _ in backend.spoken
    )
    # Verify tribuna preview was called without extra volume= override (no double attenuation)
    tribuna_calls = [call for call in play_sfx_spy.call_args_list if call.args == ("tribuna",)]
    assert len(tribuna_calls) == 1
    assert tribuna_calls[0].kwargs == {}

    # Persisted to config.json on exit
    reloaded = GameConfig.load(ctx.paths.config_file)
    assert reloaded.vol_maestro == 0.60
    assert reloaded.musica is False
    assert reloaded.vol_ambiente == 0.10


def test_options_match_submenu_cycles_pace_and_toggles_rules(tmp_path: Path) -> None:
    keys = [
        # Down -> "Partido y reglas", Enter
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Index 0: Duración de cada jugada (60 -> 30 -> 120)
        (pygame.K_RETURN, "\r"),
        (pygame.K_RIGHT, ""),
        # Index 1: VAR -> Down, Enter (True -> False)
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Index 2: Aviso de tiros -> Down, Enter (True -> False)
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Index 3: Gol de visitante -> Down, Enter (True -> False)
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Exit submenu & top-level
        (pygame.K_ESCAPE, ""),
        (pygame.K_ESCAPE, ""),
    ]
    ctx, _backend, _menus = _make_options_context(tmp_path, keys)
    run_options_menu(ctx)

    assert ctx.config.turno_seg == 120
    assert ctx.config.var is False
    assert ctx.config.aviso_tiros is False
    assert ctx.config.gol_visitante is False

    reloaded = GameConfig.load(ctx.paths.config_file)
    assert reloaded.turno_seg == 120
    assert reloaded.var is False
    assert reloaded.aviso_tiros is False
    assert reloaded.gol_visitante is False


def test_options_accessibility_updates_and_rules_reader(tmp_path: Path) -> None:
    keys = [
        # Down x2 -> "Voz y accesibilidad", Enter
        (pygame.K_DOWN, ""),
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Toggle braille (True -> False)
        (pygame.K_RETURN, "\r"),
        # Down -> "Probar voz y estado de NVDA", Enter
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Escape back to top-level (cursor stays at index 2)
        (pygame.K_ESCAPE, ""),
        # Down -> "Actualizaciones", Enter
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Down -> "Buscar actualizaciones al iniciar", Enter (True -> False)
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Escape back to top-level (cursor stays at index 3)
        (pygame.K_ESCAPE, ""),
        # Down -> "Leer las reglas del juego", Enter
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
        # Escape from reader
        (pygame.K_ESCAPE, ""),
        # Escape from top-level Options
        (pygame.K_ESCAPE, ""),
    ]
    ctx, backend, _menus = _make_options_context(tmp_path, keys)
    run_options_menu(ctx)

    assert ctx.config.braille is False
    assert ctx.speech.braille_enabled is False
    assert ctx.config.buscar_actualizaciones_al_iniciar is False

    spoken = [msg for msg, _ in backend.spoken]
    assert any("Motor de voz activo:" in msg or "DLL de NVDA" in msg for msg in spoken)
    assert any(REGLAS[0] in msg for msg in spoken)
