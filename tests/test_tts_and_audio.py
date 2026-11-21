"""Tests for TTS backends (NVDA DLL, SAPI5 fallback, SpeechService), AudioEngine, and GameConfig."""

from __future__ import annotations

from pathlib import Path
import sys

from audiofutbol.engine.audio import AudioEngine, SOUND_ALIASES
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.base import NullSpeechBackend
from audiofutbol.engine.tts.nvda import NvdaDllBackend
from audiofutbol.engine.tts.sapi import Sapi5Backend
from audiofutbol.engine.tts.service import SpeechService


class _FakeUnavailableBackend:
    name = "FakeUnavailable"

    def is_available(self) -> bool:
        return False

    def speak(self, text: str, interrupt: bool = False) -> bool:
        return False

    def braille(self, text: str) -> bool:
        return False

    def cancel(self) -> None:
        pass


def test_nvda_dll_loads_from_lib_on_windows() -> None:
    paths = AppPaths.detect()
    nvda = NvdaDllBackend(paths.dll_search_dirs())
    if sys.platform == "win32":
        assert nvda.is_dll_loaded() is True
        assert nvda.dll_path is not None
        assert nvda.dll_path.is_file()
        assert nvda.error == ""


def test_sapi5_initializes_on_windows() -> None:
    sapi = Sapi5Backend()
    if sys.platform == "win32":
        assert sapi.is_available() is True
        assert sapi.error == ""


def test_speech_service_chain_history_and_listener() -> None:
    primary = _FakeUnavailableBackend()
    fallback = NullSpeechBackend()
    service = SpeechService([primary, fallback], braille_enabled=True)

    observed: list[tuple[str, bool]] = []
    service.add_listener(lambda msg, intr: observed.append((msg, intr)))

    assert service.active_backend_name() == fallback.name
    assert service.speak("Gol de Argentina", interrupt=False) is True
    assert service.last_message == "Gol de Argentina"
    assert fallback.spoken == [("Gol de Argentina", False)]
    assert fallback.brailled == ["Gol de Argentina"]
    assert observed == [("Gol de Argentina", False)]

    assert service.repeat_last() is True
    assert fallback.spoken[-1] == ("Gol de Argentina", True)

    service.silence()
    assert fallback.cancel_count == 1


def test_audio_engine_indexes_all_normalized_events() -> None:
    paths = AppPaths.detect()
    cfg = GameConfig()
    audio = AudioEngine(paths, cfg, init_mixer=False)
    assert len(audio.files) >= 30
    for event in SOUND_ALIASES:
        assert len(audio.candidates(event)) >= 1, f"Missing sound mapping for event: {event}"
    assert (paths.music_dir / "musica_menu.ogg").is_file()
    for theme in ("musica_versus", "musica_victoria", "musica_derrota", "musica_penales", "musica_fanfarria"):
        assert (paths.themes_dir / f"{theme}.ogg").is_file()


def test_config_roundtrip_and_clamping(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg = GameConfig(vol_musica=1.5, vol_efectos=-0.2, turno_seg=45, var=False, braille=False)
    assert cfg.save(cfg_file) is True

    loaded = GameConfig.load(cfg_file)
    assert loaded.vol_musica == 1.0
    assert loaded.vol_efectos == 0.0
    assert loaded.turno_seg == 60
    assert loaded.var is False
    assert loaded.braille is False
