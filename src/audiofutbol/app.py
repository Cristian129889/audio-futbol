"""Application composition root: wires engine, TTS, audio, data, and scene manager."""

from __future__ import annotations

import logging
from pathlib import Path
import traceback

from audiofutbol.data.competition_loader import StadiumDirectory
from audiofutbol.data.csv_loader import load_extra_national_teams, load_national_teams
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.service import create_default_speech_service
from audiofutbol.engine.updater import UpdateService
from audiofutbol.engine.window import GameWindow, QuitRequested
from audiofutbol.scenes.base import GameContext, SceneManager
from audiofutbol.scenes.main_menu import MainMenuScene
from audiofutbol.ui.widgets import AudioUI


def _configure_logging(paths: AppPaths) -> logging.Logger:
    logger = logging.getLogger("audiofutbol")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            paths.logs_dir / "audiofutbol.log", encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    return logger


class AudioFutbolApp:
    """Top-level application lifecycle manager (zero console dependency)."""

    def __init__(self, base_dir: Path | None = None, headless: bool = False) -> None:
        self.paths = AppPaths.detect(base_dir)
        self.logger = _configure_logging(self.paths)
        self.config = GameConfig.load(self.paths.config_file)
        self.speech = create_default_speech_service(
            self.paths.dll_search_dirs(),
            braille_enabled=self.config.braille,
        )
        self.window = GameWindow(self.speech, headless=headless)
        self.audio = AudioEngine(self.paths, self.config, init_mixer=not headless)
        self.ui = AudioUI(self.speech, self.audio, self.window)
        self.saves = SaveManager(self.paths)
        self.stadiums = StadiumDirectory(self.paths)
        self.updater = UpdateService(self.paths)
        self.updater.clean_stale_staging()

    def run(self) -> int:
        self.logger.info("Starting AudioFutbol from %s", self.paths.base_dir)
        self.logger.info("TTS status: %s", self.speech.nvda_status_summary())
        try:
            if not self.window.headless and self.config.buscar_actualizaciones_al_iniciar:
                self.updater.start_background_check()
            self.window.open()
            teams, from_csv = load_national_teams(self.paths)
            extra_teams = load_extra_national_teams(self.paths)
            ctx = GameContext(
                paths=self.paths,
                config=self.config,
                speech=self.speech,
                audio=self.audio,
                window=self.window,
                ui=self.ui,
                saves=self.saves,
                stadiums=self.stadiums,
                national_teams=teams,
                loaded_from_csv=from_csv,
                extra_national_teams=extra_teams,
                updater=self.updater,
            )
            manager = SceneManager(ctx, MainMenuScene())
            manager.run()
            return 0
        except (QuitRequested, SystemExit):
            return 0
        except Exception:
            trace = traceback.format_exc()
            self.logger.error("Unhandled exception:\n%s", trace)
            try:
                self.paths.error_log_file.write_text(trace, encoding="utf-8")
            except Exception:
                pass
            self.speech.speak(
                "Ha ocurrido un error. Se ha guardado el archivo error punto log en la carpeta del juego.",
                interrupt=True,
            )
            self.window.pause(3000)
            return 1
        finally:
            self.audio.stop_crowd_ambience()
            self.audio.stop_music(200)
            self.config.save(self.paths.config_file)
            self.window.close()


def run() -> None:
    app = AudioFutbolApp()
    raise SystemExit(app.run())
