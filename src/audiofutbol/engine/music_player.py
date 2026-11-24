"""Accessible music player with local tracks, direct URLs and YouTube search.

``yt-dlp`` is optional: when it is bundled in the portable build the player can
search YouTube and resolve a direct stream.  When it is missing, local tracks
and pasted URLs still work.  Streams are written to a temporary file with the
standard library, so no ffmpeg installation is required.
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .audio import AUDIO_EXTENSIONS, AudioEngine
from .paths import AppPaths

LOGGER = logging.getLogger("audiofutbol.music")
MAX_STREAM_BYTES = 80 * 1024 * 1024


@dataclass
class Track:
    title: str
    url: str = ""
    path: Optional[Path] = None
    source: str = "local"
    video_id: str = ""

    def label(self) -> str:
        origin = {
            "local": "archivo local",
            "youtube": "YouTube",
            "url": "URL directa",
        }.get(self.source, self.source)
        return f"{self.title}. Origen: {origin}."


class MusicPlayer:
    """Playlist controller built on top of :class:`AudioEngine`."""

    def __init__(self, paths: AppPaths, audio: AudioEngine,
                 speak: Optional[Callable[[str], None]] = None) -> None:
        self.paths = paths
        self.audio = audio
        self.speak = speak or (lambda _text: None)
        self.tracks: list[Track] = self.local_tracks()
        self.index = -1
        self.paused = False
        self._stream_dir = Path(tempfile.gettempdir()) / "audiofutbol-streams"
        self._stream_dir.mkdir(parents=True, exist_ok=True)
        self._current_path: Optional[Path] = None

    # -- discovery ---------------------------------------------------------
    def local_tracks(self) -> list[Track]:
        result: list[Track] = []
        for folder in (self.paths.music_dir, self.paths.themes_dir):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                    result.append(Track(path.stem.replace("_", " "), source="local", path=path))
        return result

    @staticmethod
    def ytdlp_available() -> bool:
        try:
            import yt_dlp  # noqa: F401
            return True
        except Exception:
            return False

    def search(self, query: str, limit: int = 8) -> tuple[list[Track], str]:
        """Return (results, note).  Never raises on network or yt-dlp errors."""
        query = query.strip()
        if not query:
            return [], "Escribe algo para buscar."
        if self.ytdlp_available():
            try:
                import yt_dlp

                options = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "extract_flat": "in_playlist",
                    "playlistend": limit,
                    "noplaylist": True,
                }
                with yt_dlp.YoutubeDL(options) as downloader:
                    info = downloader.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = list((info or {}).get("entries") or [])
                tracks = [
                    Track(
                        title=str(entry.get("title") or "Resultado de YouTube"),
                        url=str(entry.get("url") or ""),
                        source="youtube",
                        video_id=str(entry.get("id") or ""),
                    )
                    for entry in entries
                    if entry
                ]
                for track in tracks:
                    if track.url and not track.url.startswith("http"):
                        track.url = f"https://www.youtube.com/watch?v={track.url}"
                if tracks:
                    return tracks[:limit], f"{len(tracks)} resultados de YouTube para {query}."
            except Exception as exc:
                LOGGER.info("YouTube search failed: %s", exc)
                note = f"YouTube no respondió: {exc}. Se buscará en la música local."
        else:
            note = "yt-dlp no está disponible; se buscará en la música local."
        lowered = query.casefold()
        local = [track for track in self.local_tracks() if lowered in track.title.casefold()]
        return local[:limit], note + (f" {len(local)} resultados locales." if local else " Sin resultados.")

    # -- playback ----------------------------------------------------------
    def _stream_to_file(self, url: str) -> Path:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 AudioFutbol/0.18"}
        )
        parsed = urllib.parse.urlparse(url)
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            suffix = ".m4a"
        target = self._stream_dir / f"pista_{abs(hash(url))}{suffix}"
        if target.exists() and target.stat().st_size > 0:
            return target
        downloaded = 0
        with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_STREAM_BYTES:
                    break
                handle.write(chunk)
        if downloaded == 0:
            target.unlink(missing_ok=True)
            raise OSError("el servidor no devolvió datos de audio")
        return target

    def _resolve_youtube(self, url: str) -> tuple[str, str]:
        """Return (direct_url, title) using the bundled yt-dlp when available."""
        if not self.ytdlp_available():
            raise RuntimeError("yt-dlp no está incluido en esta instalación")
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "format": "bestaudio[ext=mp3]/best[ext=mp3]/bestaudio/best",
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
        if not info or not info.get("url"):
            raise RuntimeError("YouTube no devolvió una URL reproducible")
        return str(info["url"]), str(info.get("title") or url)

    def play(self, track: Track) -> tuple[bool, str]:
        """Resolve and play a track; returns (ok,spoken_message)."""
        try:
            if track.path is not None:
                path = track.path
            elif track.source == "youtube":
                direct, title = self._resolve_youtube(track.url)
                track.title = title or track.title
                path = self._stream_to_file(direct)
            else:
                path = self._stream_to_file(track.url)
            ok = self.audio.play_music_file(path)
            if not ok:
                return False, (
                    f"No se pudo reproducir {track.title}. "
                    "El formato puede no ser compatible con el reproductor."
                )
            if track.source != "local":
                self.tracks.append(track)
            self.index = len(self.tracks) - 1
            self._current_path = path
            self.paused = False
            return True, f"Reproduciendo {track.title}."
        except Exception as exc:
            LOGGER.info("play failed: %s", exc)
            return False, f"No se pudo reproducir: {exc}"

    def play_query(self, query: str) -> tuple[bool, str]:
        """Play a search result by its spoken number, or a pasted URL."""
        if query.startswith("http://") or query.startswith("https://"):
            return self.play(Track(query, url=query, source="url"))
        try:
            number = int(query.strip())
        except ValueError:
            return False, "Escribe el número de un resultado o pega una URL."
        if 1 <= number <= len(self.tracks):
            return self.play(self.tracks[number - 1])
        return False, f"No hay un resultado número {number}."

    def toggle_pause(self) -> str:
        if self.paused:
            self.audio.music_unpause()
            self.paused = False
            return "Música reanudada."
        if not self.audio.music_playing():
            return "No hay música reproduciéndose."
        self.audio.music_pause()
        self.paused = True
        return "Música en pausa."

    def forward(self, seconds: float = 10.0) -> str:
        self.audio.music_seek(self.audio.music_position() + seconds)
        return f"Avanzado {int(seconds)} segundos."

    def rewind(self, seconds: float = 10.0) -> str:
        self.audio.music_seek(max(0.0, self.audio.music_position() - seconds))
        return f"Retrocedido {int(seconds)} segundos."

    def set_volume(self, value: float) -> str:
        volume = max(0.0, min(1.0, float(value)))
        self.audio.config.vol_musica = volume
        self.audio.set_music_volume(volume)
        return f"Volumen de música: {round(volume * 100)} por ciento."

    def status(self) -> str:
        if self.index < 0 or self.index >= len(self.tracks):
            return "No hay ninguna pista cargada."
        track = self.tracks[self.index]
        state = "en pausa" if self.paused else "en reproducción" if self.audio.music_playing() else "detenida"
        return (
            f"{track.title}. Estado: {state}. "
            f"Posición: {int(self.audio.music_position())} segundos. "
            f"Volumen: {round(self.audio.config.vol_musica * 100)} por ciento."
        )
