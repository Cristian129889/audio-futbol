"""Unit and integration tests for the GitHub Releases self-updater and UI flow."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import urllib.error
import zipfile

import pygame
import pytest

from audiofutbol.data.competition_loader import StadiumDirectory
from audiofutbol.data.save_manager import SaveManager
from audiofutbol.engine.audio import AudioEngine
from audiofutbol.engine.config import GameConfig
from audiofutbol.engine.paths import AppPaths
from audiofutbol.engine.tts.base import NullSpeechBackend
from audiofutbol.engine.tts.service import SpeechService
from audiofutbol.engine.updater import (
    PORTABLE_ASSET_NAME,
    ReleaseInfo,
    UpdateService,
    build_windows_update_batch_script,
    format_changelog_for_speech,
    is_newer_version,
    parse_release_payload,
    parse_version_tuple,
    safe_extract_zip,
)
from audiofutbol.engine.window import GameWindow, QuitRequested
from audiofutbol.scenes.base import GameContext
from audiofutbol.scenes.updater_scene import (
    check_updates_on_startup_if_ready,
    present_update_dialog,
    run_manual_update_check,
)
from audiofutbol.ui.widgets import AudioUI


class _FakeHttpResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._stream = io.BytesIO(body)
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        pass


def _make_sample_release_zip(files: dict[str, bytes]) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files.items():
            zf.writestr(f"AudioFutbol_Portable/{rel_path}", content)
    raw = buf.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    return raw, sha


def _make_test_context(
    tmp_path: Path,
    updater: UpdateService,
    keys: list[tuple[int, str]],
) -> tuple[GameContext, NullSpeechBackend]:
    paths = AppPaths(tmp_path)
    config = GameConfig()
    backend = NullSpeechBackend()
    speech = SpeechService([backend])
    window = GameWindow(speech, headless=True)
    audio = AudioEngine(paths, config, init_mixer=False)
    ui = AudioUI(speech, audio, window)
    saves = SaveManager(paths)
    stadiums = StadiumDirectory(paths)

    key_iter = iter(keys)
    window.wait_key = lambda: next(key_iter)  # type: ignore[method-assign]

    ctx = GameContext(
        paths=paths,
        config=config,
        speech=speech,
        audio=audio,
        window=window,
        ui=ui,
        saves=saves,
        stadiums=stadiums,
        national_teams=[],
        loaded_from_csv=True,
        updater=updater,
    )
    return ctx, backend


def test_version_parsing_and_comparison() -> None:
    assert parse_version_tuple("v0.16.0") == (0, 16, 0)
    assert parse_version_tuple("0.15.2") == (0, 15, 2)
    assert parse_version_tuple("v1.0") == (1, 0, 0)
    assert is_newer_version("v0.16.0", "0.15.2") is True
    assert is_newer_version("0.16.1", "0.16.0") is True
    assert is_newer_version("v0.16.0", "0.16.0") is False
    assert is_newer_version("v0.15.2", "0.16.0") is False


def test_format_changelog_for_speech_strips_markdown_cleanly() -> None:
    md = (
        "## Novedades de AudioFútbol v0.16.0\n\n"
        "- **Premier League 2026-27**: 20 clubes con plantillas reales.\n"
        "- Corregido `ligamx_clubes.csv` y [COLABORAR.md](COLABORAR.md)\n"
        "- \\AudioFutbol_Portable.zip\\ listo!"
    )
    lines = format_changelog_for_speech(md, "0.16.0")
    assert lines[0] == "Novedades de AudioFútbol versión 0.16.0."
    assert any("Premier League 2026-27: 20 clubes con plantillas reales." in l for l in lines)
    assert any("Corregido ligamx_clubes.csv y COLABORAR.md." in l for l in lines)
    assert all("**" not in l and "`" not in l and "##" not in l for l in lines)


def test_parse_release_payload_extracts_asset_and_sha256() -> None:
    sha = "a" * 64
    payload = {
        "tag_name": "v0.17.0",
        "name": "AudioFútbol v0.17.0",
        "body": "- Nuevo actualizador integrado.",
        "html_url": "https://github.com/rayo-alcantar/audio-futbol/releases/tag/v0.17.0",
        "published_at": "2026-11-22T18:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": PORTABLE_ASSET_NAME,
                "browser_download_url": "https://example.com/AudioFutbol_Portable.zip",
                "size": 26214400,
                "digest": f"sha256:{sha}",
            }
        ],
    }
    res = parse_release_payload(payload, current_version="0.16.0")
    assert res.status == "update_available"
    assert res.release is not None
    assert res.release.version == "0.17.0"
    assert res.release.asset_sha256 == sha
    assert res.release.size_mb_text == "25.0 megabytes"

    same = parse_release_payload(payload, current_version="0.17.0")
    assert same.status == "up_to_date"


def test_safe_extract_zip_blocks_zip_slip_traversal(tmp_path: Path) -> None:
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../evil.txt", "pwned")

    with pytest.raises(ValueError, match="Ruta insegura"):
        safe_extract_zip(bad_zip, tmp_path / "out")


def test_download_verify_and_stage_preserves_user_saves_and_config(tmp_path: Path) -> None:
    zip_bytes, sha = _make_sample_release_zip(
        {
            "AudioFutbol.exe": b"NEW_EXE_BINARY",
            "README.md": b"# Updated README",
            "datos/TODAS_LAS_SELECCIONES.csv": b"equipo,numero,jugador,posicion,calidad\n",
            "config.json": b'{"vol_maestro": 0.99}',
            "sonidos.txt": b"gol=nuevo.wav",
            "saves/partida_mundial.json": b'{"overwritten": true}',
        }
    )

    # Pre-populate user install folder with custom config and save
    (tmp_path / "config.json").write_text('{"vol_maestro": 0.25}', encoding="utf-8")
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves" / "partida_mundial.json").write_text('{"my_save": 1}', encoding="utf-8")

    release = ReleaseInfo(
        tag_name="v0.17.0",
        version="0.17.0",
        title="AudioFútbol v0.17.0",
        body="- Novedad 1",
        html_url="https://github.com/rayo-alcantar/audio-futbol/releases/tag/v0.17.0",
        published_at="2026-11-22T18:00:00Z",
        asset_name=PORTABLE_ASSET_NAME,
        asset_url="https://example.com/AudioFutbol_Portable.zip",
        asset_size=len(zip_bytes),
        asset_sha256=sha,
    )

    updater = UpdateService(
        AppPaths(tmp_path),
        current_version="0.16.0",
        opener=lambda _req, timeout=30.0: _FakeHttpResponse(zip_bytes),
    )
    progress_calls: list[int] = []
    ok, msg, payload_dir = updater.download_and_stage_release(
        release, progress_callback=progress_calls.append
    )
    assert ok is True
    assert payload_dir is not None
    assert 100 in progress_calls

    # Protected user files must have been pruned from staging!
    assert not (payload_dir / "config.json").exists()
    assert not (payload_dir / "sonidos.txt").exists()
    assert not (payload_dir / "saves").exists()
    assert (payload_dir / "README.md").is_file()

    # Apply in non-frozen mode and verify user config & saves remain untouched
    applied, _, requires_exit = updater.apply_staged_update(payload_dir, frozen=False)
    assert applied is True
    assert requires_exit is False
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# Updated README"
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == '{"vol_maestro": 0.25}'
    assert (tmp_path / "saves" / "partida_mundial.json").read_text(encoding="utf-8") == '{"my_save": 1}'


def test_download_rejects_bad_sha256_checksum(tmp_path: Path) -> None:
    zip_bytes, _ = _make_sample_release_zip({"README.md": b"ok"})
    release = ReleaseInfo(
        tag_name="v0.17.0",
        version="0.17.0",
        title="AudioFútbol v0.17.0",
        body="- Novedad",
        html_url="",
        published_at="",
        asset_name=PORTABLE_ASSET_NAME,
        asset_url="https://example.com/AudioFutbol_Portable.zip",
        asset_size=len(zip_bytes),
        asset_sha256="f" * 64,  # Mismatched hash
    )
    updater = UpdateService(
        AppPaths(tmp_path),
        current_version="0.16.0",
        opener=lambda _req, timeout=30.0: _FakeHttpResponse(zip_bytes),
    )
    ok, msg, payload_dir = updater.download_and_stage_release(release)
    assert ok is False
    assert payload_dir is None
    assert "SHA-256" in msg


def test_windows_batch_script_generation(tmp_path: Path) -> None:
    script = build_windows_update_batch_script(
        staging_payload_dir=tmp_path / "_update_staging" / "extracted",
        install_dir=tmp_path,
        staging_root_dir=tmp_path / "_update_staging",
        exe_name="AudioFutbol.exe",
        pid=12345,
    )
    assert "WAITPID=12345" in script
    assert "robocopy" in script
    assert "AudioFutbol.exe" in script


def test_update_ui_flow_reads_changelog_before_deciding(tmp_path: Path) -> None:
    zip_bytes, sha = _make_sample_release_zip({"README.md": b"# v0.17.0"})
    payload = {
        "tag_name": "v0.17.0",
        "name": "AudioFútbol v0.17.0",
        "body": "## Cambios\n- Nuevo actualizador accesible con lector de novedades.",
        "html_url": "https://github.com/rayo-alcantar/audio-futbol/releases/tag/v0.17.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": PORTABLE_ASSET_NAME,
                "browser_download_url": "https://example.com/AudioFutbol_Portable.zip",
                "size": len(zip_bytes),
                "digest": f"sha256:{sha}",
            }
        ],
    }

    def _fake_opener(req: object, timeout: float = 10.0) -> _FakeHttpResponse:
        url = getattr(req, "full_url", "")
        if "api.github.com" in url:
            return _FakeHttpResponse(json.dumps(payload).encode("utf-8"))
        return _FakeHttpResponse(zip_bytes)

    updater = UpdateService(AppPaths(tmp_path), current_version="0.16.0", opener=_fake_opener)

    # Sequence of user keys:
    # 1. Enter on option 0 ("Leer la lista de cambios...") -> opens reader
    # 2. Down arrow in reader -> reads changelog line 2
    # 3. Escape -> exits reader back to update menu (focus automatically moves to option 1: "Actualizar ahora")
    # 4. Down arrow -> moves to option 2 ("Abrir la página...")
    # 5. Down arrow -> moves to option 3 ("Ahora no, volver a Opciones")
    # 6. Enter -> declines update cleanly
    keys = [
        (pygame.K_RETURN, "\r"),
        (pygame.K_DOWN, ""),
        (pygame.K_ESCAPE, ""),
        (pygame.K_DOWN, ""),
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
    ]
    ctx, backend = _make_test_context(tmp_path, updater, keys)
    run_manual_update_check(ctx)

    spoken_texts = [text for text, _ in backend.spoken]
    assert any("Nueva versión disponible: AudioFútbol 0.17.0" in t for t in spoken_texts)
    assert any("Lista de cambios: versión 0.17.0" in t for t in spoken_texts)
    assert any("Nuevo actualizador accesible con lector de novedades." in t for t in spoken_texts)


def test_startup_update_check_respects_skip_version(tmp_path: Path) -> None:
    payload = {
        "tag_name": "v0.17.0",
        "name": "AudioFútbol v0.17.0",
        "body": "- Mejoras generales.",
        "draft": False,
        "prerelease": False,
        "assets": [],
    }
    updater = UpdateService(
        AppPaths(tmp_path),
        current_version="0.16.0",
        opener=lambda _req, timeout=5.0: _FakeHttpResponse(json.dumps(payload).encode("utf-8")),
    )
    updater.start_background_check()

    # User chooses option 3 on startup dialog: "Omitir esta versión (0.17.0)..."
    keys = [
        (pygame.K_DOWN, ""),
        (pygame.K_DOWN, ""),
        (pygame.K_DOWN, ""),
        (pygame.K_RETURN, "\r"),
    ]
    ctx, backend = _make_test_context(tmp_path, updater, keys)
    check_updates_on_startup_if_ready(ctx)
    assert ctx.config.version_omitida == "0.17.0"
    assert any("Se omitirá el aviso automático" in t for t, _ in backend.spoken)
