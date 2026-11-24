"""Pygame mixer audio engine: stereo-panned SFX, crowd ambience, music, and themes."""

from __future__ import annotations

import array
import io
import logging
import math
import os
from pathlib import Path
import random
import sys
from typing import Any

from audiofutbol.domain.text import normalize_text, tokenize
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths

AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".wav",
    ".ogg",
    ".mp3",
    ".flac",
    ".opus",
    ".aiff",
    ".aif",
    ".mod",
    ".xm",
    ".it",
    ".s3m",
)

SOUND_ALIASES: dict[str, list[str]] = {
    "silbato": ["silbato", "whistle", "pito", "pitido", "referee", "arbitro", "ref"],
    "silbato_descanso": ["descanso", "halftime"],
    "silbato_final": ["final", "fulltime"],
    "falta": ["falta", "foul"],
    "gol": ["gol", "goal", "scored", "score"],
    "ovacion": [
        "ovacion",
        "cheer",
        "cheers",
        "aplauso",
        "applause",
        "clap",
        "hinchada",
        "aficion",
        "crowd",
    ],
    "abucheo": ["abucheo", "boo", "boos", "jeer"],
    "balon": ["balon", "ball", "kick", "patada", "pase", "pass", "shot", "tiro"],
    "cabezazo": ["cabezazo", "header"],
    "barrera": ["barrera", "wall"],
    "barrida": ["barrida", "tackle", "slide"],
    "regate": ["regate", "dribble"],
    "chilena": ["chilena", "bicycle"],
    "choque": ["choque", "impact"],
    "parada": ["parada", "save", "catch", "atajada", "keeper"],
    "poste": ["poste", "post", "crossbar", "travesano"],
    "tarjeta": ["tarjeta", "card", "yellow"],
    "tarjeta_roja": ["roja", "red"],
    "var": ["var"],
    "var_confirmado": ["confirmado"],
    "var_anulado": ["anulado"],
    "cambio": ["cambio", "tablero", "sub"],
    "corner": ["corner", "esquina"],
    "penal": ["penal", "penalty"],
    "casi": ["casi", "gasp", "uy", "near", "ooh", "oh"],
    "tribuna": ["tribuna", "ambiente", "ambience", "stadium", "estadio", "atmosphere"],
    "sorteo": ["sorteo", "draw", "bola"],
    "mover": ["mover", "cursor", "move", "tick", "select"],
    "aceptar": ["aceptar", "decide", "confirm", "enter", "ok"],
    "volver": ["volver", "cancel", "back"],
}

SOUND_FALLBACKS: dict[str, str] = {
    "silbato_descanso": "silbato",
    "silbato_final": "silbato",
    "falta": "silbato",
    "penal": "silbato",
    "tarjeta": "silbato",
    "tarjeta_roja": "tarjeta",
    "var": "silbato",
    "var_confirmado": "var",
    "var_anulado": "var",
    "cambio": "aceptar",
    "corner": "balon",
    "cabezazo": "balon",
    "barrera": "balon",
    "barrida": "balon",
    "regate": "balon",
    "chilena": "balon",
    "choque": "balon",
    "poste": "balon",
}

MASTER_SFX_HEADROOM = 0.40
MASTER_MUSIC_HEADROOM = 0.34
MASTER_CROWD_HEADROOM = 0.20
VOLUME_CURVE_EXPONENT = 2.2

CROWD_EVENTS: frozenset[str] = frozenset({"tribuna"})
REACTION_EVENTS: frozenset[str] = frozenset({"ovacion", "abucheo", "casi"})


def perceptual_gain(level: float, master: float = 1.0, headroom: float = 1.0) -> float:
    """Map a 0..1 UI slider to perceptual amplitude using an exponential loudness curve.

    Human hearing is logarithmic: linear 10% (-20 dB) still sounds loud on headphones.
    A power curve (exponent 2.2) plus headroom makes 10% genuinely soft and 100% safe.
    """
    clamped = max(0.0, min(1.0, float(level)))
    clamped_master = max(0.0, min(1.0, float(master)))
    if clamped <= 0.0 or clamped_master <= 0.0:
        return 0.0
    return (clamped ** VOLUME_CURVE_EXPONENT) * (clamped_master ** 1.8) * headroom


def score_sound_tokens(tokens: list[str], aliases: list[str]) -> float:
    best = 0.0
    for token in tokens:
        for idx, alias in enumerate(aliases):
            if token == alias:
                best = max(best, max(5.0, 10.0 - idx * 0.5))
            elif len(alias) >= 4 and token.startswith(alias):
                best = max(best, 4.0 - idx * 0.1)
    return best


class AudioEngine:
    """Manages sound effects, crowd loop, background music, and themes."""

    def __init__(self, paths: AppPaths, config: GameConfig, init_mixer: bool = True) -> None:
        self.paths = paths
        self.config = config
        self.ok = False
        self._allow_mixer_init = init_mixer
        self._pygame: Any = None
        self.files: list[Path] = []
        self.assigned: dict[Path, str] = {}
        self.manual: dict[str, list[Path]] = {}
        self._cache: dict[Path, Any] = {}
        self._synth_cache: dict[tuple[Any, ...], Any] = {}
        self._crowd_channel: Any = None
        self._music_stream: io.BytesIO | None = None

        if init_mixer:
            self.ensure_mixer()

        self.index_sounds()

    def ensure_mixer(self, force_reinit: bool = False) -> bool:
        """Initialize (or reinitialize) pygame.mixer with Windows driver fallbacks."""
        self._allow_mixer_init = True
        logger = logging.getLogger("audiofutbol")
        try:
            if self._pygame is None:
                import pygame

                self._pygame = pygame
            pygame = self._pygame
            if not force_reinit and pygame.mixer.get_init():
                self.ok = True
                return True
            if force_reinit:
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
        except Exception as exc:
            self.ok = False
            logger.warning("Failed to import/prepare pygame mixer: %s", exc)
            return False

        had_env = "SDL_AUDIODRIVER" in os.environ
        orig_env = os.environ.get("SDL_AUDIODRIVER")
        drivers: list[str | None] = [None]
        if sys.platform == "win32":
            for drv in ("directsound", "winmm"):
                if drv != orig_env:
                    drivers.append(drv)

        last_exc: Exception | None = None
        for drv in drivers:
            if drv is not None:
                os.environ["SDL_AUDIODRIVER"] = drv
            for init_kwargs in (
                {"frequency": 44100, "size": -16, "channels": 2, "buffer": 2048},
                {},
            ):
                try:
                    pygame.mixer.init(**init_kwargs)
                    pygame.mixer.set_num_channels(32)
                    self.ok = True
                    logger.info("Audio mixer initialized: %s", pygame.mixer.get_init())
                    return True
                except Exception as exc:
                    last_exc = exc
                    try:
                        pygame.mixer.quit()
                    except Exception:
                        pass

        if had_env and orig_env is not None:
            os.environ["SDL_AUDIODRIVER"] = orig_env
        else:
            os.environ.pop("SDL_AUDIODRIVER", None)
        self.ok = False
        logger.warning("Failed to initialize audio mixer: %s", last_exc)
        return False

    def is_music_playing(self) -> bool:
        """Return True when mixer music is actively playing."""
        if not self.ok or self._pygame is None:
            return False
        try:
            return bool(self._pygame.mixer.music.get_busy())
        except Exception:
            return False

    def index_sounds(self) -> None:
        """Scan sounds directory and apply optional manual overrides from sonidos.txt."""
        self.files = []
        if self.paths.sounds_dir.is_dir():
            for root, _, filenames in os.walk(self.paths.sounds_dir):
                for name in sorted(filenames):
                    if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                        self.files.append(Path(root) / name)

        self.assigned = {}
        for filepath in self.files:
            tokens = tokenize(filepath.stem)
            best_event: str | None = None
            best_score = 0.0
            for event, aliases in SOUND_ALIASES.items():
                candidates = aliases + ([event] if event not in aliases else [])
                score = score_sound_tokens(tokens, candidates)
                if score > best_score:
                    best_event, best_score = event, score
            if best_event:
                self.assigned[filepath] = best_event

        self.manual = {}
        if self.paths.sound_map_file.is_file():
            try:
                with self.paths.sound_map_file.open("r", encoding="utf-8-sig") as fh:
                    for raw_line in fh:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        event_raw, _, target_raw = line.partition("=")
                        event = normalize_text(event_raw.strip())
                        target = normalize_text(target_raw.strip().strip('"'))
                        if target in ("ninguno", "ninguna", "nada"):
                            self.manual.setdefault(event, [])
                            continue
                        for filepath in self.files:
                            fname = normalize_text(filepath.name)
                            stem = normalize_text(filepath.stem)
                            if target in (fname, stem):
                                self.manual.setdefault(event, []).append(filepath)
            except Exception:
                pass

    def candidates(self, event: str) -> list[Path]:
        if event in self.manual:
            return self.manual[event]
        matched = [p for p, ev in self.assigned.items() if ev == event]
        if not matched and event in SOUND_FALLBACKS:
            return self.candidates(SOUND_FALLBACKS[event])
        return matched

    def _load_sound(self, filepath: Path) -> Any:
        if filepath not in self._cache:
            try:
                self._cache[filepath] = self._pygame.mixer.Sound(str(filepath))
            except Exception:
                self._cache[filepath] = None
        return self._cache[filepath]

    def play_sfx(
        self,
        event: str,
        pan: float | None = None,
        volume: float = 1.0,
        loop: bool = False,
    ) -> Any:
        """Play a sound effect mapped to `event` with optional stereo pan (-1..1)."""
        if not self.ok or self._pygame is None:
            return None
        paths = self.candidates(event)
        if not paths:
            return None
        sound = self._load_sound(random.choice(paths))
        if sound is None:
            return None
        try:
            channel = sound.play(loops=-1 if loop else 0)
            if channel is not None:
                if event in CROWD_EVENTS:
                    base_gain = perceptual_gain(
                        self.config.vol_ambiente,
                        master=self.config.vol_maestro,
                        headroom=MASTER_CROWD_HEADROOM,
                    )
                elif event == "gol":
                    base_gain = perceptual_gain(
                        self.config.vol_efectos,
                        master=self.config.vol_maestro,
                        headroom=MASTER_SFX_HEADROOM * 0.85,
                    )
                elif event in REACTION_EVENTS:
                    eff_gain = perceptual_gain(
                        self.config.vol_efectos,
                        master=self.config.vol_maestro,
                        headroom=MASTER_SFX_HEADROOM * 0.65,
                    )
                    crowd_gain = perceptual_gain(
                        self.config.vol_ambiente,
                        master=self.config.vol_maestro,
                        headroom=MASTER_CROWD_HEADROOM,
                    )
                    base_gain = max(eff_gain, crowd_gain)
                else:
                    base_gain = perceptual_gain(
                        self.config.vol_efectos,
                        master=self.config.vol_maestro,
                        headroom=MASTER_SFX_HEADROOM,
                    )
                vol = max(0.0, min(1.0, base_gain * max(0.0, float(volume))))
                left = vol if pan is None else vol * min(1.0, max(0.0, 1.0 - pan))
                right = vol if pan is None else vol * min(1.0, max(0.0, 1.0 + pan))
                channel.set_volume(left, right)
            return channel
        except Exception:
            return None

    def start_crowd_ambience(self, attendance_factor: float = 1.0) -> None:
        self.stop_crowd_ambience()
        if self.config.vol_ambiente <= 0 or self.config.vol_maestro <= 0:
            return
        if not self.ok or self._pygame is None:
            return
        paths = self.candidates("tribuna")
        if not paths:
            return
        sound = self._load_sound(random.choice(paths))
        if sound is None:
            return
        try:
            channel = sound.play(loops=-1)
            if channel is not None:
                gain = perceptual_gain(
                    self.config.vol_ambiente,
                    master=self.config.vol_maestro,
                    headroom=MASTER_CROWD_HEADROOM,
                )
                vol = max(0.0, min(1.0, gain * attendance_factor))
                channel.set_volume(vol, vol)
            self._crowd_channel = channel
        except Exception:
            self._crowd_channel = None

    def stop_crowd_ambience(self) -> None:
        if self._crowd_channel is not None:
            try:
                self._crowd_channel.stop()
            except Exception:
                pass
            self._crowd_channel = None

    def _start_music_file(self, track: Path, loops: int, volume: float) -> bool:
        """Load and play a music track, falling back to BytesIO and mixer reinit on failure."""
        if not self.ok and self._allow_mixer_init:
            self.ensure_mixer()
        if not self.ok or self._pygame is None:
            return False

        logger = logging.getLogger("audiofutbol")
        vol = max(0.0, min(1.0, float(volume)))

        def _try_load_and_play(use_bytes: bool) -> bool:
            music = self._pygame.mixer.music
            try:
                music.stop()
            except Exception:
                pass
            if hasattr(music, "unload"):
                try:
                    music.unload()
                except Exception:
                    pass
            try:
                if use_bytes:
                    buf = io.BytesIO(track.read_bytes())
                    self._music_stream = buf
                    namehint = track.suffix.lstrip(".").lower()
                    try:
                        music.load(buf, namehint=namehint)
                    except TypeError:
                        music.load(buf)
                else:
                    self._music_stream = None
                    music.load(str(track))
                music.set_volume(vol)
                music.play(loops)
                music.set_volume(vol)
                return bool(music.get_busy())
            except Exception as exc:
                logger.warning(
                    "Music load/play attempt (%s) failed for %s: %s",
                    "bytes" if use_bytes else "path",
                    track,
                    exc,
                )
                return False

        if _try_load_and_play(use_bytes=False):
            return True
        if _try_load_and_play(use_bytes=True):
            return True
        if self._allow_mixer_init:
            logger.warning("Retrying music playback for %s after mixer reinit", track)
            if self.ensure_mixer(force_reinit=True) and _try_load_and_play(use_bytes=True):
                return True
        logger.warning("Unable to play music track %s", track)
        return False

    def play_menu_music(self) -> None:
        if not self.config.musica:
            return
        if not self.ok and self._allow_mixer_init:
            self.ensure_mixer()
        if not self.ok or self._pygame is None:
            return
        tracks: list[Path] = []
        if self.paths.music_dir.is_dir():
            for root, _, filenames in os.walk(self.paths.music_dir):
                for name in filenames:
                    if Path(name).suffix.lower() in AUDIO_EXTENSIONS:
                        tracks.append(Path(root) / name)
        if not tracks:
            keywords = ("menu", "musica", "cancion", "bgm", "theme", "title", "intro")
            for filepath in self.files:
                if any(tok in keywords for tok in tokenize(filepath.name)):
                    tracks.append(filepath)
        if tracks:
            gain = perceptual_gain(
                self.config.vol_musica,
                master=self.config.vol_maestro,
                headroom=MASTER_MUSIC_HEADROOM,
            )
            self._start_music_file(random.choice(tracks), loops=-1, volume=gain)

    def stop_music(self, fade_ms: int = 600) -> None:
        if not self.ok or self._pygame is None:
            return
        try:
            self._pygame.mixer.music.fadeout(fade_ms)
        except Exception:
            pass

    def set_music_volume(self, volume: float) -> None:
        if not self.ok or self._pygame is None:
            return
        try:
            gain = perceptual_gain(
                volume,
                master=self.config.vol_maestro,
                headroom=MASTER_MUSIC_HEADROOM,
            )
            self._pygame.mixer.music.set_volume(max(0.0, min(1.0, gain)))
        except Exception:
            pass

    def play_theme(self, name: str, loop: bool = False, volume: float = 1.0) -> None:
        if not self.config.musica:
            return
        if not self.ok and self._allow_mixer_init:
            self.ensure_mixer()
        if not self.ok or self._pygame is None:
            return
        for ext in AUDIO_EXTENSIONS:
            candidate = self.paths.themes_dir / f"{name}{ext}"
            if candidate.is_file():
                gain = perceptual_gain(
                    self.config.vol_musica,
                    master=self.config.vol_maestro,
                    headroom=MASTER_MUSIC_HEADROOM,
                )
                vol = max(0.0, min(1.0, gain * max(0.0, float(volume))))
                self._start_music_file(candidate, loops=-1 if loop else 0, volume=vol)
                return

    @staticmethod
    def _equal_power_pan(gain: float, pan: float | None) -> tuple[float, float]:
        """Compute equal-power left/right channel amplitudes for pan in [-1.0, +1.0]."""
        if pan is None:
            return gain, gain
        clamped = max(-1.0, min(1.0, float(pan)))
        angle = (clamped + 1.0) * (math.pi / 4.0)
        return gain * math.cos(angle), gain * math.sin(angle)

    def _mixer_format(self) -> tuple[int, int]:
        try:
            init = self._pygame.mixer.get_init()
            if isinstance(init, tuple) and len(init) >= 3:
                return int(init[0]), int(init[2])
        except Exception:
            pass
        return 44100, 2

    def _build_tone_sound(
        self,
        freq_hz: float,
        duration_ms: int,
        is_chime: bool,
        append_edge_hz: float | None = None,
    ) -> Any:
        """Synthesize and cache a smooth 16-bit PCM tone for spatial formation navigation."""
        rate, channels = self._mixer_format()
        channels = 2 if channels >= 2 else 1
        key = (round(freq_hz, 1), duration_ms, is_chime, append_edge_hz, rate, channels)
        if key in self._synth_cache:
            return self._synth_cache[key]

        total_ms = duration_ms + (95 if append_edge_hz else 0)
        n_samples = max(1, int(rate * total_ms / 1000))
        main_samples = max(1, int(rate * duration_ms / 1000))
        edge_start = max(0, int(rate * 85 / 1000))
        edge_len = max(1, n_samples - edge_start)
        samples = array.array("h", [0] * (n_samples * channels))
        peak = 26000.0
        attack_n = max(1, int(rate * (0.004 if is_chime else 0.010)))
        tail_n = max(1, int(rate * (0.008 if is_chime else 0.025)))

        for i in range(n_samples):
            val = 0.0
            if i < main_samples:
                t = i / float(rate)
                if is_chime:
                    wave = (
                        0.78 * math.sin(2.0 * math.pi * freq_hz * t)
                        + 0.17 * math.sin(2.0 * math.pi * 2.0 * freq_hz * t)
                        + 0.05 * math.sin(2.0 * math.pi * 3.0 * freq_hz * t)
                    )
                    env = math.exp(-t / 0.038)
                else:
                    wave = (
                        0.88 * math.sin(2.0 * math.pi * freq_hz * t)
                        + 0.12 * math.sin(2.0 * math.pi * 2.0 * freq_hz * t)
                    )
                    env = 0.72
                if i < attack_n:
                    env *= i / float(attack_n)
                elif i >= main_samples - tail_n:
                    env *= max(0.0, (main_samples - i) / float(tail_n))
                val += wave * env

            if append_edge_hz and i >= edge_start:
                j = i - edge_start
                t2 = j / float(rate)
                wave2 = (
                    0.88 * math.sin(2.0 * math.pi * append_edge_hz * t2)
                    + 0.12 * math.sin(2.0 * math.pi * 2.0 * append_edge_hz * t2)
                )
                env2 = 0.68
                e_att = max(1, int(rate * 0.010))
                e_tail = max(1, int(rate * 0.025))
                if j < e_att:
                    env2 *= j / float(e_att)
                elif j >= edge_len - e_tail:
                    env2 *= max(0.0, (edge_len - j) / float(e_tail))
                val += wave2 * env2

            amp = max(-32767, min(32767, int(round(max(-1.0, min(1.0, val)) * peak))))
            if channels == 2:
                samples[2 * i] = amp
                samples[2 * i + 1] = amp
            else:
                samples[i] = amp

        try:
            sound = self._pygame.mixer.Sound(buffer=samples.tobytes())
        except Exception:
            sound = None
        self._synth_cache[key] = sound
        return sound

    def play_board_ting(
        self, pan: float = 0.0, pitch_ratio: float = 0.0, is_line_end: bool = False
    ) -> Any:
        """Play a spatial stereo chime (`ting`) mapped by row depth and horizontal pan."""
        if not self.ok or self._pygame is None:
            return None
        ratio = max(0.0, min(1.0, float(pitch_ratio)))
        freq_hz = round(880.0 * ((493.88 / 880.0) ** ratio))
        sound = self._build_tone_sound(
            freq_hz=freq_hz,
            duration_ms=115,
            is_chime=True,
            append_edge_hz=220.0 if is_line_end else None,
        )
        if sound is None:
            return None
        try:
            channel = sound.play()
            if channel is not None:
                gain = perceptual_gain(
                    self.config.vol_efectos,
                    master=self.config.vol_maestro,
                    headroom=MASTER_SFX_HEADROOM,
                )
                left, right = self._equal_power_pan(min(1.0, max(0.0, gain)), pan)
                channel.set_volume(left, right)
            return channel
        except Exception:
            return None

    def play_board_edge(self, pan: float = 0.0) -> Any:
        """Play a soft, non-intrusive low tone indicating the boundary of a formation line."""
        if not self.ok or self._pygame is None:
            return None
        sound = self._build_tone_sound(freq_hz=220.0, duration_ms=110, is_chime=False)
        if sound is None:
            return None
        try:
            channel = sound.play()
            if channel is not None:
                gain = perceptual_gain(
                    self.config.vol_efectos,
                    master=self.config.vol_maestro,
                    headroom=MASTER_SFX_HEADROOM,
                )
                left, right = self._equal_power_pan(min(1.0, max(0.0, gain)), pan)
                channel.set_volume(left, right)
            return channel
        except Exception:
            return None
